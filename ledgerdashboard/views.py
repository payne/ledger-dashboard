import datetime
from ledgerdashboard import app
from ledgerdashboard.ledger import ledger
from ledgerdashboard.renderer import LayoutRenderer
from ledgerdashboard.layout import Dashboard, Expenses
from ledgerdashboard.settings import LEDGER_FILE
import ledgerdashboard.settings as s
from flask import flash, request
from pprint import pprint

months = ["december", "january", "february", "march", "april", "may", "june",
          "july", "august", "september", "october", "november", "december"]

renderer = LayoutRenderer()
app.secret_key = "%45bkjefkserhjvnjdlkf5$4$5j  k"

l = ledger.Ledger.new(filename=LEDGER_FILE)
ledger_writer = ledger.LedgerWriter(LEDGER_FILE)


@app.route("/")
def index():

    layout = Dashboard()

    layout.accounts = [
        {"name": format_account(account), 'balance': format_amount(balance)}
        for account, cur, balance in l.balance(accounts=s.ASSET_ACCOUNTS)
    ]

    layout.debts = [
        {"name": format_account(account), 'balance': format_amount(float(balance) * -1)}
        for account, cur, balance in l.balance(accounts=s.LIABILITIES)
    ]

    layout.expense_balances = [
        {"name": format_account(account), 'balance': format_amount(balance), "first": account == s.EXPENSE_ACCOUNTS}
        for account, cur, balance
        in l.balance(accounts=s.EXPENSE_ACCOUNTS, limit="date >= [this month]")
    ]

    layout.expenses_previous_month = [
        {"name": format_account(account), 'balance': format_amount(balance), "first": ":" not in account}
        for account, cur, balance
        in l.balance(accounts=s.EXPENSE_ACCOUNTS, limit="date >= [last month] and date < [this month]")
    ]

    recurring_income = ledger.regular_transactions(
        l.register(accounts=s.INCOME_ACCOUNTS)
    )

    layout.income = [
        {"name": format_account(txn['payee']), 'balance': format_amount(float(txn['amount']) * -1)}
        for txn
        in recurring_income
    ]

    layout.last_expenses = [
        {'payee': txn['payee'], 'note': txn['note'], 'amount': format_amount(txn['amount'])}
        for txn
        in l.register(accounts=s.EXPENSE_ACCOUNTS)[:-15:-1]
    ]

    recurring_transactions = ledger.regular_transactions(l.register(accounts=s.EXPENSE_ACCOUNTS))
    transactions_this_month = l.register(accounts=s.EXPENSE_ACCOUNTS, limit='date >= [this month]')

    for txn in recurring_transactions:
        txn['date'] = datetime.datetime.strptime(txn['date'], '%Y/%m/%d')

    unpayed_transactions = get_unmatched_txns(recurring_transactions, transactions_this_month)

    layout.recurring_transactions = [
        {
            "payee": txn['payee'],
            "due_in": days_until_next_transaction(txn['date']),
            "amount": format_amount(txn['amount'])
        } for txn
        in sorted(unpayed_transactions, key=lambda txn: txn['date'].day)
    ]

    total = float(0)
    for txn in unpayed_transactions:
        total += float(txn['amount'])

    layout.recurring_transactions_total = format_amount(total)

    today = datetime.date.today()
    current_month = today.month

    flow = []
    for i in range(current_month - 3, current_month + 1):
        start_year = end_year = today.year
        start_month_nr = i % 12
        end_month_nr = (i+1) % 12

        if i < 1:
            start_year -= 1
        if i > 11:
            end_year += 1

        # print(start_month_nr, start_year, end_month_nr, end_year)
        result = l.register(
            accounts=" ".join([s.EXPENSE_ACCOUNTS, s.INCOME_ACCOUNTS]),
            M=True, collapse=True,
            limit="date >= [{} {}] and date < [{} {}]".format(
                months[start_month_nr], start_year,
                months[end_month_nr], end_year)
        )

        amount = float(result[0]['amount']) * -1 if len(result) > 0 else 0
        flow.append({
            'month': months[start_month_nr],
            'amount': format_amount(amount),
            'type': "negative" if amount < 0 else "positive"
        })

    layout.cash_flow = flow

    return renderer.render(layout)


@app.route("/expenses", methods=['GET'])
def expenses_get():
    return renderer.render(Expenses())


@app.route("/expenses", methods=['POST'])
def expenses_post():
    for field in ['payee', 'account', 'amount']:
        if field not in request.form or not request.form.get(field):
            flash("Field {} not set".format(field), 'error')
            return renderer.render(Expenses(request.form))

    posting = {
        "date": request.form.get('date'),
        "payee": request.form.get('payee', ""),
        "account": request.form.get('account', ""),
        "use_source": request.form.get('use_source', "") == "on",
        "source_account": request.form.get('source_account', ""),
        "amount": request.form.get('amount', 0),
        "description": request.form.get('description', "")
    }

    pprint(posting)

    ledger_writer.write_expense(posting)

    flash("Expense successfully added")
    return "See other", 303, {"Location": "/expenses"}


@app.route("/api/accounts/")
@app.route("/api/accounts/<account_filter>")
def api_accounts(account_filter=""):
    import json
    term = request.args.get("term", "")
    accounts = json.dumps([
        l.make_aliased(account)
        for account in l.accounts(account_filter)
        if term.lower() in account.lower()
    ])
    return accounts, 200, {"Content-Type": "application/json"}


@app.route("/api/payee/")
def api_payee():
    import json
    term = request.args.get("term", "")
    payees = {txn['payee'] for txn in l.register() if term.lower() in txn['payee'].lower()}
    return json.dumps(sorted(payees)), 200, {"Content-Type": "application/json"}


def format_amount(amount):
    return "€{: >8.2f}".format(float(amount))


def format_account(account):
    if ":" not in account:
        return account

    return ("&nbsp;" * 4) + ":".join(account.split(":")[1:])


def days_until_next_transaction(txn_date: datetime.date):
    """
    Calculates the number of days until the next transaction (occurring next month)
    :param txn_date: datetime.date
    :return: int
    """
    future_transaction = datetime.date(txn_date.year, txn_date.month % 12 + 1, txn_date.day)
    return (future_transaction - datetime.date.today()).days


def get_unmatched_txns(haystack, needles):
    """
    Tries to find the transactions in needles that don't occur in the haystack
    :param haystack:list[dict]
    :param needles:list[dict]
    :return:
    """
    unmatched_txns = []

    for txn in haystack:
        found = False
        for txn_tm in needles:
            if txn['payee'] == txn_tm['payee'] and txn['amount'] == txn_tm['amount']:
                found = True
                break
        if not found:
            unmatched_txns.append(txn)

    return unmatched_txns
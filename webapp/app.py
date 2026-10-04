import requests, time, os, random, json
from datetime import datetime, timezone
from flask import Flask, render_template, request, redirect, session, jsonify

from trade_db import (
    create_order_record,
    ensure_database,
    get_trackable_orders,
    get_order_history,
    update_order_record,
    update_order_record_by_ibkr_id,
    upsert_trade,
)
from portfolio_db import (
    ensure_portfolio_database,
    upsert_account_metadata,
    record_portfolio_performance,
    record_holdings_snapshot,
    get_daily_performance_since,
)
from urllib3.exceptions import InsecureRequestWarning

# disable warnings until you install a certificate
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

BASE_API_URL = "https://localhost:5055/v1/api"
ACCOUNT_ID = os.environ['IBKR_ACCOUNT_ID']
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]

os.environ['PYTHONHTTPSVERIFY'] = '0'

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))
database_connection, database_cursor = ensure_database()
database_cursor.close()
database_connection.close()

portfolio_db_connection, portfolio_db_cursor = ensure_portfolio_database()
portfolio_db_cursor.close()
portfolio_db_connection.close()

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)



def log_webhook_traffic(payload, raw_body=""):
    """Append every incoming webhook request to a daily JSON Lines file for traffic verification."""
    received_at = datetime.now(timezone.utc)
    json_path = os.path.join(DATA_DIR, f"webhook_traffic_{received_at:%Y%m%d}.jsonl")

    record = {
        "received_at": received_at.isoformat(),
        "payload": payload if isinstance(payload, dict) else None,
        "raw_body": raw_body if not isinstance(payload, dict) else "",
    }

    with open(json_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

@app.template_filter('ctime')
def timectime(s):
    return time.ctime(s/1000)


def get_active_account_id():
    return session.get('account_id', ACCOUNT_ID)


def confirm_ibkr_warnings(response_json):
    for _ in range(10):
        if not isinstance(response_json, list):
            return response_json

        confirmation = next(
            (
                item for item in response_json
                if item.get('id') and 'Yes' in item.get('messageOptions', [])
            ),
            None
        )
        if confirmation is None:
            return response_json

        reply_id = confirmation['id']
        print(f"Automatically confirming IBKR warning {reply_id}: {confirmation.get('message')}")
        r = requests.post(
            f"{BASE_API_URL}/iserver/reply/{reply_id}",
            json={"confirmed": True},
            verify=False
        )
        print(f"IBKR confirmation response status: {r.status_code}")
        print(f"IBKR confirmation response: {r.text}")
        r.raise_for_status()
        response_json = r.json()

    raise RuntimeError("IBKR returned too many consecutive order confirmation prompts")


def first_order_response(response_json):
    if isinstance(response_json, list) and response_json:
        return response_json[0]
    if isinstance(response_json, dict):
        return response_json
    return {}


def record_submitted_order(account_id, submitted_order, response_json, order_record_id, http_status=None):
    update_order_record(
        order_record_id,
        response_json=response_json,
        http_status=http_status,
    )
    ibkr_response = first_order_response(response_json)
    order_id = (
        ibkr_response.get('order_id')
        or ibkr_response.get('orderId')
        or ibkr_response.get('id')
    )
    if not order_id:
        print("IBKR did not return an order identifier; order remains tracked locally")
        return

    ticker = request.form.get('ticker', '')
    quantity = submitted_order['quantity']
    action = submitted_order['side']
    order_type = submitted_order['orderType']
    price = submitted_order.get('price', 0)
    status = ibkr_response.get('order_status') or ibkr_response.get('status') or 'PendingSubmit'
    order_description = ibkr_response.get('order_description') or f"{action} {quantity} {ticker}"

    upsert_trade(
        account_id=account_id,
        order_id=order_id,
        ticker=ticker,
        description=request.form.get('description', ''),
        company=request.form.get('company', ''),
        order_description=order_description,
        order_type=order_type,
        status=status,
        action=action,
        quantity=quantity,
        price=price,
    )


def record_live_orders(account_id, orders):
    for order in orders:
        order_id = order.get('orderId') or order.get('order_id')
        if not order_id:
            continue

        update_order_record_by_ibkr_id(account_id, order_id, order)

        upsert_trade(
            account_id=account_id,
            order_id=order_id,
            ticker=order.get('ticker', ''),
            description=order.get('description1', ''),
            company=order.get('companyName', ''),
            order_description=order.get('orderDesc', ''),
            order_type=order.get('orderType', ''),
            status=order.get('status', ''),
            action=order.get('side', ''),
            quantity=order.get('totalSize') or order.get('quantity') or order.get('size') or 0,
            price=order.get('avgPrice') or order.get('price') or order.get('limit_price') or 0,
        )


def fetch_subaccounts():
    try:
        r = requests.get(f"{BASE_API_URL}/portfolio/subaccounts", verify=False)
        return r.json()
    except Exception:
        return []


def fetch_commissions_by_conid(account_id):
    """Sum commissions from executed trades, grouped by conid, for the P&L book."""
    totals = {}
    try:
        r = requests.get(f"{BASE_API_URL}/iserver/account/trades", verify=False)
        trades = r.json() if r.content else []
    except Exception:
        return totals

    if not isinstance(trades, list):
        return totals

    for trade in trades:
        if not isinstance(trade, dict):
            continue
        if account_id and trade.get("account") not in (None, account_id):
            continue
        conid = trade.get("conid")
        try:
            commission = float(trade.get("commission", 0) or 0)
        except (TypeError, ValueError):
            commission = 0.0
        totals[conid] = totals.get(conid, 0.0) + commission

    return totals


def _summary_amount(summary, key):
    try:
        return float(summary.get(key, {}).get("amount", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def capture_portfolio_snapshots(accounts):
    """Record a metadata / performance / holdings snapshot for every managed account.

    IBKR's summary and positions endpoints operate on whichever account is
    currently "active" in the gateway session, so this switches into each
    account in turn and restores the advisor's original active account afterwards.
    """
    if not accounts:
        return

    original_account_id = get_active_account_id()

    for account in accounts:
        account_id = account.get("accountId")
        if not account_id:
            continue

        try:
            switch_response = requests.post(f"{BASE_API_URL}/iserver/account", json={"acctId": account_id}, verify=False)
            if switch_response.status_code >= 400:
                print(f"Skipping snapshot for {account_id}: failed to switch active account ({switch_response.status_code})")
                continue

            summary_response = requests.get(f"{BASE_API_URL}/portfolio/{account_id}/summary", verify=False)
            summary = summary_response.json() if summary_response.content else {}

            positions_response = requests.get(f"{BASE_API_URL}/portfolio/{account_id}/positions/0", verify=False)
            positions = positions_response.json() if positions_response.content else []
            if not isinstance(positions, list):
                positions = []

            portfolio_value = _summary_amount(summary, "netliquidation")
            realized_pnl = _summary_amount(summary, "realizedpnl")
            unrealized_pnl = _summary_amount(summary, "unrealizedpnl")
            cash_balance = _summary_amount(summary, "totalcashvalue")
            total_pnl = realized_pnl + unrealized_pnl

            upsert_account_metadata(
                account_id=account_id,
                account_alias=account.get("accountAlias", ""),
                account_type=account.get("type", ""),
                business_type=account.get("businessType", ""),
                currency=account.get("currency", ""),
                portfolio_value=portfolio_value,
                total_pnl=total_pnl,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
            )

            record_portfolio_performance(
                account_id=account_id,
                portfolio_value=portfolio_value,
                total_pnl=total_pnl,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                cash_balance=cash_balance,
            )

            record_holdings_snapshot(account_id, positions)

        except Exception as e:
            print(f"Error capturing portfolio snapshot for {account_id}: {e}")

    if original_account_id:
        try:
            requests.post(f"{BASE_API_URL}/iserver/account", json={"acctId": original_account_id}, verify=False)
        except Exception as e:
            print(f"Error restoring active account {original_account_id}: {e}")


_spy_conid_cache = {}


def get_spy_conid():
    """Look up SPY's conid once and cache it for the life of the process."""
    if _spy_conid_cache.get("conid"):
        return _spy_conid_cache["conid"]

    try:
        r = requests.get(f"{BASE_API_URL}/iserver/secdef/search?symbol=SPY", verify=False)
        results = r.json() if r.content else []
        if isinstance(results, list) and results:
            conid = results[0].get("conid")
            if conid:
                _spy_conid_cache["conid"] = conid
                return conid
    except Exception as e:
        print(f"Error looking up SPY conid: {e}")

    return None


def fetch_benchmark_bars(conid, since_ts):
    """Fetch daily SPY bars covering (at least) the requested start date."""
    if not conid:
        return []

    try:
        r = requests.get(f"{BASE_API_URL}/iserver/marketdata/history?conid={conid}&period=1y&bar=1d", verify=False)
        history = r.json() if r.content else {}
        bars = history.get("data", []) if isinstance(history, dict) else []
    except Exception as e:
        print(f"Error fetching SPY benchmark history: {e}")
        return []

    return [bar for bar in bars if bar.get("t", 0) >= since_ts]


def build_performance_chart_data(daily_performance, benchmark_bars):
    """Pair up the account's YTD P&L history with an SPY line rebased to the
    same starting dollar amount, so both series are comparable in $ terms.
    """
    portfolio_series = [
        {"time": int(row["timestamp"] / 1000), "value": row["portfolio_value"]}
        for row in daily_performance
    ]

    benchmark_series = []
    if daily_performance and benchmark_bars:
        start_value = daily_performance[0]["portfolio_value"]
        start_price = benchmark_bars[0].get("c")
        if start_value and start_price:
            benchmark_series = [
                {"time": int(bar["t"] / 1000), "value": start_value * (bar["c"] / start_price)}
                for bar in benchmark_bars
            ]

    return {"portfolio": portfolio_series, "benchmark": benchmark_series}


@app.route("/webhook", methods=["POST"])
def webhook():

    try:
        # Get JSON from TradingView (tolerate malformed bodies so we can still log them)
        data = request.get_json(silent=True)
        raw_body = "" if data is not None else request.get_data(as_text=True)

        log_webhook_traffic(data, raw_body)

        data = data or {}

        print("Received webhook:", data)

        # -----------------------------
        # 1. Security Check
        # -----------------------------

        if data.get("secret") != WEBHOOK_SECRET:
            print("Invalid webhook secret")

            return jsonify({
                "status": "error",
                "message": "Invalid secret"
            }), 403

        # -----------------------------
        # 2. Parse Trade Details
        # -----------------------------

        symbol = data["symbol"].upper()

        action = data["strategy"]["order_action"].upper()

        try:
            quantity = int(
                float(
                    data["strategy"]["order_contracts"]
                )
            )

        except (TypeError, ValueError):

            return jsonify({
                "status": "error",
                "message": "Invalid order quantity"
            }), 400

        # -----------------------------
        # 3. Validate Quantity
        # -----------------------------

        if quantity <= 0:

            return jsonify({
                "status": "error",
                "message": "Order quantity must be greater than zero"
            }), 400

        # -----------------------------
        # 4. Validate Action
        # -----------------------------

        if action not in ["BUY", "SELL"]:

            return jsonify({
                "status": "error",
                "message": "Invalid order action"
            }), 400

        # -----------------------------
        # 5. Process Trade
        # -----------------------------

        print(
            f"Processing trade: "
            f"{action} {quantity} {symbol}"
        )

        # Your IBKR trading logic goes here

        return jsonify({
            "status": "success",
            "symbol": symbol,
            "action": action,
            "quantity": quantity
        }), 200

    except Exception as e:

        print("Webhook error:", e)

        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500


@app.route("/switch-account", methods=['GET'])
def switch_account():
    account_id = request.args.get('account_id')
    if account_id:
        session['account_id'] = account_id
    return redirect(request.referrer or '/')


@app.route("/")
def dashboard():
    accounts = fetch_subaccounts()
    if not accounts:
        return 'Make sure you authenticate first then visit this page. <a href="https://localhost:5055">Log in</a>'

    active_account_id = get_active_account_id()

    # Try to locate the active account in the subaccount list
    account = next((a for a in accounts if a.get('accountId') == active_account_id), None)
    if account is None:
        account = accounts[0]
        session['account_id'] = account['accountId']

    print(f"== account: {account} ==")

    r = requests.get(f"{BASE_API_URL}/portfolio/{get_active_account_id()}/summary", verify=False)
    summary = r.json()

    try:
        capture_portfolio_snapshots(accounts)
    except Exception as e:
        print(f"Error refreshing portfolio intelligence snapshots: {e}")

    return render_template("dashboard.html", account=account, summary=summary, accounts=accounts, selected_account_id=get_active_account_id())


@app.route("/lookup")
def lookup():
    symbol = request.args.get('symbol', '').strip()
    asset_class = request.args.get('asset_class', '')
    exchange = request.args.get('exchange', '').strip().upper()
    asset_classes = {
        '': 'All asset classes',
        'STK': 'Equities & ETFs',
        'FUND': 'Mutual funds',
        'FUT': 'Futures',
        'CASH': 'Currencies',
        'CMDTY': 'Commodities',
        'OPT': 'Options',
        'FOP': 'Futures options',
        'BOND': 'Bonds',
        'IND': 'Indices',
        'CFD': 'CFDs',
    }
    exchange_groups = {
        'United States': [
            ('NASDAQ', 'NASDAQ'),
            ('NYSE', 'New York Stock Exchange (NYSE)'),
            ('ARCA', 'NYSE Arca'),
            ('AMEX', 'NYSE American (AMEX)'),
            ('BATS', 'Cboe BZX (BATS)'),
            ('IEX', 'Investors Exchange (IEX)'),
            ('PINK', 'OTC Pink'),
            ('OTC', 'Over-the-counter (OTC)'),
        ],
        'Futures': [
            ('CME', 'CME'),
            ('CBOT', 'CBOT'),
            ('NYMEX', 'NYMEX'),
            ('COMEX', 'COMEX'),
            ('ICEUS', 'ICE US'),
        ],
        'International': [
            ('LSE', 'London Stock Exchange (LSE)'),
            ('AEB', 'Euronext Amsterdam (AEB)'),
            ('IBIS', 'Xetra (IBIS)'),
            ('TSE', 'Toronto Stock Exchange (TSE)'),
            ('TSX', 'TSX Venture Exchange (TSX)'),
            ('SEHK', 'Hong Kong Stock Exchange (SEHK)'),
            ('ASX', 'Australian Securities Exchange (ASX)'),
            ('NSE', 'National Stock Exchange of India (NSE)'),
            ('SGX', 'Singapore Exchange (SGX)'),
        ],
        'Currencies': [
            ('IDEALPRO', 'IDEALPRO'),
        ],
    }
    exchange_codes = {
        code for options in exchange_groups.values() for code, _ in options
    }
    if asset_class not in asset_classes:
        asset_class = ''
    if exchange not in exchange_codes:
        exchange = ''

    stocks = []
    accounts = fetch_subaccounts()

    if symbol:
        params = {'symbol': symbol, 'name': 'true'}
        if asset_class:
            params['secType'] = asset_class
        r = requests.get(
            f"{BASE_API_URL}/iserver/secdef/search",
            params=params,
            verify=False
        )
        response = r.json()
        if isinstance(response, list):
            stocks = response
            if exchange:
                stocks = [
                    stock for stock in stocks
                    if any(
                        str(section.get('exchange', '')).upper() == exchange
                        for section in stock.get('sections', [])
                    )
                ]

    return render_template(
        "lookup.html",
        stocks=stocks,
        symbol=symbol,
        asset_class=asset_class,
        asset_classes=asset_classes,
        exchange_groups=exchange_groups,
        exchange=exchange,
        searched=bool(symbol),
        accounts=accounts,
        selected_account_id=get_active_account_id()
    )


@app.route("/contract/<contract_id>/<period>")
def contract(contract_id, period='5d', bar='1d'):
    data = {
        "conids": [
            contract_id
        ]
    }

    r = requests.post(f"{BASE_API_URL}/trsrv/secdef", data=data, verify=False)
    contract = r.json()['secdef'][0]

    fundamental_fields = [
        ('Market capitalization', {'marketcap', 'marketcapitalization', 'mktcap', 'mktcapmil', 'mkcap'}),
        ('P/E ratio', {'pe', 'peratio', 'pricetoearnings', 'priceearningsratio', 'peexclxor', 'peexclxorttm', 'ttmpe'}),
        ('Forward P/E', {'forwardpe', 'forwardperatio', 'fwdeps'}),
        ('EPS (TTM)', {'eps', 'ttmeps', 'ttmepsxordiluted', 'dilutedttmeps', 'epsdiluted', 'earningspershare'}),
        ('Dividend yield', {'dividendyield', 'divyield', 'ttmyield', 'ttmdividendyield'}),
        ('Annual dividend per share', {'dividendpershare', 'annualdividend', 'dividend'}),
        ('Price / book', {'pricetobook', 'pricetobookratio', 'price2bk', 'p2b', 'ptb'}),
        ('Price / sales', {'pricetosales', 'pricetosalesratio', 'price2sales', 'p2s'}),
        ('Beta', {'beta', 'betavalue'}),
        ('52-week high', {'52weekhigh', 'week52high', 'high52week', 'high52'}),
        ('52-week low', {'52weeklow', 'week52low', 'low52week', 'low52'}),
        ('Return on equity', {'roe', 'returnonequity'}),
        ('Profit margin', {'profitmargin', 'netprofitmargin'}),
    ]

    def normalize_metric_key(value):
        return ''.join(character for character in str(value).lower() if character.isalnum())

    def extract_fundamental_values(payload):
        aliases = {
            alias: label
            for label, field_aliases in fundamental_fields
            for alias in field_aliases
        }
        values = {}

        def visit(item):
            if isinstance(item, dict):
                metric_name = next(
                    (item.get(key) for key in ('name', 'label', 'metric', 'key') if item.get(key)),
                    None
                )
                if metric_name:
                    label = aliases.get(normalize_metric_key(metric_name))
                    metric_value = next(
                        (item.get(key) for key in ('value', 'rawValue', 'displayValue') if item.get(key) is not None),
                        None
                    )
                    if label and metric_value is not None:
                        values.setdefault(label, str(metric_value))

                for key, value in item.items():
                    label = aliases.get(normalize_metric_key(key))
                    if label and not isinstance(value, (dict, list)) and value is not None:
                        values.setdefault(label, str(value))
                    visit(value)
            elif isinstance(item, list):
                for value in item:
                    visit(value)

        visit(payload)
        return [
            {'label': label, 'value': values.get(label)}
            for label, _ in fundamental_fields
        ]

    contract_type = str(
        contract.get('assetClass') or contract.get('secType') or contract.get('type') or ''
    ).upper()
    show_fundamentals = contract_type in {'STK', 'STOCK', 'ETF'}
    fundamentals = []
    if show_fundamentals:
        try:
            fundamentals_response = requests.get(
                f"{BASE_API_URL}/iserver/fundamentals/{contract_id}/metrics",
                params={'type': 'ReportSnapshot'},
                verify=False
            )
            if fundamentals_response.ok:
                fundamentals = extract_fundamental_values(fundamentals_response.json())
        except (requests.RequestException, ValueError, TypeError) as exc:
            print(f"Unable to load fundamentals for {contract_id}: {exc}")

    r = requests.get(f"{BASE_API_URL}/iserver/marketdata/history?conid={contract_id}&period={period}&bar={bar}", verify=False)
    price_history = r.json()

    accounts = fetch_subaccounts()
    fundamentals_available = any(metric['value'] is not None for metric in fundamentals)
    return render_template(
        "contract.html",
        price_history=price_history,
        contract=contract,
        show_fundamentals=show_fundamentals,
        fundamentals=fundamentals,
        fundamentals_available=fundamentals_available,
        accounts=accounts,
        selected_account_id=get_active_account_id()
    )


@app.route("/orders")
def orders():
    active_account_id = get_active_account_id()
    accounts = fetch_subaccounts()
    history_windows = (7, 30, 90, 180, 365)
    try:
        selected_days = int(request.args.get("days", 30))
    except (TypeError, ValueError):
        selected_days = 30
    if selected_days not in history_windows:
        selected_days = 30

    print("== fetching orders ==")
    print("Account_ID used for fetching orders: ", active_account_id)
    live_orders = []
    error_msg = None

    try:
        # Switch the active account first for financial advisor / multi-account structures
        switch_payload = {"acctId": active_account_id}
        switch_response = requests.post(f"{BASE_API_URL}/iserver/account", json=switch_payload, verify=False)
        print(f"Switch Account Response Status: {switch_response.status_code}")
        print(f"Switch Account Response: {switch_response.text}")

        if switch_response.status_code >= 400:
            error_msg = f"Failed to switch account with status {switch_response.status_code}: {switch_response.text}"
            print(error_msg)
        else:
            response = requests.get(f"{BASE_API_URL}/iserver/account/orders", verify=False)
            print(f"Orders Response Status: {response.status_code}")
            print(f"Orders Response: {response.text}")

            if response.status_code >= 400:
                error_msg = f"Failed to fetch orders with status {response.status_code}: {response.text}"
                print(error_msg)
            elif response.content:
                response_data = response.json()
                live_orders = response_data.get("orders", []) if isinstance(response_data, dict) else []
                if isinstance(live_orders, list):
                    record_live_orders(active_account_id, live_orders)
                else:
                    live_orders = []
            else:
                print("No live orders returned from IBKR")

            if response.status_code < 400:
                live_order_ids = {
                    str(order.get("orderId") or order.get("order_id"))
                    for order in live_orders
                    if order.get("orderId") or order.get("order_id")
                }
                for tracked_order in get_trackable_orders(active_account_id):
                    order_id = str(tracked_order["ibkr_order_id"])
                    if order_id in live_order_ids:
                        continue
                    try:
                        status_response = requests.get(
                            f"{BASE_API_URL}/iserver/account/order/status/{order_id}",
                            verify=False
                        )
                        if status_response.status_code < 400 and status_response.content:
                            update_order_record(
                                tracked_order["id"],
                                response_json=status_response.json(),
                                http_status=status_response.status_code,
                            )
                    except Exception as e:
                        print(f"Unable to refresh status for IBKR order {order_id}: {e}")

    except Exception as e:
        error_msg = f"Error fetching orders: {str(e)}"
        print(error_msg)

    display_orders = {}
    for record in get_order_history(active_account_id, selected_days):
        order_id = str(record["ibkr_order_id"] or f"Local-{record['id']}")
        quantity = record["quantity"] or 0
        action = record["action"] or ""
        ticker = record["ticker"] or (f"Conid {record['conid']}" if record["conid"] else "")
        order_description = f"{action} {quantity:g} {ticker}".strip()
        if record["error_message"]:
            order_description = record["error_message"]
        display_orders[order_id] = {
            "orderId": order_id,
            "ticker": ticker,
            "description1": record["description"],
            "companyName": record["company"],
            "orderDesc": order_description,
            "orderType": record["order_type"],
            "status": record["status"],
            "side": action,
            "totalSize": quantity,
            "price": record["price"],
            "filledQuantity": record["filled_quantity"],
            "avgPrice": record["average_fill_price"],
            "createdAt": datetime.fromtimestamp(record["created_at"] / 1000).strftime("%Y-%m-%d %H:%M"),
            "sortTimestamp": record["created_at"],
            "errorMessage": record["error_message"],
        }

    for order in live_orders:
        live_order_id = order.get("orderId") or order.get("order_id")
        if not live_order_id:
            continue
        order_id = str(live_order_id)
        if order_id not in display_orders:
            display_orders[order_id] = {
                "orderId": order_id,
                "ticker": order.get("ticker", ""),
                "description1": order.get("description1", ""),
                "companyName": order.get("companyName", ""),
                "orderDesc": order.get("orderDesc", ""),
                "orderType": order.get("orderType", ""),
                "status": order.get("status", ""),
                "side": order.get("side", ""),
                "totalSize": order.get("totalSize") or order.get("quantity") or 0,
                "price": order.get("price") or order.get("limit_price"),
                "filledQuantity": order.get("filledQuantity", 0),
                "avgPrice": order.get("avgPrice"),
                "createdAt": order.get("order_time", ""),
                "sortTimestamp": 0,
                "errorMessage": "",
            }

    orders = sorted(display_orders.values(), key=lambda order: order["sortTimestamp"], reverse=True)
    return render_template(
        "orders.html",
        orders=orders,
        statuses=sorted({order.get("status", "") for order in orders if order.get("status")}),
        account_id=active_account_id,
        history_windows=history_windows,
        selected_days=selected_days,
        error=error_msg,
        accounts=accounts,
        selected_account_id=active_account_id,
    )


@app.route("/limit_order", methods=['POST'])
def place_order():
    active_account_id = get_active_account_id()
    print("== placing Limit Order ==")
    print("Account_ID used for limit order: ", active_account_id)

    data = {
        "orders": [
            {
                "conid": int(request.form.get('contract_id')),
                "orderType": "LMT",
                "price": float(request.form.get('price')),
                "quantity": int(request.form.get('quantity')),
                "side": request.form.get('side'),
                "tif": "GTC"
            }
        ]
    }

    print(f"Order payload: {data}")

    order_record_id = create_order_record(
        active_account_id,
        data,
        ticker=request.form.get('ticker', ''),
        description=request.form.get('description', ''),
        company=request.form.get('company', ''),
    )
    
    try:
        r = requests.post(f"{BASE_API_URL}/iserver/account/{active_account_id}/orders", json=data, verify=False)
        print(f"IBKR Response Status: {r.status_code}")
        print(f"IBKR Response: {r.text}")

        # Check for errors in response
        if r.status_code >= 400:
            error_msg = f"Order submission failed with status {r.status_code}: {r.text}"
            print(error_msg)
            update_order_record(
                order_record_id,
                response_json={"body": r.text},
                status="Rejected",
                error_message=error_msg,
                http_status=r.status_code,
            )
            return render_template("orders.html", orders=[], error=error_msg)
        
        response_json = r.json()
        response_json = confirm_ibkr_warnings(response_json)
        record_submitted_order(active_account_id, data['orders'][0], response_json, order_record_id, r.status_code)
        print(f"Order response JSON: {response_json}")
        
    except Exception as e:
        error_msg = f"Error placing order: {str(e)}"
        print(error_msg)
        update_order_record(
            order_record_id,
            status="SubmissionFailed",
            error_message=error_msg,
        )
        return render_template("orders.html", orders=[], error=error_msg)

    return redirect("/orders")


@app.route("/market_order", methods=['POST'])
def place_market_order():
    active_account_id = get_active_account_id()
    print("== placing Market Order ==")
    print("Account_ID used for market order: ", active_account_id)

    data = {
        "orders": [
            {
                "conid": int(request.form.get('contract_id')),
                "orderType": "MKT",
                "quantity": int(request.form.get('quantity')),
                "side": request.form.get('side'),
                "tif": "GTC"
            }
        ]
    }

    print(f"Order payload: {data}")

    order_record_id = create_order_record(
        active_account_id,
        data,
        ticker=request.form.get('ticker', ''),
        description=request.form.get('description', ''),
        company=request.form.get('company', ''),
    )

    try:
        r = requests.post(f"{BASE_API_URL}/iserver/account/{active_account_id}/orders", json=data, verify=False)
        print(f"IBKR Response Status: {r.status_code}")
        print(f"IBKR Response: {r.text}")

        if r.status_code >= 400:
            error_msg = f"Order submission failed with status {r.status_code}: {r.text}"
            print(error_msg)
            update_order_record(
                order_record_id,
                response_json={"body": r.text},
                status="Rejected",
                error_message=error_msg,
                http_status=r.status_code,
            )
            return render_template("orders.html", orders=[], error=error_msg)

        response_json = r.json()
        response_json = confirm_ibkr_warnings(response_json)
        record_submitted_order(active_account_id, data['orders'][0], response_json, order_record_id, r.status_code)
        print(f"Order response JSON: {response_json}")

    except Exception as e:
        error_msg = f"Error placing order: {str(e)}"
        print(error_msg)
        update_order_record(
            order_record_id,
            status="SubmissionFailed",
            error_message=error_msg,
        )
        return render_template("orders.html", orders=[], error=error_msg)

    return redirect("/orders")


@app.route("/orders/<order_id>/cancel")
def cancel_order(order_id):
    active_account_id = get_active_account_id()
    cancel_url = f"{BASE_API_URL}/iserver/account/{active_account_id}/order/{order_id}"
    r = requests.delete(cancel_url, verify=False)
    try:
        response_json = r.json()
    except ValueError:
        response_json = {"body": r.text}
    update_order_record_by_ibkr_id(
        active_account_id,
        order_id,
        response_json,
        status="PendingCancel" if r.status_code < 400 else None,
        error_message=None if r.status_code < 400 else r.text,
        http_status=r.status_code,
    )

    return response_json


@app.route("/portfolio")
def portfolio():
    active_account_id = get_active_account_id()
    accounts = fetch_subaccounts()

    summary_response = requests.get(
        f"{BASE_API_URL}/portfolio/{active_account_id}/summary",
        verify=False
    )
    summary = summary_response.json() if summary_response.content else {}
    cash_balance = _summary_amount(summary, "totalcashvalue")
    portfolio_value = _summary_amount(summary, "netliquidation")

    r = requests.get(f"{BASE_API_URL}/portfolio/{active_account_id}/positions/0", verify=False)

    if r.content:
        positions = r.json()
    else:
        positions = []

    commissions_by_conid = fetch_commissions_by_conid(active_account_id)

    for item in positions:
        item["costBasis"] = item.get("avgCost", 0) * item.get("position", 0)
        item["commission"] = commissions_by_conid.get(item.get("conid"), 0.0)

    # sort P&L book by unrealized P&L, highest first
    positions.sort(key=lambda p: p.get("unrealizedPnl", 0), reverse=True)

    try:
        capture_portfolio_snapshots(accounts)
    except Exception as e:
        print(f"Error refreshing portfolio intelligence snapshots: {e}")

    ytd_start_ts = int(datetime(datetime.now(timezone.utc).year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    daily_performance = get_daily_performance_since(active_account_id, ytd_start_ts)
    benchmark_bars = fetch_benchmark_bars(get_spy_conid(), ytd_start_ts)
    performance_chart_data = build_performance_chart_data(daily_performance, benchmark_bars)

    # return my positions, how much cash i have in this account
    return render_template(
        "portfolio.html",
        positions=positions,
        cash_balance=cash_balance,
        portfolio_value=portfolio_value,
        securities_value=sum(item.get("mktValue", 0) for item in positions),
        has_portfolio=bool(positions) or cash_balance != 0 or portfolio_value != 0,
        account_id=active_account_id,
        accounts=accounts,
        selected_account_id=active_account_id,
        performance_chart_data=performance_chart_data,
    )

@app.route("/scanner")
def scanner():
    accounts = fetch_subaccounts()

    r = requests.get(f"{BASE_API_URL}/iserver/scanner/params", verify=False)
    params = r.json() if r.content else {}

    if not isinstance(params, dict) or 'instrument_list' not in params:
        error_msg = f"Failed to load scanner parameters from IBKR (status {r.status_code}): {r.text}"
        return render_template("scanner.html", params={}, scanner_map={}, filter_map={}, scan_results=[], error=error_msg, accounts=accounts, selected_account_id=get_active_account_id())

    scanner_map = {}
    filter_map = {}

    for item in params['instrument_list']:
        scanner_map[item['type']] = {
            "display_name": item['display_name'],
            "filters": item['filters'],
            "sorts": []
        }

    for item in params['filter_list']:
        filter_map[item['group']] = {
            "display_name": item['display_name'],
            "type": item['type'],
            "code": item['code']
        }

    for item in params['scan_type_list']:
        for instrument in item['instruments']:
            scanner_map[instrument]['sorts'].append({
                "name": item['display_name'],
                "code": item['code']
            })

    for item in params['location_tree']:
        scanner_map[item['type']]['locations'] = item['locations']

    # IBKR exposes market cap thresholds as separate above/below filter codes, discover them dynamically
    market_cap_codes = {}
    for item in params['filter_list']:
        code = item.get('code', '')
        if 'marketcap' in code.lower():
            if 'below' in code.lower():
                market_cap_codes['below'] = code
            elif 'above' in code.lower():
                market_cap_codes['above'] = code

    submitted = request.args.get("submitted", "")
    selected_instrument = request.args.get("instrument", "")
    location = request.args.get("location", "")
    sort = request.args.get("sort", "")
    scan_results = []
    filter_code = request.args.get("filter", "")
    filter_value = request.args.get("filter_value", "")
    market_cap_min = request.args.get("market_cap_min", "")
    market_cap_max = request.args.get("market_cap_max", "")

    if submitted:
        filters = [
            {
                "code": filter_code,
                "value": filter_value
            }
        ]

        if market_cap_min and market_cap_codes.get('above'):
            filters.append({
                "code": market_cap_codes['above'],
                "value": str(float(market_cap_min) * 1_000_000)
            })

        if market_cap_max and market_cap_codes.get('below'):
            filters.append({
                "code": market_cap_codes['below'],
                "value": str(float(market_cap_max) * 1_000_000)
            })

        data = {
            "instrument": selected_instrument,
            "location": location,
            "type": sort,
            "filter": filters
        }
            
        r = requests.post(f"{BASE_API_URL}/iserver/scanner/run", json=data, verify=False)
        scan_results = r.json()

    return render_template(
        "scanner.html",
        params=params,
        scanner_map=scanner_map,
        filter_map=filter_map,
        scan_results=scan_results,
        market_cap_available=bool(market_cap_codes.get('above') or market_cap_codes.get('below')),
        accounts=accounts,
        selected_account_id=get_active_account_id()
    )

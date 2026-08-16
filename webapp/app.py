import requests, time, os, random
from flask import Flask, render_template, request, redirect, session
from urllib3.exceptions import InsecureRequestWarning

# disable warnings until you install a certificate
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

BASE_API_URL = "https://localhost:5055/v1/api"
ACCOUNT_ID = os.environ['IBKR_ACCOUNT_ID']

os.environ['PYTHONHTTPSVERIFY'] = '0'

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(32))

@app.template_filter('ctime')
def timectime(s):
    return time.ctime(s/1000)


def get_active_account_id():
    return session.get('account_id', ACCOUNT_ID)


def fetch_subaccounts():
    try:
        r = requests.get(f"{BASE_API_URL}/portfolio/subaccounts", verify=False)
        return r.json()
    except Exception:
        return []


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

    return render_template("dashboard.html", account=account, summary=summary, accounts=accounts, selected_account_id=get_active_account_id())


@app.route("/lookup")
def lookup():
    symbol = request.args.get('symbol', None)
    stocks = []
    accounts = fetch_subaccounts()

    if symbol is not None:
        r = requests.get(f"{BASE_API_URL}/iserver/secdef/search?symbol={symbol}&name=true", verify=False)

        response = r.json()
        stocks = response

    return render_template("lookup.html", stocks=stocks, accounts=accounts, selected_account_id=get_active_account_id())


@app.route("/contract/<contract_id>/<period>")
def contract(contract_id, period='5d', bar='1d'):
    data = {
        "conids": [
            contract_id
        ]
    }

    r = requests.post(f"{BASE_API_URL}/trsrv/secdef", data=data, verify=False)
    contract = r.json()['secdef'][0]

    r = requests.get(f"{BASE_API_URL}/iserver/marketdata/history?conid={contract_id}&period={period}&bar={bar}", verify=False)
    price_history = r.json()

    accounts = fetch_subaccounts()
    return render_template("contract.html", price_history=price_history, contract=contract, accounts=accounts, selected_account_id=get_active_account_id())


@app.route("/orders")
def orders():
    active_account_id = get_active_account_id()
    accounts = fetch_subaccounts()
    print("== fetching orders ==")
    print("Account_ID used for fetching orders: ", active_account_id)

    try:
        # Switch the active account first for financial advisor / multi-account structures
        switch_payload = {"acctId": active_account_id}
        switch_response = requests.post(f"{BASE_API_URL}/iserver/account", json=switch_payload, verify=False)
        print(f"Switch Account Response Status: {switch_response.status_code}")
        print(f"Switch Account Response: {switch_response.text}")

        if switch_response.status_code >= 400:
            error_msg = f"Failed to switch account with status {switch_response.status_code}: {switch_response.text}"
            print(error_msg)
            return render_template("orders.html", orders=[], error=error_msg, accounts=accounts, selected_account_id=active_account_id)

        r = requests.get(f"{BASE_API_URL}/iserver/account/orders", verify=False)
        print(f"Orders Response Status: {r.status_code}")
        print(f"Orders Response: {r.text}")
        
        if r.status_code >= 400:
            error_msg = f"Failed to fetch orders with status {r.status_code}: {r.text}"
            print(error_msg)
            return render_template("orders.html", orders=[], error=error_msg, accounts=accounts, selected_account_id=active_account_id)

        if r.text:
            orders = r.json()["orders"]
        else:
            orders = []
            print("No orders returned from IBKR")

    except Exception as e:
        error_msg = f"Error fetching orders: {str(e)}"
        print(error_msg)
        return render_template("orders.html", orders=[], error=error_msg, accounts=accounts, selected_account_id=active_account_id)

    return render_template("orders.html", orders=orders, account_id=active_account_id, accounts=accounts, selected_account_id=active_account_id)


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
    
    try:
        r = requests.post(f"{BASE_API_URL}/iserver/account/{active_account_id}/orders", json=data, verify=False)
        print(f"IBKR Response Status: {r.status_code}")
        print(f"IBKR Response: {r.text}")

        # Check for errors in response
        if r.status_code >= 400:
            error_msg = f"Order submission failed with status {r.status_code}: {r.text}"
            print(error_msg)
            return render_template("orders.html", orders=[], error=error_msg)
        
        response_json = r.json()
        print(f"Order response JSON: {response_json}")
        
    except Exception as e:
        error_msg = f"Error placing order: {str(e)}"
        print(error_msg)
        return render_template("orders.html", orders=[], error=error_msg)

    return redirect("/orders")


@app.route("/orders/<order_id>/cancel")
def cancel_order(order_id):
    active_account_id = get_active_account_id()
    cancel_url = f"{BASE_API_URL}/iserver/account/{active_account_id}/order/{order_id}"
    r = requests.delete(cancel_url, verify=False)

    return r.json()


@app.route("/portfolio")
def portfolio():
    active_account_id = get_active_account_id()
    accounts = fetch_subaccounts()

    r = requests.get(f"{BASE_API_URL}/portfolio/{active_account_id}/positions/0", verify=False)

    if r.content:
        positions = r.json()
    else:
        positions = []

    # return my positions, how much cash i have in this account
    return render_template("portfolio.html", positions=positions, account_id=active_account_id, accounts=accounts, selected_account_id=active_account_id)

@app.route("/scanner")
def scanner():
    r = requests.get(f"{BASE_API_URL}/iserver/scanner/params", verify=False)
    params = r.json()

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


    submitted = request.args.get("submitted", "")
    selected_instrument = request.args.get("instrument", "")
    location = request.args.get("location", "")
    sort = request.args.get("sort", "")
    scan_results = []
    filter_code = request.args.get("filter", "")
    filter_value = request.args.get("filter_value", "")

    if submitted:
        data = {
            "instrument": selected_instrument,
            "location": location,
            "type": sort,
            "filter": [
                {
                    "code": filter_code,
                    "value": filter_value
                }
            ]
        }
            
        r = requests.post(f"{BASE_API_URL}/iserver/scanner/run", json=data, verify=False)
        scan_results = r.json()

    accounts = fetch_subaccounts()
    return render_template("scanner.html", params=params, scanner_map=scanner_map, filter_map=filter_map, scan_results=scan_results, accounts=accounts, selected_account_id=get_active_account_id())

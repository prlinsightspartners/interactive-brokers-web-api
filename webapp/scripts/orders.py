"""Order listing, submission, cancellation, and status synchronization routes."""

from datetime import datetime

import requests
from flask import Blueprint, redirect, render_template, request

from scripts.common import BASE_API_URL, fetch_subaccounts, get_active_account_id
from trade_db import (
    create_order_record,
    get_order_history,
    get_trackable_orders,
    update_order_record,
    update_order_record_by_ibkr_id,
    upsert_trade,
)

bp = Blueprint("orders", __name__)

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

@bp.route("/orders")
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

@bp.route("/limit_order", methods=["POST"])
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

@bp.route("/market_order", methods=["POST"])
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

@bp.route("/orders/<order_id>/cancel")
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

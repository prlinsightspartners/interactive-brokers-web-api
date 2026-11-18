import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

from market_signals_engine.order_generation.audit import (
    DEFAULT_AUDIT_LOG,
    append_order_event,
)


DEFAULT_API_URL = "https://localhost:5055/v1/api"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _positive_quantity(value: str) -> int:
    try:
        quantity = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("quantity must be a positive whole number") from error
    if quantity <= 0:
        raise argparse.ArgumentTypeError("quantity must be a positive whole number")
    return quantity


def _positive_price(value: str) -> Decimal:
    try:
        price = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("limit price must be a positive number") from error
    if not price.is_finite() or price <= 0:
        raise argparse.ArgumentTypeError("limit price must be a positive number")
    return price


def _json_response(response: requests.Response):
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as error:
        raise RuntimeError(
            f"IBKR returned non-JSON data for {response.url}"
        ) from error


def _record_transmission_event(
    event_type: str,
    account_id: str,
    *,
    order_id: str | int | None = None,
    order: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    log_path: Path = DEFAULT_AUDIT_LOG,
) -> None:
    try:
        append_order_event(
            event_type,
            account_id,
            order_id=order_id,
            order=order,
            details=details,
            log_path=log_path,
        )
    except OSError as error:
        raise RuntimeError(
            "An IBKR request was attempted but its audit event could not be "
            "saved; inspect IBKR and the audit log before retrying"
        ) from error


def _resolve_contract(
    session: requests.Session, api_url: str, symbol: str, exchange: str
) -> str:
    response = session.get(
        f"{api_url}/iserver/secdef/search",
        params={"symbol": symbol, "name": "true", "secType": "STK"},
        timeout=15,
    )
    results = _json_response(response)
    if not isinstance(results, list):
        raise RuntimeError("IBKR returned an unexpected contract-search response")

    matches = [
        item
        for item in results
        if item.get("symbol") == symbol
        and item.get("secType") == "STK"
        and (item.get("companyHeader") or "").endswith(f"({exchange})")
        and item.get("conid")
        and str(item["conid"]) != "-1"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {symbol} stock contract on {exchange}; "
            f"found {len(matches)}"
        )
    return str(matches[0]["conid"])


def generate_order(
    session: requests.Session,
    api_url: str,
    account_id: str,
    symbol: str,
    quantity: int,
    limit_price: Decimal,
    time_in_force: str,
    exchange: str,
    submit: bool,
    audit_log_path: Path = DEFAULT_AUDIT_LOG,
) -> dict:
    parsed_url = urlparse(api_url)
    if parsed_url.scheme != "https" or parsed_url.hostname not in {
        "localhost",
        "127.0.0.1",
    }:
        raise ValueError("IBKR API URL must use HTTPS on localhost")
    if not account_id.startswith("DU"):
        raise ValueError("Refusing to route this order outside a DU paper account")

    auth = _json_response(
        session.get(f"{api_url}/iserver/auth/status", timeout=15)
    )
    if not auth.get("authenticated") or not auth.get("connected"):
        raise RuntimeError("IBKR Gateway is not authenticated and connected")

    account_response = _json_response(
        session.get(f"{api_url}/portfolio/subaccounts", timeout=15)
    )
    available_accounts = {
        str(item.get("accountId"))
        for item in account_response
        if isinstance(item, dict)
    } if isinstance(account_response, list) else set()
    if account_id not in available_accounts:
        raise RuntimeError("Configured paper account is not available in IBKR")

    conid = _resolve_contract(session, api_url, symbol, exchange)
    payload = {
        "orders": [
            {
                "conid": int(conid),
                "orderType": "LMT",
                "price": float(limit_price),
                "quantity": quantity,
                "side": "BUY",
                "tif": time_in_force,
            }
        ]
    }
    order_details = {
        **payload["orders"][0],
        "symbol": symbol,
        "exchange": exchange,
        "currency": "USD",
    }
    if not submit:
        return {
            "submitted": False,
            "account_id": account_id,
            "conid": conid,
            "payload": payload,
        }

    append_order_event(
        "submission_requested",
        account_id,
        order=order_details,
        details={"endpoint": f"/iserver/account/{account_id}/orders"},
        log_path=audit_log_path,
    )
    try:
        response = session.post(
            f"{api_url}/iserver/account/{account_id}/orders",
            json=payload,
            timeout=30,
        )
    except requests.RequestException as error:
        _record_transmission_event(
            "submission_outcome_unknown",
            account_id,
            order=order_details,
            details={"error": str(error)},
            log_path=audit_log_path,
        )
        raise RuntimeError(
            "IBKR submission outcome is unknown; inspect IBKR and the audit log "
            "before retrying"
        ) from error
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        event_type = (
            "submission_rejected"
            if response.status_code < 500
            else "submission_outcome_unknown"
        )
        _record_transmission_event(
            event_type,
            account_id,
            order=order_details,
            details={
                "http_status": response.status_code,
                "response_body": response.text,
            },
            log_path=audit_log_path,
        )
        raise RuntimeError(
            f"IBKR order submission returned HTTP {response.status_code}; "
            "inspect IBKR and the audit log before retrying"
        ) from error
    try:
        result = response.json()
    except ValueError as error:
        _record_transmission_event(
            "submission_outcome_unknown",
            account_id,
            order=order_details,
            details={
                "http_status": response.status_code,
                "response_body": response.text,
                "error": "non-JSON broker response",
            },
            log_path=audit_log_path,
        )
        raise RuntimeError(
            "IBKR returned a non-JSON order response; inspect IBKR and the "
            "audit log before retrying"
        ) from error
    entries = result if isinstance(result, list) else [result]
    if any(
        isinstance(item, dict)
        and item.get("id")
        and item.get("messageOptions")
        for item in entries
    ):
        confirmation = next(
            item
            for item in entries
            if isinstance(item, dict)
            and item.get("id")
            and item.get("messageOptions")
        )
        _record_transmission_event(
            "confirmation_required",
            account_id,
            order=order_details,
            details={
                "confirmation_id": confirmation["id"],
                "messages": confirmation.get("message", []),
                "message_options": confirmation.get("messageOptions", []),
                "broker_response": result,
            },
            log_path=audit_log_path,
        )
        raise RuntimeError(
            "IBKR requires an order confirmation; no warning was auto-accepted: "
            + json.dumps(result)
            + f". Confirmation ID {confirmation['id']} was recorded in "
            + str(audit_log_path)
        )

    order = next(
        (
            item
            for item in entries
            if isinstance(item, dict)
            and (item.get("order_id") or item.get("orderId"))
        ),
        None,
    )
    if order is None:
        _record_transmission_event(
            "submission_unconfirmed",
            account_id,
            order=order_details,
            details={"broker_response": result},
            log_path=audit_log_path,
        )
        raise RuntimeError(
            "IBKR response did not confirm an order ID; check the order status "
            "in IBKR and inspect the audit log before retrying: "
            + json.dumps(result)
        )
    order_id = order.get("order_id") or order.get("orderId")
    status = order.get("order_status") or order.get("status", "Submitted")
    _record_transmission_event(
        "submission_acknowledged",
        account_id,
        order_id=order_id,
        order=order_details,
        details={"status": status, "broker_response": result},
        log_path=audit_log_path,
    )
    return {
        "submitted": True,
        "account_id": account_id,
        "conid": conid,
        "order_id": order_id,
        "status": status,
    }


def refresh_order_status(
    session: requests.Session,
    api_url: str,
    account_id: str,
    order_id: str,
    audit_log_path: Path = DEFAULT_AUDIT_LOG,
) -> dict:
    if not account_id.startswith("DU"):
        raise ValueError("Refusing to query outside a DU paper account")
    auth = _json_response(
        session.get(f"{api_url}/iserver/auth/status", timeout=15)
    )
    if not auth.get("authenticated") or not auth.get("connected"):
        raise RuntimeError("IBKR Gateway is not authenticated and connected")

    account_response = _json_response(
        session.get(f"{api_url}/portfolio/subaccounts", timeout=15)
    )
    available_accounts = {
        str(item.get("accountId"))
        for item in account_response
        if isinstance(item, dict)
    } if isinstance(account_response, list) else set()
    if account_id not in available_accounts:
        raise RuntimeError("Configured paper account is not available in IBKR")

    response = session.post(
        f"{api_url}/iserver/account",
        json={"acctId": account_id},
        timeout=15,
    )
    _json_response(response)
    response = session.get(f"{api_url}/iserver/account/orders", timeout=15)
    orders = _json_response(response)
    if isinstance(orders, dict):
        orders = orders.get("orders", [])
    if not isinstance(orders, list):
        raise RuntimeError("IBKR returned an unexpected open-orders response")

    match = next(
        (item for item in orders if str(item.get("orderId")) == str(order_id)),
        None,
    )
    if match is None:
        append_order_event(
            "status_not_found",
            account_id,
            order_id=order_id,
            details={
                "status": "UNKNOWN",
                "source": "open_orders",
                "note": "Order is not present in the current open-orders response; "
                "this does not establish a final order status.",
            },
            log_path=audit_log_path,
        )
        return {
            "account_id": account_id,
            "order_id": str(order_id),
            "status": "UNKNOWN",
            "note": "Not found in current open orders; inspect IBKR order history.",
        }

    status = match.get("status") or match.get("order_status") or "Unknown"
    order_details = {
        key: match.get(key)
        for key in (
            "ticker",
            "side",
            "totalSize",
            "filledQuantity",
            "remainingQuantity",
            "orderType",
            "price",
            "avgPrice",
            "timeInForce",
            "orderDesc",
        )
    }
    append_order_event(
        "status_update",
        account_id,
        order_id=order_id,
        order=order_details,
        details={"status": status, "source": "open_orders"},
        log_path=audit_log_path,
    )
    return {
        "account_id": account_id,
        "order_id": str(order_id),
        "status": status,
        "order": order_details,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate or submit a guarded IBKR paper-account limit order."
    )
    parser.add_argument("--symbol")
    parser.add_argument("--quantity", type=_positive_quantity)
    parser.add_argument("--limit-price", type=_positive_price)
    parser.add_argument("--exchange", default="NASDAQ")
    parser.add_argument("--time-in-force", choices=("DAY", "GTC"), default="DAY")
    parser.add_argument("--account-id", help="Paper account ID; defaults to IBKR_ACCOUNT_ID.")
    parser.add_argument(
        "--check-status",
        action="store_true",
        help="Refresh and log status for --order-id instead of generating an order.",
    )
    parser.add_argument("--order-id", help="IBKR order ID used with --check-status.")
    parser.add_argument(
        "--api-url", default=os.environ.get("IBKR_API_URL", DEFAULT_API_URL)
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Send the order; without this flag only validate and preview it.",
    )
    arguments = parser.parse_args()
    account_id = (
        arguments.account_id or os.environ.get("IBKR_ACCOUNT_ID", "")
    ).strip()
    if not account_id:
        parser.error("--account-id or IBKR_ACCOUNT_ID must specify a paper account")
    if arguments.check_status:
        if not arguments.order_id:
            parser.error("--order-id is required with --check-status")
        if arguments.submit:
            parser.error("--submit cannot be used with --check-status")
        if arguments.quantity is not None or arguments.limit_price is not None:
            parser.error("--quantity and --limit-price cannot be used with --check-status")
    elif (
        not arguments.symbol
        or arguments.quantity is None
        or arguments.limit_price is None
    ):
        parser.error("--symbol, --quantity, and --limit-price are required for an order")

    with requests.Session() as session:
        session.verify = False
        api_url = arguments.api_url.rstrip("/")
        if arguments.check_status:
            result = refresh_order_status(
                session=session,
                api_url=api_url,
                account_id=account_id,
                order_id=arguments.order_id,
            )
        else:
            result = generate_order(
                session=session,
                api_url=api_url,
                account_id=account_id,
                symbol=arguments.symbol.strip().upper(),
                quantity=arguments.quantity,
                limit_price=arguments.limit_price,
                time_in_force=arguments.time_in_force,
                exchange=arguments.exchange.strip().upper(),
                submit=arguments.submit,
            )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

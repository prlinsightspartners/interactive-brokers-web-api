import asyncio
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ib_async import IB, Stock, MarketOrder

from dependencies import get_ib, get_db_conn, get_db_cursor
import config

router = APIRouter(tags=["trades"])

templates = Jinja2Templates(directory="templates")

WEBHOOK_SECRET = "your_super_secret_string_123"
IB_ACCOUNT = config.ib_account.strip() if isinstance(getattr(config, "ib_account", ""), str) else ""
IB_FA_GROUP = getattr(config, "ib_fa_group", "").strip() if isinstance(getattr(config, "ib_fa_group", ""), str) else ""
IB_FA_PROFILE = getattr(config, "ib_fa_profile", "").strip() if isinstance(getattr(config, "ib_fa_profile", ""), str) else ""
IB_FA_METHOD = getattr(config, "ib_fa_method", "").strip() if isinstance(getattr(config, "ib_fa_method", ""), str) else ""
REJECTED_STATUSES = {"CANCELLED", "APICANCELLED", "INACTIVE"}


def _get_managed_accounts(ib: IB) -> list[str]:
    """Returns managed accounts from IB as a normalized list."""
    try:
        accounts = ib.managedAccounts()
        if isinstance(accounts, str):
            raw_accounts = accounts.split(",")
        else:
            raw_accounts = accounts or []
        return [str(account).strip() for account in raw_accounts if str(account).strip()]
    except Exception:
        return []


def _resolve_order_account(ib: IB) -> str:
    """Returns the best account to route the order to."""
    managed_accounts = _get_managed_accounts(ib)

    if IB_ACCOUNT:
        if managed_accounts and IB_ACCOUNT not in managed_accounts:
            fallback = managed_accounts[0]
            print(
                f"Configured ib_account '{IB_ACCOUNT}' is not in managed accounts {managed_accounts}. "
                f"Falling back to '{fallback}'."
            )
            return fallback
        return IB_ACCOUNT

    if managed_accounts:
        return managed_accounts[0]

    return ""


def _resolve_order_allocation(ib: IB, payload: dict) -> dict[str, str]:
    """
    Resolves allocation target for order routing.
    Priority:
    1) webhook payload override
    2) config account
    3) config FA group/profile
    4) first managed account from IB
    """
    payload_account = str(payload.get("account") or "").strip()
    payload_fa_group = str(payload.get("fa_group") or payload.get("group") or "").strip()
    payload_fa_profile = str(payload.get("fa_profile") or payload.get("profile") or "").strip()
    payload_fa_method = str(payload.get("fa_method") or "").strip()

    if payload_account:
        return {"account": payload_account}

    if payload_fa_group:
        allocation = {"faGroup": payload_fa_group}
        if payload_fa_method:
            allocation["faMethod"] = payload_fa_method
        return allocation

    if payload_fa_profile:
        return {"faProfile": payload_fa_profile}

    account = _resolve_order_account(ib)
    if account:
        return {"account": account}

    if IB_FA_GROUP:
        allocation = {"faGroup": IB_FA_GROUP}
        if IB_FA_METHOD:
            allocation["faMethod"] = IB_FA_METHOD
        return allocation

    if IB_FA_PROFILE:
        return {"faProfile": IB_FA_PROFILE}

    return {}


def _trade_error_reason(trade) -> str:
    """Extracts the most useful broker-side error message for a trade."""
    if getattr(trade, "advancedError", None):
        return trade.advancedError

    for entry in reversed(getattr(trade, "log", [])):
        if getattr(entry, "message", ""):
            return entry.message

    return "No fill details returned by broker"


def _trade_was_rejected_or_cancelled(trade) -> bool:
    """Checks trade status and logs for cancelled/rejected outcomes."""
    status = (getattr(trade.orderStatus, "status", "") or "").upper()
    if status in REJECTED_STATUSES:
        return True

    for entry in reversed(getattr(trade, "log", [])):
        entry_status = str(getattr(entry, "status", "") or "").upper()
        if entry_status in REJECTED_STATUSES:
            return True

    return False


def log_trade(symbol: str, action: str, quantity: int, price: float, cursor: sqlite3.Cursor, conn: sqlite3.Connection):
    """Logs a completed trade to the SQLite database."""
    timestamp = int(datetime.utcnow().timestamp())
    try:
        cursor.execute(
            "INSERT INTO trades (timestamp, symbol, action, quantity, price) VALUES (?, ?, ?, ?, ?)",
            (timestamp, symbol, action, quantity, price)
        )
        conn.commit()
        print(f"Logged trade: {action} {quantity} {symbol} @ ${price:.2f}")
    except Exception as e:
        print(f"Database logging error: {e}")


@router.get("/tradelog", response_class=HTMLResponse)
def tradelog_page(request: Request, cursor: sqlite3.Cursor = Depends(get_db_cursor)):
    """Displays the trade log page with all recorded trades."""
    cursor.execute("SELECT timestamp, symbol, action, quantity, price FROM trades ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    
    # Format trades for display
    trades = [
        {
            "timestamp": datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "price": price
        }
        for ts, symbol, action, quantity, price in rows
    ]
    return templates.TemplateResponse("tradelog.html", {"request": request, "trades": trades})


@router.post("/webhook")
@router.post("/", include_in_schema=False)
async def webhook(
    request: Request, 
    ib: IB = Depends(get_ib), 
    conn: sqlite3.Connection = Depends(get_db_conn), 
    cursor: sqlite3.Cursor = Depends(get_db_cursor)
):
    """
    Handles incoming TradingView webhooks for automated trading.
    """
    try:
        data = await request.json()
        print("Received webhook:", data)

        # 1. Security Check
        if data.get("secret") != WEBHOOK_SECRET:
            print("Invalid webhook secret")
            raise HTTPException(status_code=403, detail="Invalid secret")

        # 2. Parse Trade Details from Webhook Payload
        # This structure matches the recommended TradingView alert message format
        symbol = data["symbol"].upper()
        action = data["strategy"]["order_action"].upper()  # 'BUY' or 'SELL'
        try:
            quantity = int(float(data["strategy"]["order_contracts"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid order quantity")

        if quantity <= 0:
            raise HTTPException(status_code=400, detail="Order quantity must be greater than zero")
        
        if action not in ["BUY", "SELL"]:
            raise HTTPException(status_code=400, detail="Invalid order action")

        print(f"Processing trade: {action} {quantity} {symbol}")

        # 3. Create IBKR Contract and Order
        contract = Stock(symbol, "SMART", "USD")
        order = MarketOrder(action, quantity)
        order.tif = "DAY"

        allocation = _resolve_order_allocation(ib, data)
        if not allocation:
            raise HTTPException(
                status_code=500,
                detail="No IB account/allocation available. Set config.ib_account or configure FA allocation."
            )

        # Required for advisor/multi-account setups where IBKR needs explicit order allocation.
        if "account" in allocation:
            order.account = allocation["account"]
            print(f"Submitting order using account: {allocation['account']}")
        elif "faGroup" in allocation:
            order.faGroup = allocation["faGroup"]
            if allocation.get("faMethod"):
                order.faMethod = allocation["faMethod"]
            print(
                f"Submitting order using FA group: {allocation['faGroup']}"
                + (f", method: {allocation['faMethod']}" if allocation.get("faMethod") else "")
            )
        elif "faProfile" in allocation:
            order.faProfile = allocation["faProfile"]
            print(f"Submitting order using FA profile: {allocation['faProfile']}")

        # 4. Place the Order
        trade = ib.placeOrder(contract, order)
        print(f"Order placed for {symbol}. Waiting for fill...")

        # 5. Wait for the Order to Fill
        # We'll wait up to 30 seconds for the trade to execute.
        for _ in range(60): # 60 * 0.5s = 30s
            await asyncio.sleep(0.5)
            status = (trade.orderStatus.status or "").upper()
            if trade.isDone() or status in REJECTED_STATUSES or _trade_was_rejected_or_cancelled(trade):
                break

        status = (trade.orderStatus.status or "").upper()

        if status in REJECTED_STATUSES or _trade_was_rejected_or_cancelled(trade):
            reason = _trade_error_reason(trade)
            print(f"Order rejected/cancelled by broker for {symbol}: {reason}")
            raise HTTPException(status_code=400, detail=f"Order rejected by broker: {reason}")
        
        if not trade.isDone():
            # If the order didn't complete in time, cancel and report timeout.
            if not _trade_was_rejected_or_cancelled(trade):
                ib.cancelOrder(trade.order)
            print(f"Order for {symbol} did not fill in time. Canceled.")
            raise HTTPException(status_code=504, detail="Order did not fill in time")

        if not trade.fills:
            reason = _trade_error_reason(trade)

            print(f"Order completed without fills for {symbol}: status={status}, reason={reason}")
            raise HTTPException(status_code=502, detail=f"Order completed without fills: {reason}")

        # 6. Log the Executed Trade
        fill_price = trade.fills[0].execution.price
        log_trade(symbol, action, quantity, fill_price, cursor, conn)

        return {
            "status": "success",
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "fill_price": fill_price
        }

    except HTTPException:
        raise  # Re-raise FastAPI's own exceptions
    except Exception as e:
        print(f"Unhandled webhook error: {e}")
        raise HTTPException(status_code=500, detail=f"An internal error occurred: {e}")
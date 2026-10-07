"""Shared configuration and IBKR/portfolio helpers for Flask routes."""

import json
import os
import time
from datetime import datetime, timezone

import requests
from flask import session
from urllib3.exceptions import InsecureRequestWarning

from portfolio_db import (
    record_holdings_snapshot,
    record_portfolio_performance,
    upsert_account_metadata,
)

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
os.environ["PYTHONHTTPSVERIFY"] = "0"

BASE_API_URL = "https://localhost:5055/v1/api"
ACCOUNT_ID = os.environ["IBKR_ACCOUNT_ID"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
os.makedirs(DATA_DIR, exist_ok=True)
_spy_conid_cache = {}
_risk_history_cache = {}
_RISK_HISTORY_CACHE_SECONDS = 900

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

def fetch_risk_history(conid):
    """Fetch and briefly cache one year of daily closes for a contract."""
    cached = _risk_history_cache.get(str(conid))
    if cached and time.monotonic() - cached["cached_at"] < _RISK_HISTORY_CACHE_SECONDS:
        return cached["bars"], None

    try:
        response = requests.get(
            f"{BASE_API_URL}/iserver/marketdata/history"
            f"?conid={conid}&period=1y&bar=1d",
            verify=False,
            timeout=15,
        )
        response.raise_for_status()
        history = response.json() if response.content else {}
        bars = history.get("data", []) if isinstance(history, dict) else []
        if not isinstance(bars, list):
            raise ValueError("IBKR returned an invalid daily-history response")
        _risk_history_cache[str(conid)] = {
            "cached_at": time.monotonic(),
            "bars": bars,
        }
        return bars, None
    except (requests.RequestException, ValueError) as error:
        return [], str(error)

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

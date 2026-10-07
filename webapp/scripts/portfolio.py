"""Portfolio, performance, and risk-management routes."""

from datetime import datetime, timezone

import requests
from flask import Blueprint, render_template

from portfolio_db import get_daily_performance_since
from risk_analysis import analyze_portfolio_risk
from scripts.common import (
    BASE_API_URL, _summary_amount, build_performance_chart_data, capture_portfolio_snapshots,
    fetch_benchmark_bars, fetch_commissions_by_conid, fetch_risk_history, fetch_subaccounts,
    get_active_account_id, get_spy_conid,
)

bp = Blueprint("portfolio", __name__)

@bp.route("/portfolio")
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

@bp.route("/risk-management")
def risk_management():
    active_account_id = get_active_account_id()
    accounts = fetch_subaccounts()
    selected_account_id = active_account_id
    analysis = None
    error = None
    history_errors = []

    try:
        summary_response = requests.get(
            f"{BASE_API_URL}/portfolio/{active_account_id}/summary",
            verify=False,
            timeout=15,
        )
        summary_response.raise_for_status()
        summary = summary_response.json() if summary_response.content else {}
        if not isinstance(summary, dict):
            raise ValueError("IBKR returned an invalid account-summary response")

        positions_response = requests.get(
            f"{BASE_API_URL}/portfolio/{active_account_id}/positions/0",
            verify=False,
            timeout=15,
        )
        positions_response.raise_for_status()
        positions = positions_response.json() if positions_response.content else []
        if not isinstance(positions, list):
            raise ValueError("IBKR returned an invalid positions response")

        benchmark_conid = get_spy_conid()
        benchmark_bars = []
        if benchmark_conid:
            benchmark_bars, benchmark_error = fetch_risk_history(benchmark_conid)
            if benchmark_error:
                history_errors.append(f"SPY benchmark: {benchmark_error}")
        else:
            history_errors.append("Could not resolve the SPY benchmark contract.")

        price_history = {}
        for position in positions:
            if not isinstance(position, dict) or not position.get("conid"):
                continue
            conid = str(position["conid"])
            bars, history_error = fetch_risk_history(conid)
            if history_error:
                symbol = position.get("ticker") or position.get("name") or conid
                history_errors.append(f"{symbol}: {history_error}")
            else:
                price_history[conid] = bars

        analysis = analyze_portfolio_risk(
            positions=positions,
            price_history=price_history,
            benchmark_bars=benchmark_bars,
            portfolio_value=_summary_amount(summary, "netliquidation"),
        )
    except (requests.RequestException, ValueError) as exception:
        error = str(exception)

    return render_template(
        "risk_management.html",
        analysis=analysis,
        error=error,
        history_errors=history_errors,
        accounts=accounts,
        selected_account_id=selected_account_id,
    )

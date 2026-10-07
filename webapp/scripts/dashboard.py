"""Dashboard and account-selection routes."""

import requests
from flask import Blueprint, redirect, render_template, request, session

from scripts.common import (
    BASE_API_URL, capture_portfolio_snapshots, fetch_subaccounts, get_active_account_id,
)

bp = Blueprint("dashboard", __name__)

@bp.route("/switch-account", methods=["GET"])
def switch_account():
    account_id = request.args.get('account_id')
    if account_id:
        session['account_id'] = account_id
    return redirect(request.referrer or '/')

@bp.route("/")
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

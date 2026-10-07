"""IBKR market-scanner route."""

import requests
from flask import Blueprint, render_template, request

from scripts.common import BASE_API_URL, fetch_subaccounts, get_active_account_id

bp = Blueprint("scanner", __name__)

@bp.route("/scanner")
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

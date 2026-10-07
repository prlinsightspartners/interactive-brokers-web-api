"""Contract search, details, and market-data routes."""

import requests
from flask import Blueprint, render_template, request

from scripts.common import BASE_API_URL, fetch_subaccounts, get_active_account_id

bp = Blueprint("contracts", __name__)

@bp.route("/lookup")
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

@bp.route("/contract/<contract_id>/<period>")
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

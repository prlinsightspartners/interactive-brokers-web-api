# Limit order generation

`submit_limit_order.py` validates a local, authenticated IBKR Client Portal
Gateway session, confirms the configured account is an available paper account,
resolves the requested stock contract on the selected exchange, and generates
a BUY limit-order payload. It only routes to paper accounts whose IDs start
with `DU`.

Without `--submit`, the script validates and prints the order preview without
sending it. To submit, set `IBKR_ACCOUNT_ID` to the configured paper account
and pass `--submit` explicitly:

```powershell
$env:IBKR_ACCOUNT_ID = "DU..."
python -m market_signals_engine.order_generation.submit_limit_order `
  --symbol MSFT `
  --quantity 1 `
  --limit-price 430 `
  --exchange NASDAQ `
  --time-in-force GTC `
  --submit
```

The script uses the locally hosted Gateway at `https://localhost:5055/v1/api`
by default. It disables certificate verification only for that localhost
connection, because the local Gateway uses a self-signed certificate. Broker
confirmation prompts are never accepted automatically; inspect the order and
warning, then confirm separately if appropriate.

## Order audit log

Transmitted orders and status checks are appended to
`logs/ibkr_orders.jsonl`. Each JSON Lines record includes the account/portfolio
ID, event time, order ID when available, order instructions, broker response,
and status or confirmation details. The submission intent is flushed to disk
before the request is sent. If the broker request times out, the log records an
unknown outcome; inspect IBKR and the log before retrying to avoid duplicate
orders. The log may contain account and order details, is excluded from Git, and
should be backed up with other private trading records.

Pass `--account-id` to select an available paper account explicitly. To refresh
and log a specific order's current status:

```powershell
python -m market_signals_engine.order_generation.submit_limit_order `
  --account-id DU... `
  --check-status `
  --order-id 123456789
```

The status refresh uses IBKR's current open-orders response. If the order is
absent, it records `UNKNOWN` rather than inferring that the order was canceled
or filled; check IBKR order history for final status.

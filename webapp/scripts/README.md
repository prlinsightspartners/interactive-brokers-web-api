# Flask workflow modules and trading helpers

## Flask application structure

`webapp/app.py` is now the Flask application setup module: it creates `app`,
initializes the existing SQLite databases, registers the shared `ctime`
template filter, and registers the route blueprints. Run the Flask app the same
way as before from the `webapp/` directory (`flask --app app run`); the public
URLs and route behavior are unchanged.

Each Flask workflow is grouped by responsibility in this package:

| Module | Responsibility | Routes |
| --- | --- | --- |
| `scripts/common.py` | Shared IBKR API configuration, selected-account and subaccount helpers, portfolio snapshot/history retrieval, webhook traffic logging, and account-value parsing | Shared by route modules |
| `scripts/webhooks.py` | TradingView webhook validation and traffic logging | `POST /webhook` |
| `scripts/dashboard.py` | Dashboard summary and selected-account switching | `GET /`, `GET /switch-account` |
| `scripts/contracts.py` | Contract search/details, market history, and stock fundamentals | `GET /lookup`, `GET /contract/<contract_id>/<period>` |
| `scripts/orders.py` | Live/history order retrieval, local order synchronization, order submission, warning confirmation, and cancellation | `GET /orders`, `POST /limit_order`, `POST /market_order`, `GET /orders/<order_id>/cancel` |
| `scripts/portfolio.py` | Current portfolio, performance chart data, portfolio snapshots, and risk-analysis page | `GET /portfolio`, `GET /risk-management` |
| `scripts/scanner.py` | IBKR scanner parameter loading and scan submission | `GET /scanner` |

The route modules expose Flask blueprints and import shared helpers from
`scripts.common`; `app.py` registers those blueprints. Keep route-specific
request handling in its workflow module and move helpers used by multiple
workflows into `common.py` (or a narrowly scoped helper module if the shared
logic grows). This keeps the startup module small and makes workflow ownership
clear without changing the Flask CLI entry point.

The numbered files (`01_...` through `06_...`) below are separate illustrative
signal-to-order helpers. They are not route modules and are not automatically
called by the Flask webhook or the blueprint routes.

## Standalone signal checks and IBKR execution

These six small Python modules describe stages of a signal-to-order workflow.
They are reusable building blocks, not a running pipeline: no coordinator
calls them in sequence. In particular, the FastAPI webhook in
`ibkr-algo-tradeapp/routers/trades.py` has its own order-handling path and
does not call these helpers. Adding or editing these files alone therefore
does not cause the checks below to run for live webhook orders.

## Intended flow

An application that integrates these helpers should process a signal in this
order:

1. **Receive and validate the signal.** Authenticate the sender, validate the
   required fields and normalize values such as `action`, `quantity`, and
   `asset_type`. These helpers do not implement webhook authentication or
   general payload validation.
2. **Identify and reserve it.** Use `DuplicateSignalChecker` to generate a
   stable ID, check whether that ID has already been recorded, and reserve it
   before doing more work. If it is already recorded, stop without placing an
   order. If reservation fails, stop as well; do not treat a failed reservation
   as permission to trade.
3. **Resolve the contract and gather context.** Use `ContractResolver` to find
   one matching IBKR contract. With that contract, obtain current market data,
   account values, and the current position from the relevant data sources.
   The helper modules do not fetch all of this context for one another.
4. **Run risk checks.** Pass the signal, market data, and account data to
   `RiskChecker`. Stop if it returns a rejected `RiskResult`.
5. **Check the projected position.** Pass the signal and current position to
   `PositionChecker`. Stop if it returns a rejected `PositionResult`.
6. **Construct the order.** Use `OrderBuilder` to turn the approved signal into
   an `ib_insync` order object.
7. **Submit and log.** Pass the resolved IBKR contract, order, and signal to
   `IBKRExecution`. It checks the connection and a few final order properties,
   submits with `placeOrder`, and writes a submission record to the database.

Every rejection or error before submission should end processing for that
signal. A successfully submitted order is not necessarily a filled order.

## Stage details

### Risk checks — `01_risk_checks.py`

`RiskChecker.check(signal, market_data, account)` applies the configured
`RiskLimits` and returns a `RiskResult` with `approved` and, on rejection, a
`reason`. It checks:

- Trading is enabled for the account.
- Quantity is positive and no greater than `max_quantity`.
- Estimated order notional (`market_data["price"] * quantity`) does not exceed
  `max_order_notional`.
- Market data is present and not marked `stale`.
- Bid and ask are present, and their spread as a percentage of the midpoint is
  within `max_bid_ask_spread_pct`.
- If the signal has a `limit_price`, it is within
  `max_price_deviation_pct` of `market_data["mid"]`.
- Account daily P&L has not passed the `max_daily_loss` loss threshold.
- Estimated cost does not exceed `account["available_funds"]`.

The defaults in `RiskLimits` are: `$100,000` maximum order notional, `$5,000`
maximum daily loss, `$250,000` maximum position notional, `1,000` maximum
quantity, `2%` maximum limit-price deviation, and `1%` maximum bid/ask spread.
`max_position_notional` is defined but is not currently applied by
`RiskChecker.check`; position size is separately checked by
`PositionChecker`. Supply a `RiskLimits` instance to change the applied limits.

The account mapping must provide `trading_enabled`, `daily_pnl`, and
`available_funds`. Market data is expected to provide `price`, `bid`, `ask`,
and, when checking a limit price, `mid`; `stale` may be used to flag stale
data. The checker evaluates the individual checks before returning the first
rejected result, so callers should supply valid market data rather than rely on
the `"No market data"` result to handle `None`.

### Position checks — `02_position_checks.py`

`PositionChecker.check(signal, current_position)` treats a missing current
position as zero, then calculates the projected position by adding quantity
for `BUY` or subtracting it for `SELL`. It rejects unsupported actions and
rejects a projected absolute position above `signal["max_position"]`, which
defaults to `1,000` when omitted. The caller is responsible for retrieving the
correct current position for the resolved contract and account.

### Duplicate signals — `03_duplicate_signals.py`

`DuplicateSignalChecker` makes a SHA-256 ID from the sorted JSON
representation of these signal fields: `strategy_id`, `symbol`, `action`,
`quantity`, and `timestamp`. Changes to any of those values produce a different
ID. The ID should be stored as `signal["signal_id"]` for later order
construction and execution logging.

`is_duplicate(signal_id)` looks for the ID in the `webhook_signals` table.
`reserve_signal (signal_id)` attempts to insert it with a UTC `received_at`
timestamp and status `RECEIVED`, then commits. The caller should check both
for an existing ID and for successful reservation before proceeding. The
reservation method returns `False` if its insert fails; that result does not
distinguish a duplicate from another database error. The check and reservation
are separate operations, so deployments should also enforce uniqueness for
`signal_id` in the database to protect against simultaneous duplicate
requests.

### Contract lookup — `04_contract_lookup.py`

`ContractResolver.resolve(signal)` creates an IBKR contract description based
on `asset_type` and asks IBKR for matching contract details:

- `STOCK`: `symbol`, `currency`, and `exchange` (defaults to `SMART`).
- `FUTURE`: `symbol`, `expiry`, `exchange`, and `currency`.
- `OPTION`: `symbol`, `exchange`, `currency`, and nested `option` fields
  `expiry`, `strike`, and `right`.
- `FOREX`: `symbol` passed to `Forex`.

Unsupported asset types, no matches, or multiple matches raise `ValueError`;
the helper deliberately requires exactly one match instead of guessing. It
returns the single item from `reqContractDetails`. The integration should use
the qualified contract carried by that result for subsequent market-data and
order calls, and ensure it supplies the contract object expected by
`IBKRExecution`.

### Order construction — `05_order_construction.py`

`OrderBuilder.build(signal)` creates an `ib_insync` order using `action`,
`quantity`, and `order_type`:

- `MARKET` creates a `MarketOrder`.
- `LIMIT` creates a `LimitOrder` using `limit_price`.
- `STOP` creates a `StopOrder` using `stop_price`.

The order defaults to time in force `DAY` and `outsideRth=False`, overridable
with `time_in_force` and `outside_rth`. Its `orderRef` is set to
`signal["signal_id"]`. An unsupported order type raises `ValueError`; required
fields for the chosen type must be present in the signal.

### IBKR execution — `06_ibkr_execution.py`

`IBKRExecution.execute(contract, order, signal)` first requires an active IBKR
connection, then verifies that the order quantity is positive, the contract
has a `conId`, and the action is `BUY` or `SELL`. It submits the order with
`ib.placeOrder(contract, order)`, writes the returned order ID, contract
symbol, action, quantity, order type, and current IBKR order status to the
`orders` table, commits, and returns the IBKR `Trade`.

This helper records a submission/current status; it does not wait for a fill,
monitor later status changes, retry, or cancel the order. If database logging
fails after `placeOrder`, the order may already have been submitted even
though execution raises while recording it. Callers should handle that
uncertain outcome by checking IBKR order state before retrying, to avoid
submitting the same trade twice.

## Expected signal and context

The exact signal shape depends on the instrument and order type. A typical
stock limit-order signal includes:

```python
signal = {
    "signal_id": "...",       # generated/reserved before execution
    "strategy_id": "strategy-1",
    "symbol": "AAPL",
    "asset_type": "STOCK",
    "currency": "USD",
    "action": "BUY",
    "quantity": 10,
    "order_type": "LIMIT",
    "limit_price": 150.00,
    "timestamp": "2026-10-06T15:00:00Z",
}
```

The integration must additionally supply account and market-data mappings to
the risk checker and the current position to the position checker. Contract
lookup requires the connection represented by the `ib` client to be usable.
These modules do not define a shared signal schema, fetch account/position/
market data, or coordinate database transactions across stages.
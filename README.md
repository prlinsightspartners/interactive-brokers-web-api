# Interactive Brokers Web API

This repository contains a dockerized Interactive Brokers (IBKR) trading toolkit with two separate application layers:

- A Flask dashboard that connects to the IBKR Client Portal Gateway and exposes portfolio, orders, contract lookup, scanner, and webhook-ingestion views.
- A FastAPI app that receives TradingView webhook alerts and places automated IBKR orders using `ib_async`.

The stack is designed to run alongside an IBKR Client Portal Gateway instance and is intended for paper trading and local strategy automation experiments.

## What this repo does

The project combines:

- IBKR Client Portal Gateway configuration via `conf.yaml`
- a local web application for account and ordering workflows
- a webhook-driven trade execution app for TradingView alerts
- SQLite trade logging for executed and tracked orders
- example scripts for risk checks, duplicate signal filtering, contract resolution, and order execution logic

## Repository layout

- `webapp/` — Flask dashboard application
  - `app.py` — main Flask app and routes
  - `trade_db.py` — SQLite trade log creation and upsert logic
  - `templates/` — HTML pages for dashboard, portfolio, orders, lookup, scanner, etc.
  - `static/` — CSS assets
  - `scripts/` — reusable risk and order modules
  - `data/` — logged webhook traffic JSONL files
- `ibkr-algo-tradeapp/` — FastAPI automation app
  - `main.py` — FastAPI startup and lifespan setup
  - `config.py` — IBKR account and allocation settings
  - `dependencies.py` — singleton app dependencies
  - `init_db.py` — SQLite trade logging for the FastAPI app
  - `routers/trades.py` — webhook handling and order execution
  - `routers/charts.py` — chart page and bar subscription code
- `Dockerfile` — container image for gateway + Python apps
- `docker-compose.yml` — local stack configuration
- `conf.yaml` — IBKR Gateway configuration
- `start.sh` — startup script for gateway + Flask app
- `scripts/` — standalone REST/API examples and helper scripts

## Architecture

The project runs the following services in the container:

1. IBKR Client Portal Gateway on the local Docker host
2. Flask app at port `5056`
3. FastAPI app at port `4002`
4. Client Portal Gateway public API at `https://localhost:5055/v1/api`

The Flask app is the operational dashboard, while the FastAPI app is the automated webhook execution layer.

## Requirements

- Docker Desktop
- An IBKR account with Client Portal Gateway access
- A working IBKR paper trading account or live account with the proper permissions
- Optional: a TradingView alert configured to POST JSON to the webhook endpoint

## Quick start

Clone the repository:

```bash
git clone https://github.com/hackingthemarkets/interactive-brokers-web-api.git
cd interactive-brokers-web-api
```

Start the stack:

```bash
docker-compose up --build
```

This starts the container and launches the gateway + web application. The container config also sets:

- `IBKR_ACCOUNT_ID=DUO504961`
- `WEBHOOK_SECRET=your_super_secret_string_123`

These values are placeholders that should be adjusted to your environment.

## Access points

After startup:

- Client Portal Gateway: `https://localhost:5055`
- Flask dashboard: `http://localhost:5056`
- FastAPI app: `http://localhost:4002`

The root dashboard is served from the Flask app and is designed to help you:

- view account summaries
- switch between IBKR accounts
- browse contract details and historical data
- view live orders and submitted trades
- use a contract lookup interface
- run market scanner configurations

## Flask app features

The main Flask app in `webapp/app.py` exposes several routes.

### Account and portfolio routes

- `/` — dashboard summary page
- `/portfolio` — positions and account portfolio view
- `/switch-account` — set the active account in session

### Contract and market data routes

- `/lookup` — search for security definitions by symbol
- `/contract/<contract_id>/<period>` — fetch market-data history and display contract details
- `/scanner` — run scanner queries against the IBKR gateway API

### Order routes

- `/orders` — fetch and display live orders
- `/limit_order` — submit limit orders
- `/market_order` — submit market orders
- `/orders/<order_id>/cancel` — cancel a live order

### Webhook route

- `/webhook` — receives TradingView JSON payloads, validates `secret`, validates order fields, and returns success/error results.

The app writes every received webhook to `webapp/data/webhook_traffic_YYYYMMDD.jsonl` for verification and replay.

## TradingView webhook flow

The payload expected by the Flask app is structured similarly to:

```json
{
  "secret": "your_super_secret_string_123",
  "symbol": "AAPL",
  "strategy": {
    "order_action": "BUY",
    "order_contracts": 10
  }
}
```

The app validates:

- `secret` matches `WEBHOOK_SECRET`
- `symbol` is present
- `order_action` is `BUY` or `SELL`
- `order_contracts` is a positive integer

If the payload is valid it returns a success response; otherwise it returns a 400/403/500 error.

## FastAPI trading app

The app under `ibkr-algo-tradeapp/` is a more direct automated trading service.

### Main startup

`ibkr-algo-tradeapp/main.py` creates the FastAPI app, mounts static files, and uses an async lifespan hook to:

- connect to the IBKR gateway
- resolve managed accounts
- initialize database and dependency injection
- disconnect cleanly on shutdown

### Trade webhook endpoint

The main endpoint lives in `ibkr-algo-tradeapp/routers/trades.py`:

- `POST /webhook`
- `POST /` (legacy alias)

Behavior:

1. Validates webhook secret
2. Parses symbol + quantity + direction
3. Builds a `Stock` contract against `SMART`/`USD`
4. Creates a `MarketOrder` with `DAY` time in force
5. Chooses account or FA allocation
6. Places the order through `ib_async`
7. Waits up to 30 seconds for fills
8. Rejects cancelled/rejected orders
9. Logs the completed trade into SQLite

The endpoint returns a JSON result like:

```json
{
  "status": "success",
  "symbol": "AAPL",
  "action": "BUY",
  "quantity": 10,
  "fill_price": 150.25
}
```

### Charting endpoint

`ibkr-algo-tradeapp/routers/charts.py` provides chart-related routes and uses the `ib_async` contract qualification flow to pull and display live OHLC data for a ticker.

## Database behavior

Two SQLite databases are present:

### `webapp/database/tradelog.db`

Created by `webapp/trade_db.py` and stores a `tradelog` table with:

- `account_id`
- `timestamp`
- `order_id`
- `ticker`
- `description`
- `company`
- `order_description`
- `order_type`
- `status`
- `action`
- `quantity`
- `price`

This table is updated with `upsert_trade()` so the latest state of each order is kept.

### `ibkr-algo-tradeapp/trades.db`

Created by `ibkr-algo-tradeapp/init_db.py` and stores a `trades` table with:

- `timestamp`
- `symbol`
- `action`
- `quantity`
- `price`

This is used for the trade log page and webhook-driven order history.

## Risk and execution helper scripts

The `webapp/scripts/` directory contains reusable logic for algorithmic execution workflows:

- `01_risk_checks.py` — `RiskChecker` and `RiskLimits` for validating order safety before execution
- `02_position_checks.py` — likely position evaluation logic
- `03_duplicate_signals.py` — deduplicating repeated webhook signals
- `04_contract_lookup.py` — contract resolution for symbols
- `05_order_construction.py` — `OrderBuilder` that turns a signal into a market/limit/stop order
- `06_ibkr_execution.py` — `IBKRExecution` wrapper that validates and submits order objects to IBKR

These scripts are examples of the broker-integration logic used by a larger algo trading workflow and are not all wired directly into the Flask app at runtime.

## Config and environment notes

### `conf.yaml`

This file configures the Client Portal Gateway and exposes the web API on port `5055` with SSL enabled and the `v1` API path. It also includes authentication delay and access control settings.

### `docker-compose.yml`

The container exposes:

- `5055` — IBKR API / gateway UI
- `5056` — Flask dashboard
- `4002` — FastAPI app
- `4040` — additional local port exposed by the gateway setup

### `Dockerfile`

The container image installs:

- Java runtime (`openjdk-17-jre-headless`)
- `ngrok`
- Python 3 and pip
- the IBKR Client Portal Gateway ZIP
- the app source directories (`webapp`, `ibkr-algo-tradeapp`, `scripts`)

## Important operational notes

- This repository assumes a local IBKR Client Portal Gateway is running and authenticated.
- The default webhook secret and account ID are convenience defaults and should be changed before production use.
- The Flask app disables certificate verification with `requests.packages.urllib3.disable_warnings(InsecureRequestWarning)` and `PYTHONHTTPSVERIFY=0`, which is common for local/self-signed gateway setups but should be adjusted for secure deployments.
- Live order submission depends on your IBKR permissions and correct account configuration.

## Typical usage flow

1. Start the container with Docker Compose.
2. Open the IBKR gateway and authenticate in the browser.
3. Access the Flask dashboard on port `5056`.
4. Confirm account information and portfolio summary.
5. Use the lookup, scanner, or order pages to work with market data and orders.
6. Configure a TradingView alert to POST JSON to the FastAPI or Flask webhook endpoint.
7. Monitor order fills and trade logs in SQLite.

## Example request to the fastapi webhook

```bash
curl -X POST http://localhost:4002/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "secret": "your_super_secret_string_123",
    "symbol": "AAPL",
    "strategy": {
      "order_action": "BUY",
      "order_contracts": 10
    }
  }'
```

## License

This project appears to be distributed under the MIT license, as indicated by the repo-level LICENSE file.

## Related references

- IBKR Client Portal Gateway documentation
- TradingView alert webhook payload patterns
- `ib_async` order placement APIs

## Summary

This repo is best understood as a local automation sandbox for IBKR trading workflows:

- it authenticates to the IBKR gateway,
- exposes a dashboard for market and order operations,
- captures webhook signals from TradingView,
- enforces simple validation and risk patterns,
- submits orders to IBKR,
- and logs both live and executed trades to SQLite.

If you are building or extending this project, the most important runtime entry points are:

- `webapp/app.py`
- `ibkr-algo-tradeapp/main.py`
- `ibkr-algo-tradeapp/routers/trades.py`
- `webapp/trade_db.py`
- `conf.yaml`



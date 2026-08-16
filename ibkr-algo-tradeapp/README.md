# IBKR TradingView Integration Platform

A FastAPI-based application that integrates Interactive Brokers (IBKR) with TradingView for automated trading. This platform allows you to receive webhook alerts from TradingView strategies, automatically execute trades on IBKR, view real-time charts, and maintain a complete trade log.

## Overview

This application provides a seamless bridge between TradingView's technical analysis signals and Interactive Brokers' trading execution. It features:

- **Real-time charting** with candlestick data from IBKR
- **Automated webhook-based trading** from TradingView alerts
- **Trade logging and history** tracking all executed trades
- **Account management** with support for single accounts and FA (Financial Advisor) allocations
- **Web-based dashboard** for monitoring and analysis

## Project Structure

### Core Application Files

#### [`main.py`](main.py)
**Purpose:** FastAPI application entry point and lifecycle management
- Initializes the FastAPI application instance
- Mounts static files (CSS, images, etc.) at `/static`
- Configures application lifespan management with `@asynccontextmanager`
- Establishes connection to IBKR Gateway on application startup
- Initializes database connection and dependency injection
- Includes all routers (charts and trades)
- Handles graceful shutdown of IBKR connection and database on application termination

**Key Features:**
- Async context manager for proper resource cleanup
- Dependency injection setup for IB instance and database
- Error handling for missing managed accounts

#### [`config.py`](config.py)
**Purpose:** Configuration management for IBKR connection and account settings
- `host`: IP address of IBKR Gateway (default: 127.0.0.1)
- `port`: Port number for IBKR Gateway connection (default: 4002)
- `clientId`: Unique client identifier for IB connection (default: 0)
- `ib_account`: Specific account to use for order routing (e.g., 'DUO504957' for paper trading)
- `ib_fa_group`: Financial Advisor group name (for multi-account management)
- `ib_fa_method`: Financial Advisor allocation method
- `ib_fa_profile`: Financial Advisor profile name

**Usage:** Modify these values to connect to your IBKR Gateway instance and specify trading account details.

#### [`dependencies.py`](dependencies.py)
**Purpose:** Dependency injection container for shared application resources
- Manages global instances of:
  - `IB`: Interactive Brokers connection instance
  - `sqlite3.Connection`: Database connection
  - `sqlite3.Cursor`: Database query cursor
- Provides getter functions for FastAPI dependency injection:
  - `get_ib()`: Returns the IB instance
  - `get_db_conn()`: Returns database connection
  - `get_db_cursor()`: Returns database cursor
- Setter functions to initialize dependencies:
  - `set_ib_instance(ib)`
  - `set_database_dependencies(conn, cursor)`
  - `setup_dependencies(ib, conn, cursor)`: Convenience function for setup

**Design Pattern:** Uses a singleton pattern to ensure single instances across the application.

#### [`init_db.py`](init_db.py)
**Purpose:** Database initialization and schema creation
- Creates SQLite database file (`trades.db`) if it doesn't exist
- Creates the `trades` table with the following schema:
  - `id`: Auto-incremented primary key
  - `timestamp`: Unix timestamp of trade execution
  - `symbol`: Trading symbol (e.g., 'AAPL')
  - `action`: Trade action ('BUY' or 'SELL')
  - `quantity`: Number of shares traded
  - `price`: Execution price
  - `created_at`: Database insertion timestamp (auto-set)
- Ensures database connectivity with `check_same_thread=False` for async operations

**Function:**
- `ensure_database(db_path="trades.db")`: Creates/connects to database and returns (connection, cursor)

#### [`tws_python_client.py`](tws_python_client.py)
**Purpose:** Legacy IBKR client implementation using ibapi (alternative to ib_async)
- Implements `TradeApp` class inheriting from both `EWrapper` and `EClient`
- Connects to TWS (Trader Workstation) instead of IB Gateway
- Handles account updates and position tracking
- Key methods:
  - `updateAccountValue()`: Receives account balance and equity updates
  - `position()`: Tracks open positions with symbol, quantity, and average cost
  - `positionEnd()`: Summarizes positions by asset class
- Can be used for background monitoring if needed
- Currently not integrated with the main FastAPI application

**Note:** This is a reference implementation; the main app uses `ib_async` for better async support.

#### [`requirements.txt`](requirements.txt)
**Purpose:** Python package dependencies
- `fastapi`: Web framework for building APIs
- `uvicorn[standard]`: ASGI server for running FastAPI
- `ib_async`: Async wrapper for Interactive Brokers API (primary IBKR connector)
- `sse-starlette`: Server-Sent Events implementation for real-time updates
- `jinja2`: Template engine for HTML rendering

### Router Modules

#### [`routers/charts.py`](routers/charts.py)
**Purpose:** Real-time charting endpoints for displaying market data
- **`ChartState` class**: Manages current chart state including:
  - `latest_tick`: Most recent price tick
  - `current_ticker`: Active ticker subscription
  - `current_minute`: Current minute data
  - `current_ohlc`: Current OHLC (Open, High, Low, Close) data

**Endpoints:**

1. **`GET /` (response_class=HTMLResponse)**
   - Serves the main charting page
   - Template: `index.html`
   - Renders interactive candlestick chart using Lightweight Charts library

2. **`POST /subscribe`**
   - Accepts JSON payload with `symbol` field
   - Subscribes to real-time 5-minute bar data for the symbol
   - Validates contract via IBKR's contract qualifier
   - Cancels previous subscription if one exists
   - Returns status and symbol confirmation
   - Typical request: `{"symbol": "AAPL"}`

3. **`GET /history`**
   - Query parameter: `symbol` (e.g., `/history?symbol=AAPL`)
   - Fetches historical 1-day of 1-minute candlestick data
   - Returns JSON array of OHLC bars with timestamps
   - Format: `[{"time": unix_timestamp, "open": float, "high": float, "low": float, "close": float}, ...]`
   - Uses regular trading hours (RTH) only

**Key Features:**
- Real-time bar subscription with event callbacks
- Contract qualification to ensure valid symbols
- Candlestick data in format compatible with Lightweight Charts
- Historical data retrieval for chart initialization

#### [`routers/trades.py`](routers/trades.py)
**Purpose:** Trade execution and logging endpoints, handles TradingView webhooks
- **`WEBHOOK_SECRET`**: Security token for webhook validation (default: 'your_super_secret_string_123')
- **`REJECTED_STATUSES`**: Set of order statuses indicating rejection/cancellation

**Helper Functions:**

1. **`_get_managed_accounts(ib: IB) -> list[str]`**
   - Retrieves list of accounts managed by IBKR connection
   - Handles both string and list return formats from IB

2. **`_resolve_order_account(ib: IB) -> str`**
   - Determines which account to use for order routing
   - Priority: configured `ib_account` → first managed account
   - Includes fallback logic if configured account not in managed accounts

3. **`_resolve_order_allocation(ib: IB, payload: dict) -> dict[str, str]`**
   - Complex allocation resolution with 6-step priority system:
     1. Webhook payload override (account, fa_group, fa_profile, fa_method)
     2. Configuration file account
     3. Configuration file FA settings
     4. First managed account from IB
   - Returns allocation dict for order submission

4. **`_trade_error_reason(trade) -> str`**
   - Extracts most useful error message from trade object
   - Searches trade log for meaningful error messages

5. **`_trade_was_rejected_or_cancelled(trade) -> bool`**
   - Checks if trade was rejected or cancelled
   - Examines both status and trade log entries

6. **`log_trade(symbol, action, quantity, price, cursor, conn)`**
   - Inserts executed trade into SQLite database
   - Records timestamp, symbol, action, quantity, price
   - Handles database commit and error logging

**Endpoints:**

1. **`GET /tradelog` (response_class=HTMLResponse)**
   - Displays trade history page
   - Queries all trades from database ordered by timestamp (DESC)
   - Template: `tradelog.html`
   - Shows formatted trade table with links to view charts

2. **`POST /webhook` or `POST /`**
   - **Purpose**: Receive and execute TradingView webhook alerts
   - **Security**: Validates webhook secret against configured value
   - **Processing Steps**:
     1. Validates webhook secret
     2. Parses trade details (symbol, action, quantity)
     3. Validates input (action in ['BUY', 'SELL'], quantity > 0)
     4. Creates IBKR Stock contract
     5. Creates MarketOrder with 'DAY' time-in-force
     6. Resolves account/allocation for order routing
     7. Places order via IBKR
     8. Waits up to 30 seconds for order fill
     9. Checks for rejection/cancellation
     10. Verifies fill was executed
     11. Logs successful trade to database

   - **Request Format** (TradingView Alert Message):
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

   - **Response on Success**:
     ```json
     {
       "status": "success",
       "symbol": "AAPL",
       "action": "BUY",
       "quantity": 10,
       "fill_price": 150.25
     }
     ```

   - **Error Handling**:
     - 403: Invalid webhook secret
     - 400: Invalid order parameters or broker rejection
     - 504: Order timeout (30+ seconds without fill)
     - 502: Order filled without execution details
     - 500: Unhandled internal error

**Key Features:**
- Multi-step order validation
- Account/FA allocation routing
- 30-second fill timeout with cancellation
- Complete error reporting and logging
- Database persistence of all executed trades

### Template Files (HTML/Jinja2)

#### [`templates/layout.html`](templates/layout.html)
**Purpose:** Master template providing consistent layout and navigation
- Defines base HTML structure with `{% block %}` tags for inheritance
- Navigation menu with links to:
  - `/`: Real-Time Chart
  - `/scanner`: Market Scanner (placeholder)
  - `/tradelog`: Trade Log
- Active link highlighting based on current URL path
- Head and content blocks for child templates to override

#### [`templates/index.html`](templates/index.html)
**Purpose:** Real-time charting interface
- Extends `layout.html`
- Imports Lightweight Charts JavaScript library from CDN
- **UI Controls**:
  - Symbol input field for entering stock tickers
  - "Load Chart" button to fetch and display data
  - Loading indicator during data fetch
- **Chart Features**:
  - Candlestick chart representation
  - Time scale with seconds visible
  - Right-side price scale
  - Responsive sizing based on container width
  - White background with grid lines
- **JavaScript Functionality**:
  - `subscribeSymbol()`: Posts symbol to `/subscribe` endpoint
  - Calls `/history` endpoint to load historical 1-day data
  - Updates chart with fetched OHLC data

#### [`templates/tradelog.html`](templates/tradelog.html)
**Purpose:** Trade history and execution tracking
- Extends `layout.html`
- **Content**:
  - Conditional rendering: shows table if trades exist, empty state otherwise
  - Trade table with columns:
    - Time (UTC): Formatted timestamp
    - Symbol: Clickable link to view chart for that symbol
    - Action: Color-coded BUY (green) or SELL (red) badge
    - Quantity: Number of shares
    - Fill Price: Execution price formatted to 2 decimals
  - Symbol links navigate to chart page with pre-loaded symbol
  - Empty state message when no trades recorded

### Static Assets

#### [`static/styles.css`](static/styles.css)
**Purpose:** Styling for dashboard, tables, and UI elements
- **Dashboard Container**: White background with rounded corners and shadow
- **Table Styling**:
  - Full width with clean borders
  - Alternating row hover effects
  - Header styling with uppercase font
  - Padding and spacing standards
- **Action Tags**: Color-coded BUY (green background) and SELL (red background) badges
- **Component Classes**: Reusable styles for charts, buttons, inputs, and indicators

## Setup Instructions

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Connection Settings
Edit `config.py` with your IBKR connection details:
- Set `ib_account` to your paper/live trading account number
- Modify host/port if using non-standard IBKR Gateway setup
- Configure FA settings if using advisor accounts

### 3. Run the Application
```bash
uvicorn main:app --reload
```

The application will:
- Start on `http://localhost:8000` by default
- Attempt to connect to IBKR Gateway at configured host:port
- Initialize SQLite database
- Serve web interface and API endpoints

### 4. Configure TradingView Webhook
In your TradingView strategy alert settings, set the webhook URL to:
```
https://your-domain.com/webhook
```

Use ngrok or similar for local tunneling, or deploy to a public server.

**Alert Message Format:**
```json
{
  "secret": "your_super_secret_string_123",
  "symbol": "{{ticker}}",
  "strategy": {
    "order_action": "{{strategy.order.action}}",
    "order_contracts": {{strategy.order.contracts}}
  }
}
```

## API Reference

### Real-Time Charts
- `GET /` - Serve charting interface
- `POST /subscribe` - Subscribe to real-time bars
- `GET /history?symbol=AAPL` - Fetch historical OHLC data

### Trade Execution & Logging
- `POST /webhook` - Execute trade from TradingView alert
- `GET /tradelog` - View trade history

## Database Schema

### trades table
```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,           -- Unix timestamp
    symbol TEXT NOT NULL,                 -- Trading symbol
    action TEXT NOT NULL,                 -- 'BUY' or 'SELL'
    quantity INTEGER NOT NULL,            -- Share quantity
    price REAL NOT NULL,                  -- Execution price
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP  -- Insertion time
)
```

## Security Considerations

⚠️ **Important Security Notes:**

1. **Change Webhook Secret**: Update `WEBHOOK_SECRET` in `routers/trades.py` to a unique, strong value
2. **Environment Variables**: Use `.env` file for sensitive configuration (account numbers, secrets)
3. **HTTPS Only**: Always use HTTPS (SSL/TLS) when deploying to production
4. **Account Protection**: Never commit actual account numbers or credentials to version control
5. **IB Gateway Security**: Keep IB Gateway running on localhost or secure network only
6. **Webhook URL**: Use rate limiting and IP whitelisting if possible

## Troubleshooting

### Cannot connect to IBKR Gateway
- Ensure IB Gateway (or TWS) is running
- Verify host and port in `config.py` match your setup
- Check that clientId is unique and not in use
- Verify network connectivity: `ping 127.0.0.1:4002`

### Trades not logging
- Check that `trades.db` file is created in project root
- Verify database file permissions allow read/write
- Check console output for database errors

### Webhooks not received
- Verify webhook URL is publicly accessible
- Check webhook secret matches configuration
- Review TradingView alert message format matches expected payload
- Check application logs for incoming requests

### Chart not loading
- Ensure symbol is valid and exists in IBKR database
- Verify IB connection is active
- Check browser console for JavaScript errors
- Try refreshing the page

## Future Enhancements

- [ ] Market scanner implementation
- [ ] Advanced portfolio analytics
- [ ] Multi-timeframe analysis
- [ ] Strategy backtesting
- [ ] Risk management features (stop-loss, position sizing)
- [ ] Real-time P&L tracking
- [ ] Trade notifications (email, SMS, Discord)
- [ ] Paper trading mode indicator
- [ ] Account statement downloads
- [ ] Performance metrics and reporting
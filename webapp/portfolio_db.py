import os
import sqlite3
import time

from trade_db import DATABASE_DIR

PORTFOLIO_DB_PATH = os.path.join(DATABASE_DIR, "portfolio_intelligence.db")

# Maps IBKR's raw assetClass codes to a human-readable asset class grouping
ASSET_CLASS_LABELS = {
    "STK": "Equity",
    "OPT": "Option",
    "FUT": "Future",
    "FOP": "Future Option",
    "CASH": "Forex",
    "BOND": "Bond",
    "FUND": "Fund",
    "CMDTY": "Commodity",
    "WAR": "Warrant",
    "IOPT": "Structured Product",
    "CFD": "CFD",
    "CRYPTO": "Crypto",
}


def ensure_portfolio_database(db_path=PORTFOLIO_DB_PATH):
    """Create the portfolio_intelligence database and its tables if they don't already exist."""
    print(f"Ensuring portfolio intelligence database exists at: {db_path}")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()

    # Latest known snapshot of each account's metadata / headline figures
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS account_metadata (
        account_id TEXT PRIMARY KEY,
        account_alias TEXT,
        account_type TEXT,
        business_type TEXT,
        currency TEXT,
        portfolio_value REAL,
        total_pnl REAL,
        realized_pnl REAL,
        unrealized_pnl REAL,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Time series of portfolio-level performance, one row per capture per account
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        portfolio_value REAL,
        total_pnl REAL,
        realized_pnl REAL,
        unrealized_pnl REAL,
        cash_balance REAL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Append-only holdings log; each capture writes one row per position so
    # prior rows remain as a historical record of what an account has held
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        conid TEXT NOT NULL,
        symbol TEXT,
        description TEXT,
        asset_class TEXT,
        instrument_type TEXT,
        currency TEXT,
        position REAL,
        avg_cost REAL,
        mkt_price REAL,
        mkt_value REAL,
        unrealized_pnl REAL,
        realized_pnl REAL,
        as_of INTEGER NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_perf_account_ts ON portfolio_performance(account_id, timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_holdings_account_asof ON holdings(account_id, as_of)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_holdings_conid ON holdings(account_id, conid)")

    print("Portfolio intelligence database is ready.")
    conn.commit()

    return conn, cursor


def upsert_account_metadata(
    account_id,
    account_alias="",
    account_type="",
    business_type="",
    currency="",
    portfolio_value=0,
    total_pnl=0,
    realized_pnl=0,
    unrealized_pnl=0,
    db_path=PORTFOLIO_DB_PATH,
):
    """Store the latest known metadata / headline figures for an account."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO account_metadata (
                account_id, account_alias, account_type, business_type, currency,
                portfolio_value, total_pnl, realized_pnl, unrealized_pnl, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id) DO UPDATE SET
                account_alias = excluded.account_alias,
                account_type = excluded.account_type,
                business_type = excluded.business_type,
                currency = excluded.currency,
                portfolio_value = excluded.portfolio_value,
                total_pnl = excluded.total_pnl,
                realized_pnl = excluded.realized_pnl,
                unrealized_pnl = excluded.unrealized_pnl,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                account_id,
                account_alias or "",
                account_type or "",
                business_type or "",
                currency or "",
                portfolio_value or 0,
                total_pnl or 0,
                realized_pnl or 0,
                unrealized_pnl or 0,
            ),
        )


def record_portfolio_performance(
    account_id,
    portfolio_value=0,
    total_pnl=0,
    realized_pnl=0,
    unrealized_pnl=0,
    cash_balance=0,
    db_path=PORTFOLIO_DB_PATH,
):
    """Append a performance snapshot row for an account's P&L trade book history."""
    timestamp = int(time.time() * 1000)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO portfolio_performance (
                account_id, timestamp, portfolio_value, total_pnl, realized_pnl, unrealized_pnl, cash_balance
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, timestamp, portfolio_value or 0, total_pnl or 0, realized_pnl or 0, unrealized_pnl or 0, cash_balance or 0),
        )


def record_holdings_snapshot(account_id, positions, db_path=PORTFOLIO_DB_PATH):
    """Append one row per held instrument so past holdings remain queryable history."""
    if not positions:
        return

    as_of = int(time.time() * 1000)
    rows = []
    for item in positions:
        asset_class_code = item.get("assetClass", "")
        rows.append((
            account_id,
            str(item.get("conid", "")),
            item.get("ticker") or item.get("name", ""),
            item.get("contractDesc", ""),
            ASSET_CLASS_LABELS.get(asset_class_code, asset_class_code),
            asset_class_code,
            item.get("currency", ""),
            item.get("position", 0),
            item.get("avgCost", 0),
            item.get("mktPrice", 0),
            item.get("mktValue", 0),
            item.get("unrealizedPnl", 0),
            item.get("realizedPnl", 0),
            as_of,
        ))

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO holdings (
                account_id, conid, symbol, description, asset_class, instrument_type,
                currency, position, avg_cost, mkt_price, mkt_value, unrealized_pnl, realized_pnl, as_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_daily_performance_since(account_id, since_ts, db_path=PORTFOLIO_DB_PATH):
    """Return the P&L tradebook history (portfolio_performance) as one point per
    calendar day, keeping the latest snapshot captured that day, ascending in time.
    Used to plot the portfolio value chart on the Portfolio page.
    """
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, portfolio_value
            FROM portfolio_performance
            WHERE account_id = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (account_id, since_ts),
        ).fetchall()

    daily = {}
    for timestamp, portfolio_value in rows:
        day_key = time.strftime("%Y-%m-%d", time.gmtime(timestamp / 1000))
        daily[day_key] = {"timestamp": timestamp, "portfolio_value": portfolio_value}

    return [daily[key] for key in sorted(daily.keys())]

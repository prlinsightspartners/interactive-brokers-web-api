import os
import sqlite3
import time


DATABASE_PATH = os.path.join(os.path.dirname(__file__), "tradelog.db")


def ensure_database(db_path=DATABASE_PATH):
    print(f"Ensuring database exists at: {db_path}")
    
    # Connect to the database. It will be created if it doesn't exist.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()
    
    # Create the 'tradelog' table if it doesn't already exist
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tradelog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        timestamp INTEGER NOT NULL,
        order_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        description TEXT NOT NULL,
        company TEXT NOT NULL,
        order_description TEXT NOT NULL,
        order_type TEXT NOT NULL,
        status TEXT NOT NULL,
        action TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    print("Database is ready.")
    conn.commit()
    
    return conn, cursor


def upsert_trade(
    account_id,
    order_id,
    ticker="",
    description="",
    company="",
    order_description="",
    order_type="",
    status="",
    action="",
    quantity=0,
    price=0,
):
    """Create or refresh the latest known state for an IBKR order."""
    timestamp = int(time.time() * 1000)
    values = (
        timestamp,
        ticker or "",
        description or "",
        company or "",
        order_description or "",
        order_type or "",
        status or "",
        action or "",
        quantity or 0,
        price or 0,
        account_id,
        str(order_id),
    )

    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE tradelog
            SET timestamp = ?, ticker = ?, description = ?, company = ?,
                order_description = ?, order_type = ?, status = ?, action = ?,
                quantity = ?, price = ?
            WHERE account_id = ? AND order_id = ?
            """,
            values,
        )

        if cursor.rowcount == 0:
            cursor.execute(
                """
                INSERT INTO tradelog (
                    account_id, timestamp, order_id, ticker, description, company,
                    order_description, order_type, status, action, quantity, price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    timestamp,
                    str(order_id),
                    ticker or "",
                    description or "",
                    company or "",
                    order_description or "",
                    order_type or "",
                    status or "",
                    action or "",
                    quantity or 0,
                    price or 0,
                ),
            )
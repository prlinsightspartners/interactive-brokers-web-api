import os
import json
import sqlite3
import time
import uuid


DATABASE_DIR = os.path.join(os.path.dirname(__file__), "database")
os.makedirs(DATABASE_DIR, exist_ok=True)
DATABASE_PATH = os.path.join(DATABASE_DIR, "tradelog.db")


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

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_order_id TEXT NOT NULL UNIQUE,
        account_id TEXT NOT NULL,
        ibkr_order_id TEXT,
        conid INTEGER,
        ticker TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        company TEXT NOT NULL DEFAULT '',
        order_type TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL DEFAULT '',
        quantity REAL NOT NULL DEFAULT 0,
        price REAL,
        time_in_force TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL,
        response_json TEXT,
        status TEXT NOT NULL,
        filled_quantity REAL NOT NULL DEFAULT 0,
        remaining_quantity REAL,
        average_fill_price REAL,
        error_message TEXT,
        http_status INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS order_status_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_record_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        filled_quantity REAL NOT NULL DEFAULT 0,
        remaining_quantity REAL,
        average_fill_price REAL,
        details_json TEXT,
        observed_at INTEGER NOT NULL,
        FOREIGN KEY (order_record_id) REFERENCES orders(id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_account_status ON orders(account_id, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_account_created ON orders(account_id, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_ibkr_id ON orders(account_id, ibkr_order_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_status_history_record ON order_status_history(order_record_id, observed_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tradelog_account_created ON tradelog(account_id, created_at)")
    
    print("Database is ready.")
    conn.commit()
    
    return conn, cursor


def _order_response_object(response_json):
    if isinstance(response_json, list):
        return next((item for item in response_json if isinstance(item, dict)), {})
    return response_json if isinstance(response_json, dict) else {}


def _canonical_order_status(value, fallback="PendingSubmit"):
    if not value:
        return fallback
    normalized = "".join(character for character in str(value).lower() if character.isalnum())
    known_statuses = {
        "pendingsubmit": "PendingSubmit",
        "presubmitted": "PreSubmitted",
        "submitted": "Submitted",
        "partiallyfilled": "PartiallyFilled",
        "filled": "Filled",
        "pendingcancel": "PendingCancel",
        "cancelled": "Cancelled",
        "canceled": "Cancelled",
        "apicancelled": "ApiCancelled",
        "apicanceled": "ApiCancelled",
        "rejected": "Rejected",
        "inactive": "Inactive",
        "submissionfailed": "SubmissionFailed",
    }
    return known_statuses.get(normalized, str(value))


def _response_number(response, keys, fallback=None):
    for key in keys:
        value = response.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return fallback


def create_order_record(account_id, payload, ticker="", description="", company="", db_path=DATABASE_PATH):
    """Persist an order attempt before it is sent to IBKR."""
    submitted_order = (payload.get("orders") or [{}])[0]
    timestamp = int(time.time() * 1000)
    client_order_id = uuid.uuid4().hex
    values = (
        client_order_id,
        account_id,
        submitted_order.get("conid"),
        ticker or "",
        description or "",
        company or "",
        submitted_order.get("orderType", ""),
        submitted_order.get("side", ""),
        submitted_order.get("quantity", 0),
        submitted_order.get("price"),
        submitted_order.get("tif", ""),
        json.dumps(payload, sort_keys=True, default=str),
        "PendingSubmit",
        timestamp,
        timestamp,
    )
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO orders (
                client_order_id, account_id, conid, ticker, description, company,
                order_type, action, quantity, price, time_in_force, payload_json,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        order_record_id = cursor.lastrowid
        conn.execute(
            """
            INSERT INTO order_status_history (order_record_id, status, details_json, observed_at)
            VALUES (?, 'PendingSubmit', ?, ?)
            """,
            (order_record_id, json.dumps({"event": "submission_started"}), timestamp),
        )
    return order_record_id


def update_order_record(
    order_record_id,
    response_json=None,
    status=None,
    error_message=None,
    http_status=None,
    db_path=DATABASE_PATH,
):
    """Save the latest IBKR response and append a history row for each observed change."""
    response = _order_response_object(response_json)
    timestamp = int(time.time() * 1000)
    serialized_response = json.dumps(response_json, sort_keys=True, default=str) if response_json is not None else None

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        current = conn.execute("SELECT * FROM orders WHERE id = ?", (order_record_id,)).fetchone()
        if current is None:
            return

        response_status = next(
            (response.get(key) for key in ("order_status", "orderStatus", "status") if response.get(key)),
            None,
        )
        next_status = _canonical_order_status(status or response_status, current["status"])
        if not status and not response_status and response.get("order_id", response.get("orderId")):
            next_status = _canonical_order_status("Submitted", current["status"])

        order_id = response.get("order_id") or response.get("orderId") or response.get("id")
        filled_quantity = _response_number(
            response,
            ("filledQuantity", "filled", "filled_qty", "cumQty"),
            current["filled_quantity"],
        )
        remaining_quantity = _response_number(
            response,
            ("remainingQuantity", "remaining", "remaining_qty"),
            current["remaining_quantity"],
        )
        average_fill_price = _response_number(
            response,
            ("averageFillPrice", "avgFillPrice", "avgPrice", "average_price"),
            current["average_fill_price"],
        )
        next_error = error_message if error_message is not None else current["error_message"]

        conn.execute(
            """
            UPDATE orders
            SET ibkr_order_id = COALESCE(?, ibkr_order_id), response_json = COALESCE(?, response_json),
                status = ?, filled_quantity = ?, remaining_quantity = ?, average_fill_price = ?,
                error_message = ?, http_status = COALESCE(?, http_status), updated_at = ?
            WHERE id = ?
            """,
            (
                str(order_id) if order_id is not None else None,
                serialized_response,
                next_status,
                filled_quantity,
                remaining_quantity,
                average_fill_price,
                next_error,
                http_status,
                timestamp,
                order_record_id,
            ),
        )

        changed = (
            next_status != current["status"]
            or filled_quantity != current["filled_quantity"]
            or remaining_quantity != current["remaining_quantity"]
            or average_fill_price != current["average_fill_price"]
            or next_error != current["error_message"]
        )
        if changed:
            conn.execute(
                """
                INSERT INTO order_status_history (
                    order_record_id, status, filled_quantity, remaining_quantity,
                    average_fill_price, details_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_record_id,
                    next_status,
                    filled_quantity,
                    remaining_quantity,
                    average_fill_price,
                    serialized_response or json.dumps({"error": next_error}),
                    timestamp,
                ),
            )


def update_order_record_by_ibkr_id(
    account_id,
    ibkr_order_id,
    response_json,
    status=None,
    error_message=None,
    http_status=None,
    db_path=DATABASE_PATH,
):
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM orders WHERE account_id = ? AND ibkr_order_id = ?",
            (account_id, str(ibkr_order_id)),
        ).fetchall()
    for (order_record_id,) in rows:
        update_order_record(
            order_record_id,
            response_json=response_json,
            status=status,
            error_message=error_message,
            http_status=http_status,
            db_path=db_path,
        )


def get_trackable_orders(account_id, db_path=DATABASE_PATH):
    """Return submitted orders whose final state has not yet been observed."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, ibkr_order_id, status
            FROM orders
            WHERE account_id = ? AND ibkr_order_id IS NOT NULL
                AND status IN ('PendingSubmit', 'PreSubmitted', 'Submitted', 'PartiallyFilled', 'PendingCancel')
            ORDER BY updated_at ASC
            LIMIT 100
            """,
            (account_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_order_history(account_id, days=30, db_path=DATABASE_PATH):
    """Return locally tracked orders for one account within the requested age window."""
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 30
    since_timestamp = int((time.time() - days * 24 * 60 * 60) * 1000)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM orders
            WHERE account_id = ? AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            """,
            (account_id, since_timestamp),
        ).fetchall()
        orders = [dict(row) for row in rows]
        known_order_ids = {
            str(order["ibkr_order_id"])
            for order in orders
            if order["ibkr_order_id"] is not None
        }
        legacy_rows = conn.execute(
            """
            SELECT id, order_id, ticker, description, company, order_description,
                   order_type, status, action, quantity, price,
                   CAST(strftime('%s', created_at) AS INTEGER) * 1000 AS created_at_ms
            FROM tradelog
            WHERE account_id = ? AND created_at >= datetime('now', ?)
            ORDER BY created_at DESC, id DESC
            """,
            (account_id, f"-{days} days"),
        ).fetchall()

    for row in legacy_rows:
        if str(row["order_id"]) in known_order_ids:
            continue
        orders.append({
            "id": f"legacy-{row['id']}",
            "ibkr_order_id": str(row["order_id"]),
            "conid": None,
            "ticker": row["ticker"],
            "description": row["description"],
            "company": row["company"],
            "order_type": row["order_type"],
            "action": row["action"],
            "quantity": row["quantity"],
            "price": row["price"] if row["price"] else None,
            "status": row["status"],
            "filled_quantity": 0,
            "remaining_quantity": None,
            "average_fill_price": None,
            "error_message": None,
            "created_at": row["created_at_ms"] or 0,
        })

    return sorted(orders, key=lambda order: order["created_at"], reverse=True)


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
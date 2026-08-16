import sqlite3

def ensure_database(db_path="trades.db"):
    print(f"Ensuring database exists at: {db_path}")
    
    # Connect to the database. It will be created if it doesn't exist.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()
    
    # Create the 'trades' table if it doesn't already exist
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp INTEGER NOT NULL,
        symbol TEXT NOT NULL,
        action TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    print("Database is ready.")
    conn.commit()
    
    return conn, cursor
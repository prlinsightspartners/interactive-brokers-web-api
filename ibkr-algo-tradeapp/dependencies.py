import sqlite3
from typing import Optional
from ib_async import IB

# Global instances for dependencies
_ib_instance: Optional[IB] = None
_db_conn: Optional[sqlite3.Connection] = None
_db_cursor: Optional[sqlite3.Cursor] = None

# --- IB Dependency ---
def get_ib() -> IB:
    if _ib_instance is None:
        raise RuntimeError("IB instance not initialized")
    return _ib_instance

def set_ib_instance(ib: IB):
    global _ib_instance
    _ib_instance = ib

# --- Database Dependencies (NEW) ---
def get_db_conn() -> sqlite3.Connection:
    if _db_conn is None:
        raise RuntimeError("Database connection not initialized")
    return _db_conn

def get_db_cursor() -> sqlite3.Cursor:
    if _db_cursor is None:
        raise RuntimeError("Database cursor not initialized")
    return _db_cursor

def set_database_dependencies(conn: sqlite3.Connection, cursor: sqlite3.Cursor):
    global _db_conn, _db_cursor
    _db_conn = conn
    _db_cursor = cursor

# --- Combined Setup Function (NEW) ---
def setup_dependencies(ib: IB, conn: sqlite3.Connection, cursor: sqlite3.Cursor):
    """A convenience function to set up all dependencies at once."""
    set_ib_instance(ib)
    set_database_dependencies(conn, cursor)
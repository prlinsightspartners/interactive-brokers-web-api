from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from ib_async import IB
from contextlib import asynccontextmanager

# Import all components
from routers import charts, trades # , scanner
from init_db import ensure_database
import dependencies
import config

# Initialize database and get connection/cursor
conn, cursor = ensure_database("trades.db")

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")

# Include all three routers
app.include_router(charts.router)
# app.include_router(scanner.router)
app.include_router(trades.router) # <-- Add trades router

ib = IB()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages application lifecycle."""
    try:
        await ib.connectAsync(config.host, config.port, clientId=config.clientId)
        print("Connected to IB Gateway")
        try:
            managed_accounts = ib.managedAccounts()
            print(f"Managed accounts: {managed_accounts}")
        except Exception:
            print("Could not retrieve managed accounts from IB Gateway")
        
        # Set up all dependencies (IB and Database) for injection
        dependencies.setup_dependencies(ib, conn, cursor)
        
        yield
        
    finally:
        print("Disconnecting from IB")
        ib.disconnect()
        conn.close() # Close database connection on shutdown
        print("Database connection closed")

app.router.lifespan_context = lifespan
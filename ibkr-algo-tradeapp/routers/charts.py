from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ib_async import IB, Stock, RealTimeBar

from dependencies import get_ib

import math, json, asyncio

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Chart state management
class ChartState:
    def __init__(self):
        self.latest_tick = None
        self.current_ticker = None
        self.current_minute = None
        self.current_ohlc = None

# Global chart state instance
chart_state = ChartState()


@router.post("/subscribe")
async def subscribe(request: Request, ib: IB = Depends(get_ib)):
    global chart_state
    
    data = await request.json()
    symbol = data.get("symbol", "").upper()
    print(f"Subscribing to {symbol}")

    contract = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        return {"status": "error", "message": f"Could not qualify {symbol}"}

    # Cancel existing subscription
    if chart_state.current_ticker:
        ib.cancelRealTimeBars(chart_state.current_ticker)

    # Start new subscription
    chart_state.current_ticker = ib.reqRealTimeBars(
        qualified[0],
        barSize=5,
        whatToShow="TRADES",
        useRTH=True
    )
    

    def on_bar(bars: list[RealTimeBar], hasNewBar: bool):
        print(bars)

    chart_state.current_ticker.updateEvent += on_bar
    
    return {"status": "ok", "symbol": symbol}


@router.get("/", response_class=HTMLResponse)
async def get_index(request: Request):
    print("rendering index page")
    """Serves the main charting page."""
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/history")
async def get_history(symbol: str, ib: IB = Depends(get_ib)):
    """Fetches 1 day of 1-minute historical bars for a symbol."""
    contract = Stock(symbol.upper(), "SMART", "USD")
    
    # Ask IBKR to find the specific contract details
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        return []

    # Request the historical data
    bars = await ib.reqHistoricalDataAsync(
        qualified[0],
        endDateTime='',
        durationStr='1 D',
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=True,  # Use Regular Trading Hours
        formatDate=1  # Return timestamps in UTC seconds
    )

    # Format the data into a JSON structure our chart can understand
    return [
        {
            "time": int(bar.date.timestamp()),
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close
        }
        for bar in bars
    ]
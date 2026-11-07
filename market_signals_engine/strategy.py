from .factors import FactorSnapshot, calculate_factors
from .models import Bar, Signal


MINIMUM_HISTORY = 20
ENTRY_BAND_ATR_FRACTION = 0.25


def evaluate_symbol(bars: list[Bar]) -> Signal:
    latest = bars[-1]
    if len(bars) < MINIMUM_HISTORY:
        return Signal(
            symbol=latest.symbol,
            as_of_date=latest.date,
            action="HOLD",
            reference_price=latest.close,
            entry_range=None,
            factors=None,
            reasons=(f"Insufficient history: need {MINIMUM_HISTORY} daily bars.",),
        )

    factors = calculate_factors(bars)
    action, reasons = _sample_technical_rule(latest, factors)
    entry_range = _entry_range(latest.close, factors, action)
    return Signal(
        symbol=latest.symbol,
        as_of_date=latest.date,
        action=action,
        reference_price=latest.close,
        entry_range=entry_range,
        factors=factors.to_dict(),
        reasons=reasons,
    )


def _sample_technical_rule(
    latest: Bar, factors: FactorSnapshot
) -> tuple[str, tuple[str, ...]]:
    if latest.close < factors.sma_20:
        return "SELL", ("Close is below the 20-session simple moving average.",)
    if factors.rsi_14 >= 80:
        return "SELL", ("14-session RSI is at or above the sample 80 threshold.",)
    if latest.close > factors.sma_20 and factors.rsi_14 <= 70:
        return "BUY", ("Close is above the 20-session simple moving average and RSI is at or below 70.",)
    return "HOLD", ("Sample technical rules produced no buy or sell signal.",)


def _entry_range(
    close: float, factors: FactorSnapshot, action: str
) -> dict[str, float] | None:
    band = factors.atr_14 * ENTRY_BAND_ATR_FRACTION
    if action == "BUY":
        return {"low": round(max(0.01, close - band), 4), "high": round(close, 4)}
    if action == "SELL":
        return {"low": round(close, 4), "high": round(close + band, 4)}
    return None

from collections.abc import Sequence

from .scoring_common import IndicatorRow, average, number, relative_score


def calculate_trend_score(rows: Sequence[IndicatorRow]) -> float | None:
    if not rows:
        return None
    latest = rows[-1]
    close = number(latest, "Close")
    ema50 = number(latest, "EMA50")
    ema200 = number(latest, "EMA200")
    components: list[float] = []
    if close and ema50:
        components.append(relative_score(close / ema50 - 1))
    if ema50 and ema200:
        components.append(relative_score(ema50 / ema200 - 1))
    if len(rows) >= 21:
        old_ema50 = number(rows[-21], "EMA50")
        if ema50 and old_ema50:
            components.append(relative_score(ema50 / old_ema50 - 1, scale=20))
    if components:
        return average(components)

    trend = str(latest.get("Trend", "")).strip().lower()
    if trend == "bullish":
        return 50.0
    if trend == "bearish":
        return -50.0
    return None

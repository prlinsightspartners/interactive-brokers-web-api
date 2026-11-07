from collections.abc import Sequence

from .scoring_common import IndicatorRow, average, clamp, number, relative_score


def calculate_momentum_score(rows: Sequence[IndicatorRow]) -> float | None:
    if not rows:
        return None
    latest = rows[-1]
    components: list[float] = []

    rsi = number(latest, "RSI")
    if rsi is not None:
        components.append(clamp((rsi - 50) * 2, -100, 100))

    close = number(latest, "Close")
    macd_histogram = number(latest, "MACD_Histogram")
    if close and macd_histogram is not None:
        components.append(relative_score(macd_histogram / close, scale=100))

    for column in ("alpha_mom_01", "alpha_mom_02", "alpha_tech_01"):
        rank = number(latest, column)
        if rank is not None:
            components.append(clamp((rank - 0.5) * 200, -100, 100))

    return average(components)

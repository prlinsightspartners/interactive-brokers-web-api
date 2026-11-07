from collections.abc import Sequence

from .scoring_common import IndicatorRow


VOLATILITY_QUALITY = {
    "low": 80.0,
    "medium": 100.0,
    "high": 70.0,
    "extreme": 25.0,
}


def calculate_volatility_score(rows: Sequence[IndicatorRow]) -> float | None:
    if not rows:
        return None
    signal = str(rows[-1].get("Vol_Signal", "")).strip().lower()
    return VOLATILITY_QUALITY.get(signal)
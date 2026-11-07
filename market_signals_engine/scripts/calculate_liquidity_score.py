from collections.abc import Sequence

from .scoring_common import IndicatorRow, average, clamp, number


def calculate_liquidity_score(rows: Sequence[IndicatorRow]) -> float | None:
    if not rows:
        return None
    latest = rows[-1]
    ranks = [
        number(latest, column)
        for column in ("alpha_liq_02", "alpha_liq_03")
    ]
    available = [clamp(rank, 0, 1) * 100 for rank in ranks if rank is not None]
    return average(available)
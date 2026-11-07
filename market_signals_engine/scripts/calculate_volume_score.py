from collections.abc import Sequence
import math

from .scoring_common import IndicatorRow, clamp, number


def calculate_volume_score(rows: Sequence[IndicatorRow]) -> float | None:
    if len(rows) < 2:
        return None
    latest_volume = number(rows[-1], "Volume")
    latest_close = number(rows[-1], "Close")
    previous_close = number(rows[-2], "Close")
    if (
        latest_volume is None
        or latest_close is None
        or previous_close is None
        or previous_close <= 0
    ):
        return None

    historical_volumes = [
        volume
        for row in rows[-21:-1]
        if (volume := number(row, "Volume")) is not None and volume > 0
    ]
    if not historical_volumes:
        return None
    average_volume = sum(historical_volumes) / len(historical_volumes)
    if average_volume <= 0:
        return None

    relative_volume = latest_volume / average_volume
    return_direction = math.tanh((latest_close / previous_close - 1) * 50)
    participation = clamp(relative_volume / 2, 0, 1)
    return 100 * return_direction * participation
import math
from collections.abc import Mapping, Sequence
from typing import TypeAlias


IndicatorRow: TypeAlias = Mapping[str, object]


def number(row: IndicatorRow, column: str) -> float | None:
    value = row.get(column)
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def average(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def relative_score(value: float, scale: float = 50.0) -> float:
    return 100 * math.tanh(value * scale)

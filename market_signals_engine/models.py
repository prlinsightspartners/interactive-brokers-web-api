from dataclasses import dataclass
from datetime import date
import math


@dataclass(frozen=True)
class Bar:
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must not be empty")
        values = (self.open, self.high, self.low, self.close, self.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"{self.symbol} {self.date}: bar values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError(f"{self.symbol} {self.date}: prices must be positive")
        if self.volume < 0:
            raise ValueError(f"{self.symbol} {self.date}: volume must not be negative")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError(f"{self.symbol} {self.date}: high is below another price")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError(f"{self.symbol} {self.date}: low is above another price")


@dataclass(frozen=True)
class Signal:
    symbol: str
    as_of_date: date
    action: str
    reference_price: float
    entry_range: dict[str, float] | None
    factors: dict[str, float] | None
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "as_of_date": self.as_of_date.isoformat(),
            "action": self.action,
            "reference_price": self.reference_price,
            "entry_range": self.entry_range,
            "factors": self.factors,
            "reasons": list(self.reasons),
        }

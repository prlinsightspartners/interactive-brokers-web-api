from dataclasses import dataclass

from .models import Bar


@dataclass(frozen=True)
class FactorSnapshot:
    sma_20: float
    rsi_14: float
    atr_14: float

    def to_dict(self) -> dict[str, float]:
        return {
            "sma_20": round(self.sma_20, 4),
            "rsi_14": round(self.rsi_14, 4),
            "atr_14": round(self.atr_14, 4),
        }


def calculate_factors(bars: list[Bar]) -> FactorSnapshot:
    if len(bars) < 20:
        raise ValueError("at least 20 daily bars are required")

    sma_20 = sum(bar.close for bar in bars[-20:]) / 20
    changes = [
        bars[index].close - bars[index - 1].close
        for index in range(len(bars) - 14, len(bars))
    ]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains) / 14
    average_loss = sum(losses) / 14
    if average_loss == 0:
        rsi_14 = 50.0 if average_gain == 0 else 100.0
    else:
        relative_strength = average_gain / average_loss
        rsi_14 = 100.0 - (100.0 / (1.0 + relative_strength))

    true_ranges = []
    for index in range(len(bars) - 14, len(bars)):
        bar = bars[index]
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    atr_14 = sum(true_ranges) / 14
    return FactorSnapshot(sma_20=sma_20, rsi_14=rsi_14, atr_14=atr_14)

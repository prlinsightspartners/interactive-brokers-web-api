import csv
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from market_signals_engine.data import load_bars
from market_signals_engine.factors import calculate_factors
from market_signals_engine.cli import generate_signals
from market_signals_engine.models import Bar
from market_signals_engine.strategy import evaluate_symbol


def make_bars(count: int, close: float = 100.0) -> list[Bar]:
    start = date(2026, 1, 1)
    return [
        Bar(
            symbol="TEST",
            date=start + timedelta(days=index),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=1000,
        )
        for index in range(count)
    ]


class EngineTests(unittest.TestCase):
    def test_flat_prices_have_neutral_rsi(self) -> None:
        factors = calculate_factors(make_bars(20))
        self.assertEqual(factors.rsi_14, 50)
        self.assertEqual(factors.sma_20, 100)

    def test_insufficient_history_produces_hold(self) -> None:
        signal = evaluate_symbol(make_bars(5))
        self.assertEqual(signal.action, "HOLD")
        self.assertIsNone(signal.entry_range)

    def test_upward_trend_produces_buy_proposal(self) -> None:
        start = date(2026, 1, 1)
        prices = [100.0]
        for index in range(1, 20):
            prices.append(prices[-1] + (0.3 if index % 2 == 0 else -0.2))
        bars = [
            Bar(
                symbol="TEST",
                date=start + timedelta(days=index),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000,
            )
            for index, price in enumerate(prices)
        ]
        signal = evaluate_symbol(bars)
        self.assertEqual(signal.action, "BUY")
        self.assertAlmostEqual(signal.entry_range["high"], signal.reference_price)

    def test_price_below_average_produces_sell_proposal(self) -> None:
        bars = make_bars(20)
        latest = bars[-1]
        bars[-1] = Bar(
            symbol=latest.symbol,
            date=latest.date,
            open=98,
            high=99,
            low=97,
            close=98,
            volume=latest.volume,
        )
        signal = evaluate_symbol(bars)
        self.assertEqual(signal.action, "SELL")
        self.assertLess(signal.entry_range["low"], signal.entry_range["high"])

    def test_csv_validation_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_path = root / "daily_bars.csv"
            with input_path.open("w", newline="", encoding="utf-8") as source:
                writer = csv.writer(source)
                writer.writerow(("symbol", "date", "open", "high", "low", "close", "volume"))
                for bar in make_bars(20):
                    writer.writerow(
                        (bar.symbol, bar.date, bar.open, bar.high, bar.low, bar.close, bar.volume)
                    )

            self.assertIn("TEST", load_bars(input_path))
            output_path = generate_signals(input_path, root / "output")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["signals"][0]["action"], "HOLD")

    def test_duplicate_symbol_date_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_path = Path(temporary_directory) / "daily_bars.csv"
            input_path.write_text(
                "symbol,date,open,high,low,close,volume\n"
                "TEST,2026-01-01,10,11,9,10,100\n"
                "TEST,2026-01-01,10,11,9,10,100\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate bar"):
                load_bars(input_path)


if __name__ == "__main__":
    unittest.main()

import csv
from datetime import date, timedelta
import logging
from pathlib import Path
import tempfile
import unittest

from market_signals_engine.scripts.calculate_directional_scores import (
    OUTPUT_COLUMNS,
    _classify,
    _weighted_directional_score,
    generate_directional_scores,
)


def write_indicator_file(path: Path, ticker: str, closes: list[float]) -> None:
    columns = (
        "Ticker",
        "Date",
        "Close",
        "EMA50",
        "EMA200",
        "RSI",
        "MACD_Histogram",
        "alpha_mom_01",
        "alpha_mom_02",
        "alpha_tech_01",
        "alpha_liq_02",
        "alpha_liq_03",
        "Volume",
        "Vol_Signal",
        "TD_Signal",
        "TD_Countdown_Direction",
        "TD_Buy_Countdown",
        "TD_Sell_Countdown",
        "TD_Setup_Direction",
        "TD_Setup_Count",
    )
    start = date(2026, 10, 1)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for index, close in enumerate(closes):
            writer.writerow(
                {
                    "Ticker": ticker,
                    "Date": (start + timedelta(days=index)).isoformat(),
                    "Close": close,
                    "EMA50": close * 0.99,
                    "EMA200": close * 0.97,
                    "RSI": 60,
                    "MACD_Histogram": 1,
                    "alpha_mom_01": 0.8,
                    "alpha_mom_02": 0.7,
                    "alpha_tech_01": 0.65,
                    "alpha_liq_02": 0.8,
                    "alpha_liq_03": 0.6,
                    "Volume": 1000 + index * 100,
                    "Vol_Signal": "Medium",
                    "TD_Signal": "Neutral",
                    "TD_Countdown_Direction": "Neutral",
                    "TD_Buy_Countdown": 0,
                    "TD_Sell_Countdown": 0,
                    "TD_Setup_Direction": "Bullish Setup",
                    "TD_Setup_Count": 6,
                }
            )


class DirectionalScoreTests(unittest.TestCase):
    def close_log_handlers(self) -> None:
        logger = logging.getLogger("market_signals_engine.directional_scores")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_classification_boundaries(self) -> None:
        self.assertEqual(_classify(80), "Strong Bullish")
        self.assertEqual(_classify(79.99), "Bullish")
        self.assertEqual(_classify(60), "Bullish")
        self.assertEqual(_classify(20), "Moderately Bullish")
        self.assertEqual(_classify(0), "Neutral")
        self.assertEqual(_classify(-20), "Moderately Bearish")
        self.assertEqual(_classify(-60), "Bearish")
        self.assertEqual(_classify(-80), "Strong Bearish")
        self.assertEqual(_classify(None), "Insufficient Data")

    def test_directional_score_renormalizes_available_directional_weights(self) -> None:
        score, coverage = _weighted_directional_score(
            {
                "Trend_Score": 100,
                "Momentum_Score": 0,
                "Volume_Score": None,
                "Price_Structure_Demark_Score": None,
                "Liquidity_Score": 0,
                "Volatility_Score": 0,
            }
        )
        self.assertAlmostEqual(score, 50)
        self.assertAlmostEqual(coverage, 60)

    def test_generates_one_dated_file_and_score_log_for_all_tickers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                root = Path(temporary_directory)
                indicator_dir = root / "indicators"
                scores_dir = root / "data" / "scores"
                indicator_dir.mkdir()
                write_indicator_file(
                    indicator_dir / "daily_price_indicators_AAA.csv",
                    "AAA",
                    [100, 102, 104],
                )
                write_indicator_file(
                    indicator_dir / "daily_price_indicators_BBB.csv",
                    "BBB",
                    [100, 98, 96],
                )

                output_path = generate_directional_scores(
                    indicator_dir, scores_dir, date(2026, 11, 6)
                )

                self.assertEqual(
                    output_path.name, "daily_directional_scores_2026-11-06.csv"
                )
                with output_path.open(newline="", encoding="utf-8") as source:
                    reader = csv.DictReader(source)
                    self.assertEqual(reader.fieldnames, list(OUTPUT_COLUMNS))
                    rows = list(reader)
                self.assertEqual({row["Ticker"] for row in rows}, {"AAA", "BBB"})
                for row in rows:
                    score = float(row["Directional_Score"])
                    self.assertGreaterEqual(score, -100)
                    self.assertLessEqual(score, 100)
                    self.assertEqual(row["Directional_Weight_Coverage_Pct"], "100.0")
                    self.assertIn(
                        row["Classification"],
                        {
                            "Strong Bullish",
                            "Bullish",
                            "Moderately Bullish",
                            "Neutral",
                            "Moderately Bearish",
                            "Bearish",
                            "Strong Bearish",
                        },
                    )
                self.assertTrue((scores_dir / "daily_directional_scores.log").is_file())
            finally:
                self.close_log_handlers()


if __name__ == "__main__":
    unittest.main()

import csv
from datetime import date, timedelta
import logging
from pathlib import Path
import tempfile
import unittest

from market_signals_engine.scripts.daily_price_indicators import (
    OUTPUT_COLUMNS,
    run_batch,
)


def write_price_csv(path: Path, row_count: int = 40, ticker: str = "TEST") -> date:
    last_date = date(2026, 2, 1)
    with path.open("w", newline="", encoding="utf-8") as source:
        writer = csv.writer(source)
        writer.writerow(
            ("Date", "Ticker", "Open", "High", "Low", "Close", "Adj Close", "Volume")
        )
        for index in range(row_count):
            current_date = last_date - timedelta(days=row_count - index - 1)
            close = 100 + index * 0.5
            writer.writerow(
                (
                    current_date.isoformat(),
                    ticker,
                    close,
                    close + 1,
                    close - 1,
                    close,
                    close,
                    1000 + index,
                )
            )
    return last_date


class DailyPriceIndicatorTests(unittest.TestCase):
    def close_log_handlers(self) -> None:
        logger = logging.getLogger("market_signals_engine.daily_price_indicators")
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_batch_reads_local_history_writes_indicators_and_logs_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                root = Path(temporary_directory)
                source_dir = root / "prices"
                output_dir = root / "indicators"
                source_dir.mkdir()
                last_date = write_price_csv(source_dir / "TEST.csv")
                log_file = root / "daily_price_indicators.log"

                summaries = run_batch(
                    source_dir,
                    output_dir,
                    date(2026, 2, 28),
                    log_file,
                )

                self.assertEqual(summaries, {"TEST": (last_date, 40)})
                output_path = output_dir / "daily_price_indicators_TEST.csv"
                with output_path.open(newline="", encoding="utf-8") as output:
                    reader = csv.DictReader(output)
                    self.assertEqual(reader.fieldnames, OUTPUT_COLUMNS)
                    rows = list(reader)
                self.assertEqual(len(rows), 40)
                self.assertEqual(rows[-1]["Date"], last_date.isoformat())
                self.assertEqual(rows[-1]["Ticker"], "TEST")
                self.assertTrue(rows[-1]["EMA50"])
                self.assertIn(
                    "Indicator run completed: tickers=1",
                    log_file.read_text(encoding="utf-8"),
                )
            finally:
                self.close_log_handlers()

    def test_future_source_rows_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                root = Path(temporary_directory)
                source_dir = root / "prices"
                source_dir.mkdir()
                last_date = write_price_csv(source_dir / "TEST.csv")

                summaries = run_batch(
                    source_dir,
                    root / "indicators",
                    last_date - timedelta(days=1),
                    root / "run.log",
                )

                self.assertEqual(summaries["TEST"], (last_date - timedelta(days=1), 39))
            finally:
                self.close_log_handlers()

    def test_caret_prefixed_index_symbols_use_source_filename_for_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                root = Path(temporary_directory)
                source_dir = root / "prices"
                source_dir.mkdir()
                write_price_csv(source_dir / "DJI.csv", ticker="^DJI")

                run_batch(
                    source_dir,
                    root / "indicators",
                    date(2026, 2, 28),
                    root / "run.log",
                )

                output_path = root / "indicators" / "daily_price_indicators_DJI.csv"
                with output_path.open(newline="", encoding="utf-8") as output:
                    rows = list(csv.DictReader(output))
                self.assertEqual(rows[-1]["Ticker"], "^DJI")
            finally:
                self.close_log_handlers()

    def test_zero_open_is_blank_and_logged_as_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                root = Path(temporary_directory)
                source_dir = root / "prices"
                source_dir.mkdir()
                (source_dir / "WBD.csv").write_text(
                    "Date,Ticker,Open,High,Low,Close,Volume\n"
                    "2026-10-05,WBD,0,30.99,30.94,30.95,40\n",
                    encoding="utf-8",
                )
                log_file = root / "run.log"

                run_batch(source_dir, root / "indicators", date(2026, 10, 5), log_file)

                self.assertTrue(
                    (root / "indicators" / "daily_price_indicators_WBD.csv").is_file()
                )
                self.assertIn("open is zero", log_file.read_text(encoding="utf-8"))
                with (root / "indicators" / "daily_price_indicators_WBD.csv").open(
                    newline="", encoding="utf-8"
                ) as output:
                    row = next(csv.DictReader(output))
                self.assertEqual(row["Open"], "")
            finally:
                self.close_log_handlers()

    def test_inconsistent_ohlc_bar_is_skipped_and_logged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            try:
                root = Path(temporary_directory)
                source_dir = root / "prices"
                source_dir.mkdir()
                (source_dir / "TEST.csv").write_text(
                    "Date,Ticker,Open,High,Low,Close,Volume\n"
                    "2026-01-01,TEST,10,11,9,10,1\n"
                    "2026-01-02,TEST,10,9,8,9,1\n"
                    "2026-01-03,TEST,10,11,9,10,1\n",
                    encoding="utf-8",
                )
                log_file = root / "run.log"

                summaries = run_batch(source_dir, root / "indicators", date(2026, 1, 3), log_file)

                self.assertEqual(summaries["TEST"], (date(2026, 1, 3), 2))
                self.assertIn(
                    "skipping bar with inconsistent high/low values",
                    log_file.read_text(encoding="utf-8"),
                )
            finally:
                self.close_log_handlers()


if __name__ == "__main__":
    unittest.main()

from datetime import date
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from market_signals_engine.tracking import log_daily_signals_run


class TrackingTests(unittest.TestCase):
    def test_tracking_is_skipped_when_no_uri_is_configured(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(
                log_daily_signals_run(
                    Path("prices"),
                    {},
                    Path("scores.csv"),
                    date(2026, 11, 12),
                )
            )

    def test_daily_run_logs_manifest_metrics_and_score_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_dir = root / "prices"
            source_dir.mkdir()
            source_file = source_dir / "AAPL.csv"
            source_file.write_text("price history", encoding="utf-8")
            score_file = root / "scores.csv"
            score_file.write_text("Ticker,Score\nAAPL,1\n", encoding="utf-8")

            active_run = MagicMock()
            active_run.info.run_id = "run-123"
            mlflow = MagicMock()
            mlflow.start_run.return_value.__enter__.return_value = active_run
            summaries = {
                "AAPL": (date(2026, 11, 12), 100),
                "MSFT": (date(2026, 11, 11), 90),
            }

            with (
                patch.dict(
                    os.environ,
                    {
                        "MLFLOW_TRACKING_URI": "http://mlflow:5000",
                        "MLFLOW_EXPERIMENT_NAME": "test-experiment",
                    },
                    clear=True,
                ),
                patch.dict("sys.modules", {"mlflow": mlflow}),
            ):
                run_id = log_daily_signals_run(
                    source_dir,
                    summaries,
                    score_file,
                    date(2026, 11, 12),
                )

            self.assertEqual(run_id, "run-123")
            mlflow.set_tracking_uri.assert_called_once_with("http://mlflow:5000")
            mlflow.set_experiment.assert_called_once_with("test-experiment")
            mlflow.log_metrics.assert_called_once_with(
                {
                    "ticker_count": 2,
                    "indicator_row_count": 190,
                    "tickers_with_run_date_data": 1,
                    "tickers_with_stale_data": 1,
                }
            )
            mlflow.log_artifact.assert_called_once_with(
                str(score_file), artifact_path="scores"
            )
            manifest = mlflow.log_dict.call_args.args[0]
            self.assertEqual(manifest["latest_indicator_date"], "2026-11-12")
            self.assertEqual(
                manifest["sources"][0]["sha256"],
                hashlib.sha256(b"price history").hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()

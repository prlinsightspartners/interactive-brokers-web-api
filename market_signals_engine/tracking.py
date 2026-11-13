from datetime import date
import hashlib
import os
from pathlib import Path


def log_daily_signals_run(
    source_dir: Path,
    summaries: dict[str, tuple[date, int]],
    score_file: Path,
    run_date: date,
) -> str | None:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        return None

    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(
        os.environ.get("MLFLOW_EXPERIMENT_NAME", "daily-market-signals")
    )

    source_manifest = []
    for source_file in sorted(source_dir.glob("*.csv")):
        ticker = source_file.stem.upper()
        if ticker not in summaries:
            ticker = f"^{ticker}"
        latest_date, row_count = summaries[ticker]
        source_manifest.append(
            {
                "file": source_file.name,
                "sha256": _sha256(source_file),
                "ticker": ticker,
                "latest_indicator_date": latest_date.isoformat(),
                "indicator_rows": row_count,
            }
        )

    latest_dates = [latest for latest, _ in summaries.values()]
    tickers_on_run_date = sum(
        latest == run_date for latest, _ in summaries.values()
    )
    run_name = f"daily-market-signals-{run_date.isoformat()}"
    with mlflow.start_run(run_name=run_name) as run:
        mlflow.set_tags(
            {
                "pipeline": "daily-market-signals",
                "run_date": run_date.isoformat(),
            }
        )
        mlflow.log_params(
            {
                "run_date": run_date.isoformat(),
                "source_file_count": len(source_manifest),
                "source_directory": str(source_dir),
            }
        )
        mlflow.log_metrics(
            {
                "ticker_count": len(summaries),
                "indicator_row_count": sum(
                    row_count for _, row_count in summaries.values()
                ),
                "tickers_with_run_date_data": tickers_on_run_date,
                "tickers_with_stale_data": len(summaries) - tickers_on_run_date,
            }
        )
        mlflow.log_dict(
            {
                "run_date": run_date.isoformat(),
                "latest_indicator_date": max(latest_dates).isoformat(),
                "sources": source_manifest,
            },
            "input_manifest.json",
        )
        mlflow.log_artifact(str(score_file), artifact_path="scores")
    return run.info.run_id


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

import argparse
import csv
from datetime import date, datetime, timezone
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from .calculate_liquidity_score import calculate_liquidity_score
from .calculate_momentum_score import calculate_momentum_score
from .calculate_price_structure_demark_score import (
    calculate_price_structure_demark_score,
)
from .calculate_trend_score import calculate_trend_score
from .calculate_volatility_score import calculate_volatility_score
from .calculate_volume_score import calculate_volume_score
from .scoring_common import IndicatorRow, number


ENGINE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INDICATOR_DIR = Path(
    os.environ.get("INDICATOR_OUTPUT_DIR", str(ENGINE_DIR / "data" / "indicators"))
)
DEFAULT_SCORES_DIR = Path(
    os.environ.get("DIRECTIONAL_SCORES_DIR", str(ENGINE_DIR / "data" / "scores"))
)
SCORE_LOG_NAME = "daily_directional_scores.log"
SCORE_WEIGHTS = {
    "Trend_Score": 0.30,
    "Momentum_Score": 0.30,
    "Volume_Score": 0.20,
    "Price_Structure_Demark_Score": 0.20,
}
OUTPUT_COLUMNS = (
    "Run_Date",
    "Ticker",
    "Indicator_Date",
    "Close",
    "Trend_Score",
    "Momentum_Score",
    "Liquidity_Score",
    "Volume_Score",
    "Volatility_Score",
    "Price_Structure_Demark_Score",
    "Directional_Score",
    "Classification",
    "Directional_Weight_Coverage_Pct",
)
LOGGER = logging.getLogger("market_signals_engine.directional_scores")


def _configure_logging(scores_dir: Path) -> logging.Logger:
    scores_dir.mkdir(parents=True, exist_ok=True)
    log_path = (scores_dir / SCORE_LOG_NAME).resolve()
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for handler in LOGGER.handlers[:]:
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) != log_path:
            LOGGER.removeHandler(handler)
            handler.close()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z",
    )
    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename) == log_path
        for handler in LOGGER.handlers
    ):
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
    if not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in LOGGER.handlers
    ):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        LOGGER.addHandler(stream_handler)
    return LOGGER


def _read_indicator_file(path: Path) -> tuple[str, list[IndicatorRow]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {"Ticker", "Date", "Close"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} is missing required columns: {sorted(required)}")
        rows: list[IndicatorRow] = []
        seen_dates: set[date] = set()
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{path}:{line_number}: extra CSV fields")
            try:
                ticker = (row["Ticker"] or "").strip().upper()
                row_date = date.fromisoformat((row["Date"] or "").strip())
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(
                    f"{path}:{line_number}: invalid ticker or date"
                ) from error
            if not ticker:
                raise ValueError(f"{path}:{line_number}: missing ticker")
            if row_date in seen_dates:
                raise ValueError(f"{path}:{line_number}: duplicate date {row_date}")
            seen_dates.add(row_date)
            row["Ticker"] = ticker
            row["Date"] = row_date.isoformat()
            rows.append(row)

    if not rows:
        raise ValueError(f"{path} contains no indicator rows")
    tickers = {str(row["Ticker"]) for row in rows}
    if len(tickers) != 1:
        raise ValueError(f"{path} must contain exactly one ticker")
    rows.sort(key=lambda row: str(row["Date"]))
    return next(iter(tickers)), rows


def _classify(score: float | None) -> str:
    if score is None:
        return "Insufficient Data"
    if score >= 80:
        return "Strong Bullish"
    if score >= 60:
        return "Bullish"
    if score >= 20:
        return "Moderately Bullish"
    if score > -20:
        return "Neutral"
    if score > -60:
        return "Moderately Bearish"
    if score > -80:
        return "Bearish"
    return "Strong Bearish"


def _weighted_directional_score(
    scores: dict[str, float | None],
) -> tuple[float | None, float]:
    available = [
        (scores[column], weight)
        for column, weight in SCORE_WEIGHTS.items()
        if scores[column] is not None
    ]
    coverage = sum(weight for _, weight in available)
    if coverage == 0:
        return None, 0.0
    aggregate = sum(value * weight for value, weight in available) / coverage
    return max(-100.0, min(100.0, aggregate)), coverage * 100


def _score_ticker(
    ticker: str, rows: list[IndicatorRow], run_date: date
) -> dict[str, Any]:
    latest = rows[-1]
    scores = {
        "Trend_Score": calculate_trend_score(rows),
        "Momentum_Score": calculate_momentum_score(rows),
        "Liquidity_Score": calculate_liquidity_score(rows),
        "Volume_Score": calculate_volume_score(rows),
        "Volatility_Score": calculate_volatility_score(rows),
        "Price_Structure_Demark_Score": calculate_price_structure_demark_score(
            rows
        ),
    }
    directional, coverage = _weighted_directional_score(scores)
    return {
        "Run_Date": run_date.isoformat(),
        "Ticker": ticker,
        "Indicator_Date": latest["Date"],
        "Close": number(latest, "Close"),
        **{
            name: None if value is None else round(value, 2)
            for name, value in scores.items()
        },
        "Directional_Score": None if directional is None else round(directional, 2),
        "Classification": _classify(directional),
        "Directional_Weight_Coverage_Pct": round(coverage, 2),
    }


def generate_directional_scores(
    indicator_dir: Path,
    scores_dir: Path,
    run_date: date | None = None,
) -> Path:
    run_date = run_date or date.today()
    logger = _configure_logging(scores_dir)
    started_at = datetime.now(timezone.utc)
    logger.info(
        "Directional scoring run started: indicator_dir=%s run_date=%s",
        indicator_dir,
        run_date,
    )
    try:
        indicator_paths = sorted(indicator_dir.glob("daily_price_indicators_*.csv"))
        if not indicator_paths:
            raise FileNotFoundError(
                f"No daily_price_indicators_*.csv files found in {indicator_dir}"
            )

        score_rows = []
        seen_tickers: set[str] = set()
        for path in indicator_paths:
            ticker, rows = _read_indicator_file(path)
            if ticker in seen_tickers:
                raise ValueError(f"Multiple indicator files found for {ticker}")
            seen_tickers.add(ticker)
            score_rows.append(_score_ticker(ticker, rows, run_date))

        output_path = scores_dir / f"daily_directional_scores_{run_date.isoformat()}.csv"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=scores_dir,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                writer = csv.DictWriter(output, fieldnames=OUTPUT_COLUMNS)
                writer.writeheader()
                writer.writerows(score_rows)
            os.replace(temporary_path, output_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    except Exception:
        logger.exception("Directional scoring run failed")
        raise

    classifications: dict[str, int] = {}
    for row in score_rows:
        label = row["Classification"]
        classifications[label] = classifications.get(label, 0) + 1
    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        "Directional scoring run completed: tickers=%s output=%s classifications=%s elapsed_seconds=%.2f",
        len(score_rows),
        output_path,
        classifications,
        elapsed,
    )
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate one weighted daily directional score CSV for all tickers."
    )
    parser.add_argument("--indicator-dir", type=Path, default=DEFAULT_INDICATOR_DIR)
    parser.add_argument("--scores-dir", type=Path, default=DEFAULT_SCORES_DIR)
    parser.add_argument("--run-date", type=date.fromisoformat, default=date.today())
    arguments = parser.parse_args()
    print(
        generate_directional_scores(
            arguments.indicator_dir,
            arguments.scores_dir,
            arguments.run_date,
        )
    )


if __name__ == "__main__":
    main()

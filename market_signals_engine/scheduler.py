from datetime import datetime, time, timedelta
import os
from pathlib import Path
import time as time_module
from zoneinfo import ZoneInfo

from .scripts.daily_price_indicators import (
    DEFAULT_LOG_FILE,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SOURCE_DIR,
    configure_logging,
    run_batch,
)
from .scripts.calculate_directional_scores import generate_directional_scores
from .tracking import log_daily_signals_run


def main() -> None:
    source_dir = Path(os.environ.get("INDICATOR_SOURCE_DIR", str(DEFAULT_SOURCE_DIR)))
    output_dir = Path(os.environ.get("INDICATOR_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR)))
    log_file = Path(os.environ.get("INDICATOR_LOG_FILE", str(DEFAULT_LOG_FILE)))
    scores_dir = Path(os.environ.get("DIRECTIONAL_SCORES_DIR", "/data/scores"))
    timezone_name = os.environ.get("INDICATOR_TIMEZONE", "America/New_York")
    run_at = _parse_time(os.environ.get("INDICATOR_RUN_TIME", "18:00"))
    zone = ZoneInfo(timezone_name)
    logger = configure_logging(log_file)
    startup_time = datetime.now(zone)
    startup_schedule = datetime.combine(startup_time.date(), run_at, tzinfo=zone)
    last_attempted_date = (
        startup_time.date() if startup_time >= startup_schedule else None
    )

    logger.info(
        "Daily indicator scheduler started: source=%s output=%s run_time=%s timezone=%s",
        source_dir,
        output_dir,
        run_at,
        timezone_name,
    )
    if last_attempted_date is not None:
        logger.info(
            "Started after today's scheduled time; next run is tomorrow at %s",
            run_at,
        )
    while True:
        now = datetime.now(zone)
        scheduled_run = datetime.combine(now.date(), run_at, tzinfo=zone)
        if now < scheduled_run:
            time_module.sleep((scheduled_run - now).total_seconds())
            continue
        if last_attempted_date == now.date():
            next_run = scheduled_run + timedelta(days=1)
            time_module.sleep(max(1, (next_run - now).total_seconds()))
            continue

        last_attempted_date = now.date()
        try:
            summaries = run_batch(source_dir, output_dir, now.date(), log_file)
            score_file = generate_directional_scores(
                output_dir, scores_dir, now.date()
            )
        except Exception as error:
            logger.error("Scheduled indicator run failed: %s", error)
            continue

        try:
            run_id = log_daily_signals_run(
                source_dir, summaries, score_file, now.date()
            )
            if run_id is not None:
                logger.info("Daily market-signals run logged to MLflow: %s", run_id)
        except Exception:
            logger.exception(
                "Could not log daily market-signals run to MLflow; "
                "indicator and score files remain available locally"
            )


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"INDICATOR_RUN_TIME must use HH:MM format, got {value!r}") from error


if __name__ == "__main__":
    main()

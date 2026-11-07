import argparse
import csv
import logging
import math
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .alpha_factor_generation import ALPHA_COLUMNS, calculate_alpha_factors


ENGINE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ENGINE_DIR / "data"
DEFAULT_SOURCE_DIR = Path(
    os.environ.get(
        "INDICATOR_SOURCE_DIR",
        r"C:\Github\options-chain-downloader\data\prices\csv_backups",
    )
)
DEFAULT_OUTPUT_DIR = Path(
    os.environ.get(
        "INDICATOR_OUTPUT_DIR",
        r"C:\Github\market_signals_engine\daily_price_indicators",
    )
)
DEFAULT_LOG_FILE = Path(
    os.environ.get(
        "INDICATOR_LOG_FILE",
        str(DATA_DIR / "daily_price_indicators.log"),
    )
)
LOGGER = logging.getLogger("market_signals_engine.daily_price_indicators")
FILE_PREFIX = "daily_price_indicators_"
OUTPUT_COLUMNS = [
    "Ticker",
    "Date",
    "Open",
    "High",
    "Low",
    "Close",
    "EMA50",
    "EMA200",
    "Raw_Diff",
    "EMA_Diff",
    "EMA_Diff_Delta",
    "Sigma2_Threshold",
    "EMA_Spread_Trend",
    "MACD",
    "MACD_Signal",
    "MACD_Histogram",
    "MACD_Crossover",
    "RSI",
    "RSI_Signal",
    "ATR",
    "Relative_ATR",
    "Vol_Signal",
    "Condition",
    "Trend",
    "MACD Direction",
    "Volume",
    "TD_Setup_Count",
    "TD_Setup_Direction",
    "TD_Setup_Perfected",
    "TD_Buy_Countdown",
    "TD_Sell_Countdown",
    "TD_Countdown_Direction",
    "TD_Countdown_Complete",
    "TD_Signal",
    "TD_Signal_Source",
    *ALPHA_COLUMNS,
]
PRICE_COLUMNS = ("Open", "High", "Low", "Close")


def _ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _rolling_mean(values: list[float | None], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for index in range(window - 1, len(values)):
        current = values[index - window + 1 : index + 1]
        if all(value is not None for value in current):
            result[index] = sum(value for value in current if value is not None) / window
    return result


def _rolling_std(values: list[float | None], window: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for index in range(window - 1, len(values)):
        current = values[index - window + 1 : index + 1]
        if all(value is not None for value in current):
            numbers = [value for value in current if value is not None]
            average = sum(numbers) / window
            result[index] = math.sqrt(
                sum((value - average) ** 2 for value in numbers) / (window - 1)
            )
    return result


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _calculate_demark_sequential(
    high: list[float], low: list[float], close: list[float]
) -> list[dict[str, Any]]:
    """Calculate classic close-vs-four setup and low/high-vs-two countdowns."""
    result = []
    setup_direction = 0
    setup_count = 0
    buy_countdown = 0
    sell_countdown = 0
    buy_countdown_active = False
    sell_countdown_active = False
    buy_countdown_eighth_close = None
    sell_countdown_eighth_close = None

    for index, price in enumerate(close):
        previous_setup_count = setup_count
        previous_setup_direction = setup_direction
        if index >= 4 and price < close[index - 4]:
            setup_direction = 1
            setup_count = (
                min(previous_setup_count + 1, 9)
                if previous_setup_direction == 1
                else 1
            )
        elif index >= 4 and price > close[index - 4]:
            setup_direction = -1
            setup_count = (
                min(previous_setup_count + 1, 9)
                if previous_setup_direction == -1
                else 1
            )
        else:
            setup_direction = 0
            setup_count = 0

        setup_completed = setup_count == 9 and previous_setup_count == 8
        setup_perfected = False
        if setup_completed and index >= 8:
            if setup_direction == 1:
                setup_perfected = any(
                    low[setup_bar] <= low[index - 3]
                    and low[setup_bar] <= low[index - 2]
                    for setup_bar in (index - 1, index)
                )
            else:
                setup_perfected = any(
                    high[setup_bar] >= high[index - 3]
                    and high[setup_bar] >= high[index - 2]
                    for setup_bar in (index - 1, index)
                )

        countdown_started = False
        if setup_completed:
            if setup_direction == 1:
                sell_countdown = 0
                sell_countdown_active = False
                sell_countdown_eighth_close = None
                if buy_countdown == 13 or not buy_countdown_active:
                    buy_countdown = 0
                    buy_countdown_active = True
                    buy_countdown_eighth_close = None
                    countdown_started = True
            else:
                buy_countdown = 0
                buy_countdown_active = False
                buy_countdown_eighth_close = None
                if sell_countdown == 13 or not sell_countdown_active:
                    sell_countdown = 0
                    sell_countdown_active = True
                    sell_countdown_eighth_close = None
                    countdown_started = True

        countdown_completed = 0
        if index >= 2 and not countdown_started:
            if buy_countdown_active and buy_countdown < 13 and price <= low[index - 2]:
                next_count = buy_countdown + 1
                if next_count == 8:
                    buy_countdown_eighth_close = price
                if next_count < 13:
                    buy_countdown = next_count
                elif (
                    buy_countdown_eighth_close is not None
                    and price <= buy_countdown_eighth_close
                ):
                    buy_countdown = 13
                    buy_countdown_active = False
                    countdown_completed = 1
            if sell_countdown_active and sell_countdown < 13 and price >= high[index - 2]:
                next_count = sell_countdown + 1
                if next_count == 8:
                    sell_countdown_eighth_close = price
                if next_count < 13:
                    sell_countdown = next_count
                elif (
                    sell_countdown_eighth_close is not None
                    and price >= sell_countdown_eighth_close
                ):
                    sell_countdown = 13
                    sell_countdown_active = False
                    countdown_completed = -1

        if buy_countdown == 13:
            countdown_direction = "Bullish Countdown Complete"
        elif sell_countdown == 13:
            countdown_direction = "Bearish Countdown Complete"
        elif buy_countdown_active:
            countdown_direction = "Bullish Countdown"
        elif sell_countdown_active:
            countdown_direction = "Bearish Countdown"
        else:
            countdown_direction = "Neutral"

        if countdown_completed == 1:
            signal = "Buy Exhaustion"
            signal_source = "Countdown 13"
        elif countdown_completed == -1:
            signal = "Sell Exhaustion"
            signal_source = "Countdown 13"
        elif setup_perfected and setup_direction == 1:
            signal = "Buy Exhaustion"
            signal_source = "Perfected Setup 9"
        elif setup_perfected and setup_direction == -1:
            signal = "Sell Exhaustion"
            signal_source = "Perfected Setup 9"
        else:
            signal = "Neutral"
            signal_source = "None"

        result.append(
            {
                "TD_Setup_Count": setup_count,
                "TD_Setup_Direction": (
                    "Bullish Setup"
                    if setup_direction == 1 and setup_count
                    else "Bearish Setup"
                    if setup_direction == -1 and setup_count
                    else "Neutral"
                ),
                "TD_Setup_Perfected": "Yes" if setup_perfected else "No",
                "TD_Buy_Countdown": buy_countdown,
                "TD_Sell_Countdown": sell_countdown,
                "TD_Countdown_Direction": countdown_direction,
                "TD_Countdown_Complete": countdown_completed != 0,
                "TD_Signal": signal,
                "TD_Signal_Source": signal_source,
            }
        )
    return result


def calculate_indicators(frame: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calculate all indicator and signal columns for chronologically ordered OHLC bars."""
    if not frame:
        return []

    result = [dict(row) for row in sorted(frame, key=lambda row: row["Date"])]
    close = [float(row["Close"]) for row in result]
    high = [float(row["High"]) for row in result]
    low = [float(row["Low"]) for row in result]
    demark = _calculate_demark_sequential(high, low, close)

    ema50 = _ema(close, 50)
    ema200 = _ema(close, 200)
    raw_diff = [short - long for short, long in zip(ema50, ema200)]
    ema_diff = [abs(value) for value in raw_diff]
    ema_diff_delta: list[float | None] = [None] + [
        current - previous for previous, current in zip(ema_diff, ema_diff[1:])
    ]
    sigma2_threshold = [
        None if value is None else 2 * value
        for value in _rolling_std(ema_diff_delta, 50)
    ]

    delta: list[float | None] = [None] + [
        current - previous for previous, current in zip(close, close[1:])
    ]
    gains = [0.0 if value is None or value < 0 else value for value in delta]
    losses = [0.0 if value is None or value > 0 else -value for value in delta]
    average_gains = _rolling_mean(gains, 14)
    average_losses = _rolling_mean(losses, 14)
    rsi: list[float | None] = []
    for gain, loss in zip(average_gains, average_losses):
        if gain is None or loss is None or (gain == 0 and loss == 0):
            rsi.append(None)
        elif loss == 0:
            rsi.append(100.0)
        else:
            rsi.append(100 - (100 / (1 + gain / loss)))

    macd_fast = _ema(close, 12)
    macd_slow = _ema(close, 26)
    macd = [fast - slow for fast, slow in zip(macd_fast, macd_slow)]
    macd_signal = _ema(macd, 9)
    macd_histogram = [value - signal for value, signal in zip(macd, macd_signal)]

    true_range = []
    for index, (day_high, day_low) in enumerate(zip(high, low)):
        ranges = [day_high - day_low]
        if index:
            ranges.extend(
                [
                    abs(day_high - close[index - 1]),
                    abs(day_low - close[index - 1]),
                ]
            )
        true_range.append(max(ranges))
    atr = _rolling_mean(true_range, 14)
    relative_atr = [
        None if value is None else value / price * 100
        for value, price in zip(atr, close)
    ]

    available_relative_atr = [value for value in relative_atr if value is not None]
    volatility_cutoffs = [
        _quantile(available_relative_atr, probability)
        for probability in (0.25, 0.50, 0.75)
    ]

    for index, row in enumerate(result):
        previous_spread = raw_diff[index - 1] if index else None
        spread_change = ema_diff_delta[index]
        threshold = sigma2_threshold[index]
        if previous_spread is not None and (
            (previous_spread > 0 and raw_diff[index] < 0)
            or (previous_spread < 0 and raw_diff[index] > 0)
        ):
            spread_trend = "Crossover"
        elif (
            spread_change is not None
            and threshold is not None
            and abs(spread_change) <= threshold
        ):
            spread_trend = "2sigma range"
        elif spread_change is not None and spread_change > 0:
            spread_trend = "Broadening"
        elif spread_change is not None and spread_change < 0:
            spread_trend = "Narrowing"
        else:
            spread_trend = "Neutral"

        rsi_value = rsi[index]
        rsi_signal = (
            "Overbought"
            if rsi_value is not None and rsi_value > 70
            else "Oversold"
            if rsi_value is not None and rsi_value < 30
            else "Neutral"
        )
        previous_histogram = macd_histogram[index - 1] if index else None
        if previous_histogram is not None and previous_histogram <= 0 < macd_histogram[index]:
            crossover = "Bullish"
        elif previous_histogram is not None and previous_histogram >= 0 > macd_histogram[index]:
            crossover = "Bearish"
        else:
            crossover = ""

        relative_atr_value = relative_atr[index]
        if relative_atr_value is None or any(value is None for value in volatility_cutoffs):
            volatility_signal = ""
        elif relative_atr_value <= volatility_cutoffs[0]:
            volatility_signal = "Low"
        elif relative_atr_value <= volatility_cutoffs[1]:
            volatility_signal = "Medium"
        elif relative_atr_value <= volatility_cutoffs[2]:
            volatility_signal = "High"
        else:
            volatility_signal = "Extreme"

        direction = "Bullish" if macd[index] > macd_signal[index] else "Bearish"
        if crossover:
            direction = f"{crossover} Crossover"

        row.update(
            {
                "EMA50": ema50[index],
                "EMA200": ema200[index],
                "Raw_Diff": raw_diff[index],
                "EMA_Diff": ema_diff[index],
                "EMA_Diff_Delta": spread_change,
                "Sigma2_Threshold": threshold,
                "EMA_Spread_Trend": spread_trend,
                "MACD": macd[index],
                "MACD_Signal": macd_signal[index],
                "MACD_Histogram": macd_histogram[index],
                "MACD_Crossover": crossover,
                "RSI": rsi_value,
                "RSI_Signal": rsi_signal,
                "ATR": atr[index],
                "Relative_ATR": relative_atr_value,
                "Vol_Signal": volatility_signal,
                "Condition": rsi_signal,
                "Trend": "Bullish" if ema50[index] > ema200[index] else "Bearish",
                "MACD Direction": direction,
                **demark[index],
            }
        )
    return result


def _read_price_rows(csv_path: Path) -> dict[str, dict[date, dict[str, Any]]]:
    tickers: dict[str, dict[date, dict[str, Any]]] = {}
    with csv_path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or not {"Ticker", "Date", *PRICE_COLUMNS}.issubset(
            reader.fieldnames
        ):
            raise ValueError(f"{csv_path} must contain Ticker, Date, and OHLC columns")
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{csv_path}:{line_number}: row contains extra CSV fields")
            try:
                ticker = row["Ticker"].strip().upper()
                row_date = date.fromisoformat(row["Date"].strip())
                prices = {column: float(row[column]) for column in PRICE_COLUMNS}
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(
                    f"{csv_path}:{line_number}: invalid price data"
                ) from error
            if not ticker:
                raise ValueError(f"{csv_path}:{line_number}: missing ticker")
            if (
                not all(math.isfinite(value) for value in prices.values())
                or any(prices[column] <= 0 for column in ("High", "Low", "Close"))
                or prices["Open"] < 0
            ):
                raise ValueError(
                    f"{csv_path}:{line_number}: OHLC prices must be finite and valid"
                )
            open_unavailable = prices["Open"] == 0
            if open_unavailable:
                LOGGER.warning(
                    "%s:%s: open is zero; alpha calculations will treat it as unavailable",
                    csv_path,
                    line_number,
                )
            expected_high = max(prices["Close"], prices["Low"], prices["Open"])
            expected_low = min(
                prices["Close"],
                prices["High"],
                prices["Open"] if prices["Open"] else prices["Close"],
            )
            if prices["High"] < expected_high or prices["Low"] > expected_low:
                LOGGER.warning(
                    "%s:%s: skipping bar with inconsistent high/low values",
                    csv_path,
                    line_number,
                )
                continue
            if open_unavailable:
                prices["Open"] = None
            raw_volume = row.get("Volume")
            try:
                volume = None if raw_volume is None or not raw_volume.strip() else float(raw_volume)
            except ValueError as error:
                raise ValueError(
                    f"{csv_path}:{line_number}: invalid volume"
                ) from error
            if volume is not None and (not math.isfinite(volume) or volume < 0):
                raise ValueError(
                    f"{csv_path}:{line_number}: volume must be finite and non-negative"
                )
            if row_date in tickers.get(ticker, {}):
                raise ValueError(
                    f"{csv_path}:{line_number}: duplicate {ticker} bar for {row_date}"
                )
            row.update(
                {"Ticker": ticker, "Date": row_date.isoformat(), **prices, "Volume": volume}
            )
            tickers.setdefault(ticker, {})[row_date] = row
    if not tickers:
        raise ValueError(f"No ticker data found in {csv_path}")
    return tickers


def update_csv(
    source_dir: Path, output_dir: Path, end_date: date
) -> dict[str, tuple[date, int]]:
    csv_paths = sorted(source_dir.glob("*.csv"))
    if not csv_paths:
        raise FileNotFoundError(
            f"No per-ticker price files found in {source_dir} (expected <TICKER>.csv)"
        )

    ticker_prices: dict[str, dict[date, dict[str, Any]]] = {}
    output_stems: dict[str, str] = {}
    for csv_path in csv_paths:
        file_tickers = _read_price_rows(csv_path)
        if len(file_tickers) != 1:
            raise ValueError(f"{csv_path} must contain data for exactly one ticker")
        ticker, prices = next(iter(file_tickers.items()))
        if csv_path.stem.upper() != ticker.lstrip("^"):
            raise ValueError(
                f"{csv_path} contains {ticker} data but its filename does not match"
            )
        if ticker in ticker_prices:
            raise ValueError(f"Multiple source files contain ticker {ticker}")
        prices = {day: row for day, row in prices.items() if day <= end_date}
        if not prices:
            raise ValueError(
                f"{csv_path} contains no price data on or before {end_date}"
            )
        ticker_prices[ticker] = prices
        output_stems[ticker] = csv_path.stem

    summaries: dict[str, tuple[date, int]] = {}
    indicator_rows_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for ticker, prices in ticker_prices.items():
        price_rows = [prices[day] for day in sorted(prices)]
        indicator_rows = calculate_indicators(price_rows)
        indicator_rows_by_ticker[ticker] = indicator_rows
        summaries[ticker] = (max(prices), len(price_rows))

    alpha_rows_by_ticker = calculate_alpha_factors(indicator_rows_by_ticker)
    updated_rows = {
        ticker: [
            {**row, **alpha_values}
            for row, alpha_values in zip(
                indicator_rows_by_ticker[ticker], alpha_rows_by_ticker[ticker]
            )
        ]
        for ticker in indicator_rows_by_ticker
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    for ticker, rows in updated_rows.items():
        csv_path = output_dir / f"{FILE_PREFIX}{output_stems[ticker]}.csv"
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                newline="",
                encoding="utf-8",
                dir=output_dir,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                writer = csv.DictWriter(
                    output, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore"
                )
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            column: "" if row.get(column) is None else row.get(column, "")
                            for column in OUTPUT_COLUMNS
                        }
                    )
            os.replace(temporary_path, csv_path)
        except Exception:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
            raise
    return summaries


def configure_logging(log_file: Path) -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_file = str(log_file.resolve())
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    for handler in LOGGER.handlers[:]:
        if isinstance(handler, logging.FileHandler) and handler.baseFilename != resolved_log_file:
            LOGGER.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S%z",
    )
    if not any(
        getattr(handler, "baseFilename", None) == resolved_log_file
        for handler in LOGGER.handlers
    ):
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
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


def run_batch(
    source_dir: Path,
    output_dir: Path,
    end_date: date,
    log_file: Path = DEFAULT_LOG_FILE,
) -> dict[str, tuple[date, int]]:
    logger = configure_logging(log_file)
    started_at = datetime.now(timezone.utc)
    logger.info(
        "Indicator run started: source=%s output=%s end_date=%s",
        source_dir,
        output_dir,
        end_date,
    )
    try:
        summaries = update_csv(source_dir, output_dir, end_date)
    except Exception:
        logger.exception("Indicator run failed: source=%s", source_dir)
        raise

    for ticker, (latest, row_count) in sorted(summaries.items()):
        logger.info(
            "%s: wrote %s rows through %s",
            ticker,
            row_count,
            latest,
        )
    elapsed_seconds = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        "Indicator run completed: tickers=%s elapsed_seconds=%.2f",
        len(summaries),
        elapsed_seconds,
    )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Calculate daily price indicators and alpha factors from local "
            "per-ticker OHLCV CSV files."
        )
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=date.today(),
        help="Inclusive end date in YYYY-MM-DD format (defaults to today).",
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Directory containing source <TICKER>.csv daily OHLCV files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated daily_price_indicators_<TICKER>.csv files.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=DEFAULT_LOG_FILE,
        help="Append run history and per-ticker results to this log file.",
    )
    arguments = parser.parse_args()

    for ticker, (latest, row_count) in sorted(
        run_batch(
            arguments.source_dir,
            arguments.output_dir,
            arguments.end_date,
            arguments.log_file,
        ).items()
    ):
        print(
            f"{ticker}: {row_count} rows written through {latest}"
        )


if __name__ == "__main__":
    main()

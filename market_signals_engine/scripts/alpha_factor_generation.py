"""Calculate the project's alpha factors for a panel of per-ticker OHLCV rows.

The original notebook formulas use cross-sectional ``rank`` operators. Since
the results are stored in separate ticker files, ranks are calculated across
all supplied tickers for each date before being written to those files.

The factors cover the notebook's classic alphas (001-010 where defined),
liquidity, volatility, momentum, technical, turnover, and correlation groups.
VWAP follows the notebook's daily typical-price proxy, ``(High + Low + Close)/3``.
This module has no import-time downloads or file-writing side effects.
"""

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any


ALPHA_COLUMNS = (
    "alpha_001",
    "alpha_002",
    "alpha_003",
    "alpha_004",
    "alpha_006",
    "alpha_007",
    "alpha_008",
    "alpha_009",
    "alpha_010",
    "alpha_liq_01",
    "alpha_liq_02",
    "alpha_liq_03",
    "alpha_liq_04",
    "alpha_vol_01",
    "alpha_mom_01",
    "alpha_mom_02",
    "alpha_vol_02",
    "alpha_tech_01",
    "alpha_tech_02",
    "alpha_tech_03",
    "alpha_tech_04",
    "alpha_turn_01",
    "alpha_corr_01",
    "alpha_corr_02",
)

_Series = list[float | None]
_Panel = dict[str, _Series]


def _rolling(
    values: _Series, window: int, operation: Callable[[list[float]], float]
) -> _Series:
    result: _Series = [None] * len(values)
    for index in range(window - 1, len(values)):
        sample = values[index - window + 1 : index + 1]
        if all(value is not None for value in sample):
            result[index] = operation([value for value in sample if value is not None])
    return result


def _rolling_sum(values: _Series, window: int) -> _Series:
    return _rolling(values, window, sum)


def _rolling_mean(values: _Series, window: int) -> _Series:
    return _rolling(values, window, lambda sample: sum(sample) / window)


def _rolling_min(values: _Series, window: int) -> _Series:
    return _rolling(values, window, min)


def _rolling_max(values: _Series, window: int) -> _Series:
    return _rolling(values, window, max)


def _rolling_std(values: _Series, window: int) -> _Series:
    def sample_std(sample: list[float]) -> float:
        average = sum(sample) / len(sample)
        return math.sqrt(sum((value - average) ** 2 for value in sample) / (len(sample) - 1))

    return _rolling(values, window, sample_std)


def _rolling_correlation(left: _Series, right: _Series, window: int) -> _Series:
    result: _Series = [None] * len(left)
    for index in range(window - 1, len(left)):
        left_sample = left[index - window + 1 : index + 1]
        right_sample = right[index - window + 1 : index + 1]
        if any(value is None for value in left_sample + right_sample):
            continue
        x_values = [value for value in left_sample if value is not None]
        y_values = [value for value in right_sample if value is not None]
        x_mean = sum(x_values) / window
        y_mean = sum(y_values) / window
        covariance = sum(
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x_values, y_values)
        )
        x_variance = sum((value - x_mean) ** 2 for value in x_values)
        y_variance = sum((value - y_mean) ** 2 for value in y_values)
        denominator = math.sqrt(x_variance * y_variance)
        if denominator:
            result[index] = covariance / denominator
    return result


def _delta(values: _Series, period: int = 1) -> _Series:
    return [
        None if index < period or values[index] is None or values[index - period] is None
        else values[index] - values[index - period]
        for index in range(len(values))
    ]


def _time_rank(values: _Series, window: int) -> _Series:
    result: _Series = [None] * len(values)
    for index in range(window - 1, len(values)):
        sample = values[index - window + 1 : index + 1]
        if any(value is None for value in sample):
            continue
        numbers = [value for value in sample if value is not None]
        current = numbers[-1]
        less = sum(value < current for value in numbers)
        equal = sum(value == current for value in numbers)
        result[index] = (less + (equal + 1) / 2) / window
    return result


def _cross_sectional_rank(panel: _Panel, dates: Mapping[str, Sequence[str]]) -> _Panel:
    """Rank each date across tickers, using average ranks for tied values."""
    values_by_date: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
    result: _Panel = {ticker: [None] * len(values) for ticker, values in panel.items()}
    for ticker, values in panel.items():
        for index, value in enumerate(values):
            if value is not None and math.isfinite(value):
                values_by_date[dates[ticker][index]].append((ticker, index, value))

    for values in values_by_date.values():
        ordered = sorted(values, key=lambda item: item[2])
        count = len(ordered)
        start = 0
        while start < count:
            end = start + 1
            while end < count and ordered[end][2] == ordered[start][2]:
                end += 1
            percentile_rank = ((start + 1 + end) / 2) / count
            for ticker, index, _ in ordered[start:end]:
                result[ticker][index] = percentile_rank
            start = end
    return result


def _map_panel(panel: _Panel, operation: Callable[[float], float]) -> _Panel:
    return {
        ticker: [None if value is None else operation(value) for value in values]
        for ticker, values in panel.items()
    }


def _combine_panels(
    left: _Panel, right: _Panel, operation: Callable[[float, float], float]
) -> _Panel:
    return {
        ticker: [
            None if x is None or y is None else operation(x, y)
            for x, y in zip(left[ticker], right[ticker])
        ]
        for ticker in left
    }


def _rolling_panel(panel: _Panel, window: int, operation: Callable[[_Series, int], _Series]) -> _Panel:
    return {ticker: operation(values, window) for ticker, values in panel.items()}


def _cross_sectional_correlations(
    left: _Panel, right: _Panel, window: int
) -> _Panel:
    return {
        ticker: _rolling_correlation(left[ticker], right[ticker], window)
        for ticker in left
    }


def _ema(values: _Series, span: int) -> _Series:
    result: _Series = []
    alpha = 2 / (span + 1)
    previous = None
    for value in values:
        if value is None:
            result.append(None)
        else:
            previous = value if previous is None else alpha * value + (1 - alpha) * previous
            result.append(previous)
    return result


def calculate_alpha_factors(
    ticker_rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, float | None]]]:
    """Return all alpha columns aligned to each ticker's input rows.

    Input rows must include ``Date``, OHLC, and ``Volume``. Missing opens or
    missing/zero volume leave dependent factors blank rather than inventing values.
    """
    rows_by_ticker: dict[str, list[Mapping[str, Any]]] = {}
    dates: dict[str, list[str]] = {}
    for ticker, rows in ticker_rows.items():
        ordered = sorted(rows, key=lambda row: str(row["Date"]))
        row_dates = [str(row["Date"]) for row in ordered]
        if len(set(row_dates)) != len(row_dates):
            raise ValueError(f"{ticker} has duplicate dates")
        rows_by_ticker[ticker] = ordered
        dates[ticker] = row_dates

    def read_column(column: str, *, optional: bool = False) -> _Panel:
        panel: _Panel = {}
        for ticker, rows in rows_by_ticker.items():
            values = []
            for row in rows:
                raw_value = row.get(column)
                if raw_value in (None, ""):
                    if optional:
                        values.append(None)
                        continue
                    raise ValueError(f"{ticker} row {row['Date']} is missing {column}")
                value = float(raw_value)
                values.append(value if math.isfinite(value) else None)
            panel[ticker] = values
        return panel

    close = read_column("Close")
    open_price = read_column("Open", optional=True)
    high = read_column("High")
    low = read_column("Low")
    volume = read_column("Volume", optional=True)
    returns = {
        ticker: [
            None
            if index == 0 or values[index - 1] == 0
            else values[index] / values[index - 1] - 1
            for index in range(len(values))
        ]
        for ticker, values in close.items()
    }
    vwap = _combine_panels(
        _combine_panels(high, low, lambda x, y: x + y),
        close,
        lambda subtotal, close_value: (subtotal + close_value) / 3,
    )
    turnover = _map_panel(
        {ticker: [None if value is None else value / 1_000_000 for value in values]
         for ticker, values in volume.items()},
        lambda value: value,
    )

    def rank(panel: _Panel) -> _Panel:
        return _cross_sectional_rank(panel, dates)

    factors: dict[str, _Panel] = {}
    close_high20 = _rolling_panel(close, 20, _rolling_max)
    factors["alpha_001"] = rank(
        _combine_panels(close, close_high20, lambda current, peak: current - peak)
    )

    log_volume = _map_panel(
        {
            ticker: [
                None if value is None or value <= 0 else math.log(value)
                for value in values
            ]
            for ticker, values in volume.items()
        },
        lambda value: value,
    )
    ranked_volume_delta = rank(
        {ticker: _delta(values, 2) for ticker, values in log_volume.items()}
    )
    intraday_return = _combine_panels(
        _combine_panels(close, open_price, lambda c, o: c - o),
        open_price,
        lambda difference, opening: difference / opening if opening else math.nan,
    )
    ranked_intraday_return = rank(
        {
            ticker: [None if not (value is not None and math.isfinite(value)) else value
                     for value in values]
            for ticker, values in intraday_return.items()
        }
    )
    factors["alpha_002"] = _map_panel(
        _cross_sectional_correlations(
            ranked_volume_delta, ranked_intraday_return, 6
        ),
        lambda value: -value,
    )

    factors["alpha_003"] = _map_panel(
        _cross_sectional_correlations(rank(open_price), rank(volume), 10),
        lambda value: -value,
    )
    factors["alpha_004"] = _map_panel(
        _rolling_panel(rank(low), 9, _time_rank),
        lambda value: -value,
    )
    factors["alpha_006"] = _map_panel(
        {
            ticker: _rolling_correlation(open_price[ticker], volume[ticker], 10)
            for ticker in rows_by_ticker
        },
        lambda value: -value,
    )

    close_delta7 = {ticker: _delta(values, 7) for ticker, values in close.items()}
    alpha7_rank = _rolling_panel(
        _map_panel(close_delta7, abs), 60, _time_rank
    )
    average_volume20 = _rolling_panel(volume, 20, _rolling_mean)
    factors["alpha_007"] = {}
    for ticker in rows_by_ticker:
        alpha7_values = []
        for index, (rank_value, change, average_volume, current_volume) in enumerate(
            zip(
                alpha7_rank[ticker],
                close_delta7[ticker],
                average_volume20[ticker],
                volume[ticker],
            )
        ):
            if (
                average_volume is not None
                and current_volume is not None
                and average_volume >= current_volume
            ):
                alpha7_values.append(-1.0)
            elif rank_value is None or change is None:
                alpha7_values.append(None)
            else:
                alpha7_values.append(-rank_value * (1 if change > 0 else -1 if change < 0 else 0))
        factors["alpha_007"][ticker] = alpha7_values

    open_sum5 = _rolling_panel(open_price, 5, _rolling_sum)
    return_sum5 = _rolling_panel(returns, 5, _rolling_sum)
    alpha8_base = _combine_panels(
        open_sum5, return_sum5, lambda open_sum, return_sum: open_sum * return_sum
    )
    alpha8_delta = {
        ticker: _delta(values, 10) for ticker, values in alpha8_base.items()
    }
    factors["alpha_008"] = _map_panel(rank(alpha8_delta), lambda value: -value)

    close_delta1 = {ticker: _delta(values) for ticker, values in close.items()}
    factors["alpha_009"] = {}
    factors["alpha_010"] = {}
    alpha10_base: _Panel = {}
    for ticker, changes in close_delta1.items():
        minimum5 = _rolling_min(changes, 5)
        maximum5 = _rolling_max(changes, 5)
        minimum4 = _rolling_min(changes, 4)
        maximum4 = _rolling_max(changes, 4)
        alpha9_values = []
        alpha10_values = []
        for index, change in enumerate(changes):
            trend5 = (
                (minimum5[index] is not None and minimum5[index] > 0)
                or (maximum5[index] is not None and maximum5[index] < 0)
            )
            trend4 = (
                (minimum4[index] is not None and minimum4[index] > 0)
                or (maximum4[index] is not None and maximum4[index] < 0)
            )
            alpha9_values.append(
                None if change is None else change if trend5 else -change
            )
            alpha10_values.append(
                None if change is None else change if trend4 else -change
            )
        factors["alpha_009"][ticker] = alpha9_values
        alpha10_base[ticker] = alpha10_values
    factors["alpha_010"] = rank(alpha10_base)

    volume_delta5 = {ticker: _delta(values, 5) for ticker, values in volume.items()}
    factors["alpha_liq_01"] = rank(volume_delta5)
    factors["alpha_liq_02"] = rank(average_volume20)
    factors["alpha_liq_03"] = rank(turnover)
    factors["alpha_liq_04"] = rank(
        {ticker: _delta(values, 5) for ticker, values in turnover.items()}
    )

    factors["alpha_vol_01"] = rank(_rolling_panel(returns, 20, _rolling_std))
    for period, name in ((10, "alpha_mom_01"), (20, "alpha_mom_02")):
        momentum = {
            ticker: [
                None
                if index < period or values[index - period] == 0
                else values[index] / values[index - period] - 1
                for index in range(len(values))
            ]
            for ticker, values in close.items()
        }
        factors[name] = rank(momentum)

    high10 = _rolling_panel(high, 10, _rolling_max)
    low10 = _rolling_panel(low, 10, _rolling_min)
    factors["alpha_vol_02"] = rank(
        _combine_panels(
            high10,
            low10,
            lambda highest, lowest: highest / lowest - 1 if lowest else math.nan,
        )
    )

    high20 = _rolling_panel(high, 20, _rolling_max)
    low20 = _rolling_panel(low, 20, _rolling_min)
    stochastic: _Panel = {}
    for ticker in rows_by_ticker:
        values = []
        for index, (current, floor, ceiling) in enumerate(
            zip(close[ticker], low20[ticker], high20[ticker])
        ):
            width = None if floor is None or ceiling is None else ceiling - floor
            values.append(
                None
                if width is None or width == 0
                else (current - floor) / width
            )
        stochastic[ticker] = values
    factors["alpha_tech_01"] = rank(stochastic)
    factors["alpha_tech_02"] = rank(
        {
            ticker: _rolling_correlation(returns[ticker], volume[ticker], 5)
            for ticker in rows_by_ticker
        }
    )
    vwap_distance = _combine_panels(
        _combine_panels(close, vwap, lambda current, average: current - average),
        close,
        lambda difference, current: difference / current if current else math.nan,
    )
    factors["alpha_tech_03"] = rank(
        {
            ticker: [None if value is None or not math.isfinite(value) else value
                     for value in values]
            for ticker, values in vwap_distance.items()
        }
    )

    macd: _Panel = {}
    for ticker, values in close.items():
        fast = _ema(values, 12)
        slow = _ema(values, 26)
        macd[ticker] = [
            None if short is None or long is None else short - long
            for short, long in zip(fast, slow)
        ]
    macd_signal = {ticker: _ema(values, 9) for ticker, values in macd.items()}
    macd_histogram = _combine_panels(
        macd, macd_signal, lambda value, signal: value - signal
    )
    factors["alpha_tech_04"] = rank(macd_histogram)

    turnover_mean5 = _rolling_panel(turnover, 5, _rolling_mean)
    factors["alpha_turn_01"] = rank(turnover_mean5)
    factors["alpha_corr_01"] = rank(
        {
            ticker: _rolling_correlation(
                rank(turnover)[ticker], rank(volume)[ticker], 5
            )
            for ticker in rows_by_ticker
        }
    )
    factors["alpha_corr_02"] = rank(
        {
            ticker: _rolling_correlation(close[ticker], vwap[ticker], 10)
            for ticker in rows_by_ticker
        }
    )

    return {
        ticker: [
            {name: factors[name][ticker][index] for name in ALPHA_COLUMNS}
            for index in range(len(rows))
        ]
        for ticker, rows in rows_by_ticker.items()
    }

"""Market-value-weighted portfolio risk estimates from daily close histories."""

import math
from datetime import datetime, timezone


TRADING_DAYS_PER_YEAR = 252
MIN_REGRESSION_OBSERVATIONS = 30
VAR_95_Z_SCORE = 1.645


def _number(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _bar_date(bar):
    timestamp = _number(bar.get("t"), default=None)
    if timestamp is None:
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _close_prices(bars):
    prices = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        day = _bar_date(bar)
        close = _number(bar.get("c"), default=None)
        if day and close is not None and close > 0:
            prices[day] = close
    return prices


def _daily_returns(prices):
    days = sorted(prices)
    return {
        days[index]: prices[days[index]] / prices[days[index - 1]] - 1
        for index in range(1, len(days))
    }


def _sample_variance(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _regression(dependent, independent):
    if len(dependent) != len(independent) or len(dependent) < 2:
        return None
    independent_variance = _sample_variance(independent)
    if independent_variance is None or independent_variance == 0:
        return None

    dependent_mean = sum(dependent) / len(dependent)
    independent_mean = sum(independent) / len(independent)
    covariance = sum(
        (x - independent_mean) * (y - dependent_mean)
        for x, y in zip(independent, dependent)
    ) / (len(dependent) - 1)
    beta = covariance / independent_variance
    residuals = [
        (actual - dependent_mean) - beta * (market - independent_mean)
        for actual, market in zip(dependent, independent)
    ]
    dependent_variance = _sample_variance(dependent)
    residual_variance = _sample_variance(residuals)
    if dependent_variance is None or residual_variance is None:
        return None

    return {
        "beta": beta,
        "r_squared": max(0.0, min(1.0, 1 - residual_variance / dependent_variance))
        if dependent_variance > 0 else 0.0,
        "residual_volatility": math.sqrt(residual_variance * TRADING_DAYS_PER_YEAR),
        "market_volatility": math.sqrt(independent_variance * TRADING_DAYS_PER_YEAR),
        "total_volatility": math.sqrt(dependent_variance * TRADING_DAYS_PER_YEAR),
        "observations": len(dependent),
        "daily_mean": dependent_mean,
        "daily_volatility": math.sqrt(dependent_variance),
    }


def analyze_portfolio_risk(positions, price_history, benchmark_bars, portfolio_value):
    """Return concentration, exposure coverage, and one-factor risk estimates.

    Position weights use IBKR market values and net liquidation value. Returns
    are close-to-close percentage changes; results are indicative, not a
    holdings-aware or derivatives-Greeks risk model.
    """
    positions = positions if isinstance(positions, list) else []
    holdings = []
    asset_class_values = {}
    gross_exposure = 0.0

    for position in positions:
        if not isinstance(position, dict):
            continue
        market_value = _number(position.get("mktValue"))
        if market_value == 0:
            continue
        gross_value = abs(market_value)
        gross_exposure += gross_value
        asset_class = (
            position.get("assetClass")
            or position.get("asset_class")
            or position.get("instrument_type")
            or "Other"
        )
        asset_class_values[asset_class] = asset_class_values.get(asset_class, 0.0) + gross_value
        conid = position.get("conid")
        holdings.append({
            "symbol": position.get("ticker") or position.get("symbol") or position.get("name") or str(conid or "Unknown"),
            "description": position.get("contractDesc") or position.get("description") or "",
            "asset_class": asset_class,
            "market_value": market_value,
            "gross_value": gross_value,
            "conid": str(conid) if conid is not None else "",
        })

    net_liquidation = _number(portfolio_value)
    weight_denominator = net_liquidation if net_liquidation > 0 else gross_exposure
    benchmark_returns = _daily_returns(_close_prices(benchmark_bars))
    price_history = price_history if isinstance(price_history, dict) else {}

    for holding in holdings:
        returns = _daily_returns(_close_prices(price_history.get(holding["conid"], [])))
        paired_days = sorted(set(returns).intersection(benchmark_returns))
        model = None
        if len(paired_days) >= MIN_REGRESSION_OBSERVATIONS:
            model = _regression(
                [returns[day] for day in paired_days],
                [benchmark_returns[day] for day in paired_days],
            )
        holding["net_weight_pct"] = (
            holding["market_value"] / weight_denominator * 100
            if weight_denominator > 0 else 0.0
        )
        holding["gross_weight_pct"] = (
            holding["gross_value"] / gross_exposure * 100
            if gross_exposure > 0 else 0.0
        )
        holding["beta"] = model["beta"] if model else None
        holding["beta_adjusted_weight_pct"] = (
            holding["net_weight_pct"] * model["beta"] if model else None
        )
        holding["systematic_volatility"] = (
            abs(model["beta"]) * model["market_volatility"] if model else None
        )
        holding["annualized_volatility"] = model["total_volatility"] if model else None
        holding["idiosyncratic_volatility"] = model["residual_volatility"] if model else None
        holding["observations"] = model["observations"] if model else len(paired_days)
        holding["_returns"] = returns
        holding["_modeled"] = model is not None

    modeled_holdings = [holding for holding in holdings if holding["_modeled"]]
    modeled_value = sum(holding["gross_value"] for holding in modeled_holdings)
    modeled_coverage_pct = (
        modeled_value / gross_exposure * 100 if gross_exposure > 0 else 0.0
    )

    metrics = None
    common_days = set(benchmark_returns)
    for holding in modeled_holdings:
        common_days.intersection_update(holding["_returns"])
    common_days = sorted(common_days)

    if (
        modeled_holdings
        and len(common_days) >= MIN_REGRESSION_OBSERVATIONS
        and weight_denominator > 0
    ):
        portfolio_returns = [
            sum(
                holding["market_value"] / weight_denominator * holding["_returns"][day]
                for holding in modeled_holdings
            )
            for day in common_days
        ]
        market_returns = [benchmark_returns[day] for day in common_days]
        metrics = _regression(portfolio_returns, market_returns)
        if metrics:
            metrics["systematic_volatility"] = abs(metrics["beta"]) * metrics["market_volatility"]
            metrics["idiosyncratic_volatility"] = metrics["residual_volatility"]
            metrics["one_day_var_95"] = max(
                0.0,
                (VAR_95_Z_SCORE * metrics["daily_volatility"] - metrics["daily_mean"])
                * weight_denominator,
            )
            metrics["observations"] = len(common_days)

    warnings = []
    if len(holdings) == 0:
        warnings.append("No non-zero market-value positions were available to analyze.")
    if modeled_coverage_pct < 99.9:
        warnings.append(
            "Some holdings lack at least 30 overlapping daily returns with the benchmark; "
            "their risk is excluded from the factor model."
        )
    if metrics is None and holdings:
        warnings.append(
            "There are not enough common daily observations across the modeled holdings "
            "and benchmark to estimate portfolio beta and volatility."
        )

    for holding in holdings:
        holding.pop("_returns", None)
        holding.pop("_modeled", None)

    position_rows = sorted(holdings, key=lambda holding: holding["gross_value"], reverse=True)
    asset_classes = [
        {
            "name": name,
            "gross_value": value,
            "gross_weight_pct": value / gross_exposure * 100 if gross_exposure else 0.0,
        }
        for name, value in sorted(asset_class_values.items(), key=lambda item: item[1], reverse=True)
    ]
    top_five_gross_value = sum(
        holding["gross_value"] for holding in position_rows[:5]
    )

    return {
        "portfolio_value": net_liquidation,
        "gross_exposure": gross_exposure,
        "gross_leverage_pct": gross_exposure / net_liquidation * 100 if net_liquidation > 0 else None,
        "modeled_coverage_pct": modeled_coverage_pct,
        "position_count": len(holdings),
        "modeled_position_count": len(modeled_holdings),
        "largest_position_gross_weight_pct": (
            position_rows[0]["gross_weight_pct"] if position_rows else 0.0
        ),
        "top_five_gross_weight_pct": (
            top_five_gross_value / gross_exposure * 100 if gross_exposure else 0.0
        ),
        "benchmark_observations": len(benchmark_returns),
        "metrics": metrics,
        "positions": position_rows,
        "asset_classes": asset_classes,
        "warnings": warnings,
    }

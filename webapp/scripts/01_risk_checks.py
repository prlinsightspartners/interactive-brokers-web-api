from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class RiskLimits:
    max_order_notional: Decimal = Decimal("100000")
    max_daily_loss: Decimal = Decimal("5000")
    max_position_notional: Decimal = Decimal("250000")
    max_quantity: int = 1000
    max_price_deviation_pct: Decimal = Decimal("2.0")
    max_bid_ask_spread_pct: Decimal = Decimal("1.0")


@dataclass
class RiskResult:
    approved: bool
    reason: Optional[str] = None


class RiskChecker:

    def __init__(self, ib, limits: RiskLimits):
        self.ib = ib
        self.limits = limits

    def check(self, signal, market_data, account):
        checks = [
            self._check_system_enabled(account),
            self._check_quantity(signal),
            self._check_notional(signal, market_data),
            self._check_market_data(market_data),
            self._check_spread(signal, market_data),
            self._check_price_deviation(signal, market_data),
            self._check_daily_loss(account),
            self._check_buying_power(account, signal, market_data),
        ]

        for result in checks:
            if not result.approved:
                return result

        return RiskResult(True)

    def _check_system_enabled(self, account):
        if not account["trading_enabled"]:
            return RiskResult(False, "Trading system disabled")

        return RiskResult(True)

    def _check_quantity(self, signal):

        quantity = signal["quantity"]

        if quantity <= 0:
            return RiskResult(False, "Invalid quantity")

        if quantity > self.limits.max_quantity:
            return RiskResult(
                False,
                f"Quantity exceeds limit: {quantity}"
            )

        return RiskResult(True)

    def _check_notional(self, signal, market_data):

        price = Decimal(str(market_data["price"]))
        quantity = Decimal(str(signal["quantity"]))

        notional = price * quantity

        if notional > self.limits.max_order_notional:
            return RiskResult(
                False,
                f"Order notional too large: {notional}"
            )

        return RiskResult(True)

    def _check_market_data(self, market_data):

        if market_data is None:
            return RiskResult(False, "No market data")

        if market_data.get("stale"):
            return RiskResult(False, "Market data is stale")

        return RiskResult(True)

    def _check_spread(self, signal, market_data):

        bid = market_data.get("bid")
        ask = market_data.get("ask")

        if not bid or not ask:
            return RiskResult(False, "Missing bid/ask")

        mid = (bid + ask) / 2

        spread_pct = ((ask - bid) / mid) * 100

        if spread_pct > float(
            self.limits.max_bid_ask_spread_pct
        ):
            return RiskResult(
                False,
                f"Bid/ask spread too wide: {spread_pct:.2f}%"
            )

        return RiskResult(True)

    def _check_price_deviation(self, signal, market_data):

        if signal.get("limit_price") is None:
            return RiskResult(True)

        reference_price = market_data.get("mid")

        if not reference_price:
            return RiskResult(False, "No reference price")

        limit_price = signal["limit_price"]

        deviation = abs(
            limit_price - reference_price
        ) / reference_price * 100

        if deviation > float(
            self.limits.max_price_deviation_pct
        ):
            return RiskResult(
                False,
                f"Limit price deviation too large: {deviation:.2f}%"
            )

        return RiskResult(True)

    def _check_daily_loss(self, account):

        if account["daily_pnl"] < -float(
            self.limits.max_daily_loss
        ):
            return RiskResult(
                False,
                "Daily loss limit exceeded"
            )

        return RiskResult(True)

    def _check_buying_power(
        self,
        account,
        signal,
        market_data
    ):

        required = (
            market_data["price"] *
            signal["quantity"]
        )

        if required > account["available_funds"]:
            return RiskResult(
                False,
                "Insufficient buying power"
            )

        return RiskResult(True)
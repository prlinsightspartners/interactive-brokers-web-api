from ib_insync import (
    Stock,
    Future,
    Option,
    Forex,
    Contract
)


class ContractResolver:

    def __init__(self, ib):
        self.ib = ib

    def resolve(self, signal):

        asset_type = signal["asset_type"]

        if asset_type == "STOCK":
            contract = self._stock(signal)

        elif asset_type == "FUTURE":
            contract = self._future(signal)

        elif asset_type == "OPTION":
            contract = self._option(signal)

        elif asset_type == "FOREX":
            contract = self._forex(signal)

        else:
            raise ValueError(
                f"Unsupported asset type: {asset_type}"
            )

        details = self.ib.reqContractDetails(
            contract
        )

        if not details:
            raise ValueError(
                f"Contract not found: {signal}"
            )

        if len(details) > 1:
            raise ValueError(
                "Multiple contracts matched"
            )

        return details[0]

    def _stock(self, signal):

        return Stock(
            signal["symbol"],
            signal.get("exchange", "SMART"),
            signal["currency"]
        )

    def _future(self, signal):

        return Future(
            symbol=signal["symbol"],
            lastTradeDateOrContractMonth=
                signal["expiry"],
            exchange=signal["exchange"],
            currency=signal["currency"]
        )

    def _option(self, signal):

        option = signal["option"]

        return Option(
            symbol=signal["symbol"],
            lastTradeDateOrContractMonth=
                option["expiry"],
            strike=float(option["strike"]),
            right=option["right"],
            exchange=signal["exchange"],
            currency=signal["currency"]
        )

    def _forex(self, signal):

        return Forex(
            signal["symbol"]
        )
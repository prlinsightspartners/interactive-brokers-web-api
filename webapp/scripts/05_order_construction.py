from ib_insync import MarketOrder, LimitOrder, StopOrder


class OrderBuilder:

    def build(self, signal):

        action = signal["action"]
        quantity = signal["quantity"]
        order_type = signal["order_type"]

        if order_type == "MARKET":

            order = MarketOrder(
                action,
                quantity
            )

        elif order_type == "LIMIT":

            order = LimitOrder(
                action,
                quantity,
                signal["limit_price"]
            )

        elif order_type == "STOP":

            order = StopOrder(
                action,
                quantity,
                signal["stop_price"]
            )

        else:

            raise ValueError(
                f"Unsupported order type: {order_type}"
            )

        order.tif = signal.get(
            "time_in_force",
            "DAY"
        )

        order.outsideRth = signal.get(
            "outside_rth",
            False
        )

        order.orderRef = signal[
            "signal_id"
        ]

        return order
import time


class IBKRExecution:

    def __init__(self, ib, db):

        self.ib = ib
        self.db = db

    def execute(self, contract, order, signal):

        # Final connection check
        if not self.ib.isConnected():
            raise RuntimeError(
                "IBKR is not connected"
            )

        # Final validation
        self._final_validation(
            contract,
            order
        )

        # Place order
        trade = self.ib.placeOrder(
            contract,
            order
        )

        # Log submission
        self._log_submission(
            signal,
            contract,
            order,
            trade
        )

        return trade

    def _final_validation(
        self,
        contract,
        order
    ):

        if order.totalQuantity <= 0:
            raise ValueError(
                "Invalid order quantity"
            )

        if not contract.conId:
            raise ValueError(
                "Invalid IBKR contract"
            )

        if not order.action in [
            "BUY",
            "SELL"
        ]:
            raise ValueError(
                "Invalid order action"
            )

    def _log_submission(
        self,
        signal,
        contract,
        order,
        trade
    ):

        self.db.execute(
            """
            INSERT INTO orders
            (
                signal_id,
                ib_order_id,
                symbol,
                action,
                quantity,
                order_type,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal["signal_id"],
                trade.order.orderId,
                contract.symbol,
                order.action,
                order.totalQuantity,
                order.orderType,
                trade.orderStatus.status
            )
        )

        self.db.commit()
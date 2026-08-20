from dataclasses import dataclass


@dataclass
class PositionResult:
    approved: bool
    reason: str = ""


class PositionChecker:

    def __init__(self, ib):
        self.ib = ib

    def check(self, signal, current_position):

        if current_position is None:
            current_position = 0

        quantity = signal["quantity"]
        action = signal["action"]

        if action == "BUY":
            projected = current_position + quantity

        elif action == "SELL":
            projected = current_position - quantity

        else:
            return PositionResult(
                False,
                "Invalid action"
            )

        # Example limits
        if abs(projected) > signal.get(
            "max_position",
            1000
        ):
            return PositionResult(
                False,
                f"Projected position too large: {projected}"
            )

        return PositionResult(True)
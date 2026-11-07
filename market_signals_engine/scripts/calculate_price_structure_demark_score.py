from collections.abc import Sequence

from .scoring_common import IndicatorRow, clamp, number


def calculate_price_structure_demark_score(
    rows: Sequence[IndicatorRow],
) -> float | None:
    if not rows:
        return None
    latest = rows[-1]

    signal = str(latest.get("TD_Signal", "")).strip().lower()
    if signal == "buy exhaustion":
        return 100.0
    if signal == "sell exhaustion":
        return -100.0

    countdown = str(latest.get("TD_Countdown_Direction", "")).strip().lower()
    if countdown.startswith("bullish"):
        count = number(latest, "TD_Buy_Countdown") or 0
        return 50 + 50 * clamp(count, 0, 13) / 13
    if countdown.startswith("bearish"):
        count = number(latest, "TD_Sell_Countdown") or 0
        return -(50 + 50 * clamp(count, 0, 13) / 13)

    setup = str(latest.get("TD_Setup_Direction", "")).strip().lower()
    count = number(latest, "TD_Setup_Count") or 0
    setup_strength = 60 * clamp(count, 0, 9) / 9
    if setup.startswith("bullish"):
        return setup_strength
    if setup.startswith("bearish"):
        return -setup_strength
    if "TD_Signal" in latest:
        return 0.0
    return None

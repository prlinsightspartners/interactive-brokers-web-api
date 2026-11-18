from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_AUDIT_LOG = Path(__file__).resolve().parent / "logs" / "ibkr_orders.jsonl"

def append_order_event(
    event_type: str,
    account_id: str,
    *,
    order_id: str | int | None = None,
    order: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
    log_path: Path = DEFAULT_AUDIT_LOG,
) -> None:
    record = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "account_id": account_id,
        "portfolio_id": account_id,
        "order_id": None if order_id is None else str(order_id),
        "order": order or {},
        "details": details or {},
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")
        log.flush()
        os.fsync(log.fileno())

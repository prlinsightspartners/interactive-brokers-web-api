import hashlib
import json
from datetime import datetime, timezone


class DuplicateSignalChecker:

    def __init__(self, db):
        self.db = db

    def generate_signal_id(self, signal):

        payload = {
            "strategy_id": signal["strategy_id"],
            "symbol": signal["symbol"],
            "action": signal["action"],
            "quantity": signal["quantity"],
            "timestamp": signal["timestamp"]
        }

        raw = json.dumps(
            payload,
            sort_keys=True
        )

        return hashlib.sha256(
            raw.encode()
        ).hexdigest()

    def is_duplicate(self, signal_id):

        row = self.db.execute(
            """
            SELECT signal_id
            FROM webhook_signals
            WHERE signal_id = ?
            """,
            (signal_id,)
        ).fetchone()

        return row is not None

    def reserve_signal(self, signal_id):

        try:

            self.db.execute(
                """
                INSERT INTO webhook_signals
                (
                    signal_id,
                    received_at,
                    status
                )
                VALUES (?, ?, ?)
                """,
                (
                    signal_id,
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "RECEIVED"
                )
            )

            self.db.commit()

            return True

        except Exception:
            self.db.rollback()
            return False
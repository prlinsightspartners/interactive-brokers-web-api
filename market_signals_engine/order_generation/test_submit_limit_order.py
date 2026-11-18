from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from market_signals_engine.order_generation.submit_limit_order import (
    generate_order,
    refresh_order_status,
)


def response(payload):
    result = Mock()
    result.raise_for_status.return_value = None
    result.json.return_value = payload
    result.url = "https://localhost:5055/v1/api"
    return result


class LimitOrderGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.audit_log = Path(self.temporary_directory.name) / "orders.jsonl"
        self.session = Mock()
        self.session.get.side_effect = [
            response({"authenticated": True, "connected": True}),
            response([{"accountId": "DU000000"}]),
            response(
                [
                    {
                        "conid": "272093",
                        "symbol": "MSFT",
                        "secType": "STK",
                        "companyHeader": "MICROSOFT CORP (NASDAQ)",
                    }
                ]
            ),
        ]

    def tearDown(self):
        self.temporary_directory.cleanup()

    def generate(self, submit):
        return generate_order(
            session=self.session,
            api_url="https://localhost:5055/v1/api",
            account_id="DU000000",
            symbol="MSFT",
            quantity=1,
            limit_price=Decimal("430"),
            time_in_force="GTC",
            exchange="NASDAQ",
            submit=submit,
            audit_log_path=self.audit_log,
        )

    def test_preview_does_not_submit_and_generates_limit_payload(self):
        result = self.generate(submit=False)

        self.session.post.assert_not_called()
        self.assertEqual(result["conid"], "272093")
        self.assertEqual(
            result["payload"]["orders"][0],
            {
                "conid": 272093,
                "orderType": "LMT",
                "price": 430.0,
                "quantity": 1,
                "side": "BUY",
                "tif": "GTC",
            },
        )

    def test_submission_returns_broker_order_id(self):
        self.session.post.return_value = response(
            [{"order_id": "12345", "order_status": "Submitted"}]
        )

        result = self.generate(submit=True)

        self.assertEqual(result["order_id"], "12345")
        self.session.post.assert_called_once()
        events = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [event["event_type"] for event in events],
            ["submission_requested", "submission_acknowledged"],
        )
        self.assertEqual(events[-1]["account_id"], "DU000000")
        self.assertEqual(events[-1]["order_id"], "12345")
        self.assertEqual(events[-1]["details"]["status"], "Submitted")

    def test_broker_confirmation_prompt_is_not_auto_accepted(self):
        self.session.post.return_value = response(
            [{"id": "warning-1", "messageOptions": ["Yes", "No"]}]
        )

        with self.assertRaisesRegex(RuntimeError, "no warning was auto-accepted"):
            self.generate(submit=True)
        events = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [event["event_type"] for event in events],
            ["submission_requested", "confirmation_required"],
        )
        self.assertEqual(events[-1]["details"]["confirmation_id"], "warning-1")

    def test_request_failure_is_logged_as_unknown_outcome(self):
        import requests

        self.session.post.side_effect = requests.Timeout("timed out")
        with self.assertRaisesRegex(RuntimeError, "outcome is unknown"):
            self.generate(submit=True)
        events = [
            json.loads(line)
            for line in self.audit_log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(events[-1]["event_type"], "submission_outcome_unknown")

    def test_audit_failure_prevents_transmission(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            blocked_log_path = Path(temporary_directory) / "not-a-directory"
            blocked_log_path.write_text("block log directory", encoding="utf-8")
            with self.assertRaises(OSError):
                generate_order(
                    session=self.session,
                    api_url="https://localhost:5055/v1/api",
                    account_id="DU000000",
                    symbol="MSFT",
                    quantity=1,
                    limit_price=Decimal("430"),
                    time_in_force="GTC",
                    exchange="NASDAQ",
                    submit=True,
                    audit_log_path=blocked_log_path / "orders.jsonl",
                )
            self.session.post.assert_not_called()

    def test_non_paper_account_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "DU paper account"):
            generate_order(
                session=self.session,
                api_url="https://localhost:5055/v1/api",
                account_id="LIVE000000",
                symbol="MSFT",
                quantity=1,
                limit_price=Decimal("430"),
                time_in_force="GTC",
                exchange="NASDAQ",
                submit=True,
            )
        self.session.get.assert_not_called()
        self.session.post.assert_not_called()


class OrderStatusAuditTests(unittest.TestCase):
    def test_refresh_records_status_by_account_and_order_id(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            audit_log = Path(temporary_directory) / "orders.jsonl"
            session = Mock()
            session.get.side_effect = [
                response({"authenticated": True, "connected": True}),
                response([{"accountId": "DU000000"}]),
                response(
                    {
                        "orders": [
                            {
                                "orderId": 123,
                                "ticker": "MSFT",
                                "side": "BUY",
                                "totalSize": 1,
                                "filledQuantity": 1,
                                "remainingQuantity": 0,
                                "orderType": "Limit",
                                "price": "530.00",
                                "avgPrice": "525.27",
                                "timeInForce": "GTC",
                                "status": "Filled",
                            }
                        ]
                    }
                ),
            ]
            session.post.return_value = response({"set": "DU000000"})

            result = refresh_order_status(
                session,
                "https://localhost:5055/v1/api",
                "DU000000",
                "123",
                audit_log_path=audit_log,
            )

            self.assertEqual(result["status"], "Filled")
            event = json.loads(audit_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["event_type"], "status_update")
            self.assertEqual(event["portfolio_id"], "DU000000")
            self.assertEqual(event["order_id"], "123")
            self.assertEqual(event["order"]["avgPrice"], "525.27")

    def test_missing_order_status_is_logged_as_unknown(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            audit_log = Path(temporary_directory) / "orders.jsonl"
            session = Mock()
            session.get.side_effect = [
                response({"authenticated": True, "connected": True}),
                response([{"accountId": "DU000000"}]),
                response({"orders": []}),
            ]
            session.post.return_value = response({"set": "DU000000"})

            result = refresh_order_status(
                session,
                "https://localhost:5055/v1/api",
                "DU000000",
                "123",
                audit_log_path=audit_log,
            )

            self.assertEqual(result["status"], "UNKNOWN")
            event = json.loads(audit_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["event_type"], "status_not_found")
            self.assertEqual(event["details"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()

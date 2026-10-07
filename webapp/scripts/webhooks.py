"""TradingView webhook routes."""

from flask import Blueprint, jsonify, request

from scripts.common import WEBHOOK_SECRET, log_webhook_traffic

bp = Blueprint("webhooks", __name__)

@bp.route("/webhook", methods=["POST"])
def webhook():

    try:
        # Get JSON from TradingView (tolerate malformed bodies so we can still log them)
        data = request.get_json(silent=True)
        raw_body = "" if data is not None else request.get_data(as_text=True)

        log_webhook_traffic(data, raw_body)

        data = data or {}

        print("Received webhook:", data)

        # -----------------------------
        # 1. Security Check
        # -----------------------------

        if data.get("secret") != WEBHOOK_SECRET:
            print("Invalid webhook secret")

            return jsonify({
                "status": "error",
                "message": "Invalid secret"
            }), 403

        # -----------------------------
        # 2. Parse Trade Details
        # -----------------------------

        symbol = data["symbol"].upper()

        action = data["strategy"]["order_action"].upper()

        try:
            quantity = int(
                float(
                    data["strategy"]["order_contracts"]
                )
            )

        except (TypeError, ValueError):

            return jsonify({
                "status": "error",
                "message": "Invalid order quantity"
            }), 400

        # -----------------------------
        # 3. Validate Quantity
        # -----------------------------

        if quantity <= 0:

            return jsonify({
                "status": "error",
                "message": "Order quantity must be greater than zero"
            }), 400

        # -----------------------------
        # 4. Validate Action
        # -----------------------------

        if action not in ["BUY", "SELL"]:

            return jsonify({
                "status": "error",
                "message": "Invalid order action"
            }), 400

        # -----------------------------
        # 5. Process Trade
        # -----------------------------

        print(
            f"Processing trade: "
            f"{action} {quantity} {symbol}"
        )

        # Your IBKR trading logic goes here

        return jsonify({
            "status": "success",
            "symbol": symbol,
            "action": action,
            "quantity": quantity
        }), 200

    except Exception as e:

        print("Webhook error:", e)

        return jsonify({
            "status": "error",
            "message": "Internal server error"
        }), 500

"""Flask application setup and route registration."""

import os

from flask import Flask

from portfolio_db import ensure_portfolio_database
from scripts.common import timectime
from scripts.contracts import bp as contracts_blueprint
from scripts.dashboard import bp as dashboard_blueprint
from scripts.orders import bp as orders_blueprint
from scripts.portfolio import bp as portfolio_blueprint
from scripts.scanner import bp as scanner_blueprint
from scripts.webhooks import bp as webhooks_blueprint
from trade_db import ensure_database

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))

# Preserve the existing startup behavior: ensure both local databases exist.
database_connection, database_cursor = ensure_database()
database_cursor.close()
database_connection.close()
portfolio_db_connection, portfolio_db_cursor = ensure_portfolio_database()
portfolio_db_cursor.close()
portfolio_db_connection.close()

app.template_filter("ctime")(timectime)
for blueprint in (
    webhooks_blueprint,
    dashboard_blueprint,
    contracts_blueprint,
    orders_blueprint,
    portfolio_blueprint,
    scanner_blueprint,
):
    app.register_blueprint(blueprint)

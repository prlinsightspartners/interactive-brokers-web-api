# Market Signals Engine

The Compose service calculates daily price indicators and alpha factors from
local per-ticker OHLCV CSV backups. It writes one
`daily_price_indicators_<TICKER>.csv` file per input ticker and appends every
run and per-ticker result to `market_signals_engine/data/daily_price_indicators.log`.
It does not download prices, connect to IBKR, select accounts, or place orders.

## Input format

Each source file must be named `<TICKER>.csv` and contain exactly one ticker
with the following columns (extra source columns are ignored):

```csv
Date,Ticker,Open,High,Low,Close,Volume
2026-11-05,AAPL,268.0,271.0,267.0,270.0,42000000
```

The default input and output host directories are
`C:\Github\options-chain-downloader\data\prices\csv_backups` and
`C:\Github\market_signals_engine\daily_price_indicators`. Override them with
`PRICE_CSV_SOURCE_PATH` and `INDICATORS_OUTPUT_PATH` in `.env` if needed.
Inputs are mounted read-only. High, low, and close must be finite and positive;
open may be zero when unavailable (that is logged and excluded from alpha
calculations). Volume must be non-negative, and ticker/date pairs must be
unique. Rows with inconsistent high/low values are skipped and logged. Rows
dated after the run date are excluded.

## Daily directional scores

After the indicator batch succeeds, the scheduler reads each ticker's latest
indicator row and generates one CSV at
`market_signals_engine/data/scores/daily_directional_scores_YYYY-MM-DD.csv`.
The run log `daily_directional_scores.log` is kept in the same folder.

The component scores are:

- **Trend** (-100 to +100): close vs. EMA-50, EMA-50 vs. EMA-200, and EMA-50
  20-session slope, using bounded normalized returns.
- **Momentum** (-100 to +100): RSI, MACD histogram relative to close, and
  cross-sectional momentum/stochastic alpha ranks.
- **Volume** (-100 to +100): daily return direction scaled by relative volume
  versus the preceding 20 sessions.
- **Price structure / Demark** (-100 to +100): completed Demark exhaustion,
  active countdown, or setup direction and progress.
- **Liquidity** (0 to 100): average-volume and turnover cross-sectional ranks.
- **Volatility** (0 to 100 quality): `Vol_Signal` mapping; medium=100, low=80,
  high=70, extreme=25.

The aggregate directional score is the weighted mean of available directional
components: Trend 30%, Momentum 30%, Volume 20%, and Price Structure / Demark
20%. Liquidity and volatility are deliberately reported as context/quality
scores rather than given a bullish or bearish sign. Missing directional
components are excluded and the CSV reports the available weight coverage.

Classification bands are symmetric: >=80 Strong Bullish, 60 to <80 Bullish,
20 to <60 Moderately Bullish, -20 to <20 Neutral, -60 to <-20 Moderately
Bearish, -80 to <-60 Bearish, and <=-80 Strong Bearish. The output also
includes the signal run date and each ticker's latest indicator date, so stale
or incomplete source histories are visible.

## Run and schedule

Start the engine with the other Compose services:

```bash
docker compose up --build -d market-signals-engine
```

It runs daily at `18:00` in `America/New_York`; change `INDICATOR_RUN_TIME` or
`INDICATOR_TIMEZONE` to override. When started before that day's scheduled time,
it waits and runs at the scheduled time. When started after that time, it waits
until the next day rather than starting an extra run. Indicator run details
are written to `market_signals_engine/data/daily_price_indicators.log`; score
run details are written under `market_signals_engine/data/scores/`.

Run a one-time batch manually from the repository root:

```bash
python -m market_signals_engine.scripts.daily_price_indicators \
  --source-dir "C:\Github\options-chain-downloader\data\prices\csv_backups" \
  --output-dir "C:\Github\market_signals_engine\daily_price_indicators"
```

The existing JSON signal proposal CLI remains available separately via
`market_signals_engine.cli`; the scheduled container job in this setup is the
daily price-indicator batch.

## MLflow experiment tracking

The Compose stack runs MLflow separately from the scheduled engine. Start or
rebuild both services with:

```bash
docker compose up --build -d mlflow market-signals-engine
```

Open the local MLflow UI at <http://localhost:5000>. The UI is bound to
localhost only. MLflow stores its SQLite tracking database and artifacts under
`market_signals_engine/data/mlflow/`, which persists across container
recreations and is excluded from version control.

Each successful scheduled indicator-and-score run is logged to the
`daily-market-signals` experiment. Runs include the generated score CSV,
per-source SHA-256 hashes and latest indicator dates, and summary metrics for
tickers and indicator rows. The full indicator CSVs remain in the configured
indicator output directory rather than being duplicated into MLflow artifacts.
If MLflow is temporarily unavailable, the scheduler logs the tracking error
while keeping the generated indicator and score files.

Use separate experiments for future portfolio workflows, for example
`portfolio-backtests` and `daily-portfolio-weights`. Each backtest should log
its data snapshot/hash, strategy version and parameters, asset universe,
benchmark, rebalance rules, position constraints, and transaction-cost
assumptions. Log comparable performance and risk metrics as metrics, and save
weights, tear sheets, and other reports as artifacts. The tracking server
records these results; it does not run backtests, generate weights, or produce
reports itself.

A backtest runner can use the engine's configured tracking URI directly:

```python
import os
import mlflow

mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
mlflow.set_experiment("portfolio-backtests")
with mlflow.start_run(run_name="my-strategy-2026-11-12"):
    mlflow.log_params(
        {
            "strategy_version": "1",
            "benchmark": "SPY",
            "rebalance_frequency": "daily",
            "transaction_cost_bps": 5,
        }
    )
    mlflow.log_metrics(
        {
            "annualized_return": 0.12,
            "sharpe_ratio": 1.1,
            "max_drawdown": -0.18,
            "turnover": 0.35,
        }
    )
    mlflow.log_artifact("outputs/weights.csv", artifact_path="weights")
    mlflow.log_artifact("outputs/tearsheet.html", artifact_path="reports")
```

In Compose-run scripts, use the `MLFLOW_TRACKING_URI` environment variable
instead of hard-coding the service URL. The example metric values are
illustrative; calculate and validate metrics from each backtest's actual
returns and assumptions.

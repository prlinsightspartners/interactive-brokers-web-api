## Configuration
### config.py Parameters

# Asset Classes (18 total)
ASSET_CLASSES = {
    "domestic_equity": 0.40,
    "fixed_income": 0.35,
    "alternatives": 0.15,
    "currency": 0.10,
}

# Factor Definitions
FACTOR_DEFINITIONS = {
    "market_factors": 5,  # Equity, Rates, Inflation, Credit, Currency
    "style_factors": 9,   # Size, Value, Momentum, Quality, etc.
    "optional_factors": 3,  # Liquidity, Volatility, Carry
}

# Covariance Configuration
COVARIANCE_CONFIG = {
    "ewma_lambda": 0.94,        # Exponential weight
    "lookback_window": 252,      # 1 year
    "shrinkage_target": "identity",  # Ledoit-Wolf target
}

# Beta Configuration
BETA_CONFIG = {
    "estimation_window": 252,    # 1 year
    "kalman_process_noise": 0.02,
    "kalman_measurement_noise": 0.01,
}

# Risk Configuration
RISK_CONFIG = {
    "risk_free_rate": 0.04,  # 4%
    "confidence_level": 0.95,  # 95% for VaR
    "max_asset_exposure": 0.20,  # 20% max
}
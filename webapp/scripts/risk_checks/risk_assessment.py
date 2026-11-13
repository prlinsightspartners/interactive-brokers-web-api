## Typical Workflows
### Workflow 1: Risk Assessment


# 1. Load data

from data import MarketDataFetcher
fetcher = MarketDataFetcher()
prices = fetcher.fetch(["SPY", "AGG", "GLD"], "2022-01-01", "2024-01-01")
returns = prices.pct_change().dropna()

# 2. Estimate covariance

from risk_model import CovarianceEstimator
cov_est = CovarianceEstimator("ledoit_wolf")
cov_matrix = cov_est.estimate(returns)

# 3. Optimize portfolio

from optimization import RiskParityOptimizer
optimizer = RiskParityOptimizer(["SPY", "AGG", "GLD"])
weights = optimizer.optimize(cov_matrix)

# 4. Analyze risk

from risk_model import RiskDecomposition
mcr = RiskDecomposition.marginal_contribution_to_risk(weights, cov_matrix)
ccr = RiskDecomposition.component_contribution_to_risk(weights, cov_matrix)

# 5. Report

from reporting import FactorRiskReport
report = FactorRiskReport("Risk Analysis")
report.add_risk_attribution(systematic_risk=0.12, idiosyncratic_risk=0.03)
print(report.to_html())
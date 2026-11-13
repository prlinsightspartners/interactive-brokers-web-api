### Workflow 2: Stress Testing

# 1-6: [Same as above]
# 7. Scenario analysis
from stress_testing import ScenarioManager
sm = ScenarioManager()
results = sm.apply_scenarios(weights, exposure_matrix)

# 8. Monte Carlo
from simulation import MonteCarloSimulator
sim = MonteCarloSimulator()
paths = sim.simulate_returns(expected_returns, cov_matrix)
var, cvar = sim.calculate_var_cvar(paths)

# 9. Report results
print(f"VaR 95%: {var:.2%}")
print(f"Worst Scenario: {results['pnl'].min():.2%}")

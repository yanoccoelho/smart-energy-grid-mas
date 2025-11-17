from .base_config import SCENARIO_CONFIG

SCENARIO_CONFIG["NAME"] = "High Demand Scenario"
SCENARIO_CONFIG["DESCRIPTION"] = "All households experience a significant increase in consumption, doubling energy demand across every time period."

# high demand: double consumption
for period in SCENARIO_CONFIG["HOUSEHOLDS"]["DEMAND_RANGES"]:
    low, high = SCENARIO_CONFIG["HOUSEHOLDS"]["DEMAND_RANGES"][period]
    SCENARIO_CONFIG["HOUSEHOLDS"]["DEMAND_RANGES"][period] = (low * 2, high * 2)
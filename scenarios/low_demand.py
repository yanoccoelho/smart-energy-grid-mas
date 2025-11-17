from .base_config import clone_config

SCENARIO_CONFIG = clone_config()

SCENARIO_CONFIG["NAME"] = "Low Demand Scenario"
SCENARIO_CONFIG["DESCRIPTION"] = "Household energy consumption is reduced across all time periods, decreasing demand by 40% throughout the microgrid."

# low demand: 40% reduction
for period in SCENARIO_CONFIG["HOUSEHOLDS"]["DEMAND_RANGES"]:
    low, high = SCENARIO_CONFIG["HOUSEHOLDS"]["DEMAND_RANGES"][period]
    SCENARIO_CONFIG["HOUSEHOLDS"]["DEMAND_RANGES"][period] = (low * 0.6, high * 0.6)

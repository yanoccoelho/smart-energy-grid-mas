from .base_config import clone_config

SCENARIO_CONFIG = clone_config()

SCENARIO_CONFIG["NAME"] = "Extreme Producer Failure"
SCENARIO_CONFIG["DESCRIPTION"] = "Renewable producers experience severe instability with a 90% chance of failing each round, remaining offline for 3 to 6 rounds when outages occur."

# extreme producer failure
SCENARIO_CONFIG["PRODUCERS"]["FAILURE_PROB"] = 0.9
SCENARIO_CONFIG["PRODUCERS"]["FAILURE_ROUNDS_RANGE"] = (3, 6)

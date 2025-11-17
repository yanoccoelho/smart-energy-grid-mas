from .base_config import SCENARIO_CONFIG

SCENARIO_CONFIG["NAME"] = "External Grid Failure"
SCENARIO_CONFIG["DESCRIPTION"] = "The external grid becomes unavailable, forcing the microgrid to operate in island mode without importing energy."

# disable external grid
SCENARIO_CONFIG["EXTERNAL_GRID"]["ACCEPTANCE_PROB"] = 0.0
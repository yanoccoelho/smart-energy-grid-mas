from .base_config import clone_config

SCENARIO_CONFIG = clone_config()

SCENARIO_CONFIG["NAME"] = "Blackout Scenario"
SCENARIO_CONFIG["DESCRIPTION"] = "Complete blackout: both solar and wind generation drop to zero, forcing total reliance on storage and external grid."

# blackout: no solar or wind
SCENARIO_CONFIG["PRODUCERS"]["SOLAR_CAPACITY_KW"] = 0.0
SCENARIO_CONFIG["PRODUCERS"]["WIND_CAPACITY_KW"] = 0.0

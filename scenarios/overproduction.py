from .base_config import SCENARIO_CONFIG

SCENARIO_CONFIG["NAME"] = "Overproduction Scenario"
SCENARIO_CONFIG["DESCRIPTION"] = "Solar and wind generation are significantly boosted, creating an energy surplus that tests storage, pricing, and export behavior."

# overproduction: boost solar and wind
SCENARIO_CONFIG["PRODUCERS"]["SOLAR_CAPACITY_KW"] *= 1.8
SCENARIO_CONFIG["PRODUCERS"]["WIND_CAPACITY_KW"] *= 2.0
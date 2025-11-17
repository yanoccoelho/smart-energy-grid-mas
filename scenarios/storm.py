from .base_config import SCENARIO_CONFIG

SCENARIO_CONFIG["NAME"] = "Storm Scenario"
SCENARIO_CONFIG["DESCRIPTION"] = "A severe storm disrupts generation: solar production collapses to 20% while wind output spikes to extreme levels, creating unstable supply conditions."

# storm event: low solar, extreme wind
SCENARIO_CONFIG["PRODUCERS"]["SOLAR_CAPACITY_KW"] *= 0.2
SCENARIO_CONFIG["PRODUCERS"]["WIND_CAPACITY_KW"] *= 3.0
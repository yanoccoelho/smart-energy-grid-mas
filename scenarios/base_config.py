from copy import deepcopy


SE_SCENARIO_CONFIG = {
    "NAME": "Base configuration",
    "DESCRIPTION": "Default smart grid configuration without scenario overrides.",
    "SIMULATION": {
        "XMPP_SERVER": "localhost",
        "NUM_CONSUMERS": 5,
        "NUM_PROSUMERS": 2,
        "ROUND_SLEEP_SECONDS": 10,
        "OFFERS_TIMEOUT": 10,
        "TRANSMISSION_LIMIT_KW": 35.00,
        "AGENT_LIMITS_KW": {
            "prosumer": 5.00,
            "consumer": 3.00,
            "producer": 35.00,
            "storage": 35.00,
            "battery": 35.00,
        },
    },

    "EXTERNAL_GRID": {
        "BUY_PRICE": 0.25,
        "SELL_PRICE": 0.15,
        "MIN_DYNAMIC_PRICE": 0.10,
        "MAX_DYNAMIC_PRICE": 0.30,
        "ACCEPTANCE_PROB": 0.7,
    },

    "PRODUCERS": {
        "SOLAR_CAPACITY_KW": 50.00,
        "WIND_CAPACITY_KW": 50.00,
        "SOLAR_EFFICIENCY": 0.40,
        "WIND_CAPACITY_FACTOR": 0.42,
        "PRODUCTION_NOISE_RANGE": (0.95, 1.05),
        "FAILURE_PROB": 0.20,
        "FAILURE_ROUNDS_RANGE": (1, 4),
    },

    "HOUSEHOLDS": {
        "DEMAND_RANGES": {
            "night": (0.2, 0.6),
            "morning": (0.8, 2.0),
            "afternoon": (0.6, 1.5),
            "evening": (1.2, 3.5),
        },

        "PANEL_AREA_RANGE_M2": (15.00, 25.00),
        "BATTERY_CAPACITY_KWH": 5.00,
        "BATTERY_CHARGE_RATE_KW": 2.00,
        "BATTERY_DISCHARGE_RATE_KW": 2.00,
        "BATTERY_EFFICIENCY": 0.95,
    },

    "STORAGE": {
        "CAPACITY_KWH": 50.00,
        "EMERGENCY_ONLY": True,
        "ASK_PRICE": 0.25,
        "MAX_PRICE": 0.35,
    },

    "ENVIRONMENT": {
        "BASE_WIND_SPEED": 6.00,
        "WIND_NOISE_RANGE": (-2.00, 2.00),
        "BASE_TEMPERATURE": 22.00,
        "TEMP_VARIATION": 5.00,
    },

    "METRICS": {
        "REPORT_INTERVAL_ROUNDS": 5,
    }
}


def clone_config():
    """Return a deep copy of the base scenario configuration."""
    return deepcopy(SE_SCENARIO_CONFIG)


# Backwards compatibility: modules importing SCENARIO_CONFIG directly
# still receive an isolated copy of the base configuration.
SCENARIO_CONFIG = clone_config()

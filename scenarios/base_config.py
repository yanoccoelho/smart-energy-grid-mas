from copy import deepcopy


BASE_SCENARIO_CONFIG = {
    "NAME": "Base configuration",
    "DESCRIPTION": "Default smart grid configuration without scenario overrides.",
    "SIMULATION": {
        "XMPP_SERVER": "localhost",
        "ROUND_SLEEP_SECONDS": 10,
        "OFFERS_TIMEOUT": 10,
        "TRANSMISSION_LIMIT_KW": 3.0,
    },

    "NEIGHBORHOODS": {
        "N1": {
            "NAME": "North District",
            "NEIGHBORS": ["N2"],

            "NUM_CONSUMERS": 3,
            "NUM_PROSUMERS": 1,
            "NUM_STORAGES": 1, 

            "PRODUCERS": {
                "SOLAR_FARMS": 1,
                "WIND_TURBINES": 0
            },
        },

        "N2": {
            "NAME": "South District",
            "NEIGHBORS": ["N1", "N3"],

            "NUM_CONSUMERS": 2,
            "NUM_PROSUMERS": 1,
            "NUM_STORAGES": 0,

            "PRODUCERS": {
                "SOLAR_FARMS": 0,
                "WIND_TURBINES": 1
            },
        },

        # "N3": {
        #     "NAME": "West District",
        #     "NEIGHBORS": ["N2"],

        #     "NUM_CONSUMERS": 1,
        #     "NUM_PROSUMERS": 0,
        #     "NUM_STORAGES": 2,

        #     "PRODUCERS": {
        #         "SOLAR_FARMS": 1,
        #         "WIND_TURBINES": 1
        #     },

        # }
    },

    "EXTERNAL_GRID": {
        "BUY_PRICE": 0.30,
        "SELL_PRICE": 0.11,
        "MIN_DYNAMIC_PRICE": 0.10,
        "MAX_DYNAMIC_PRICE": 0.30,
        "ACCEPTANCE_PROB": 0.8,
    },

    "PRODUCERS": {
        "SOLAR_CAPACITY_KW": 20.0,
        "WIND_CAPACITY_KW": 50.0,
        "SOLAR_EFFICIENCY": 0.20,
        "WIND_CAPACITY_FACTOR": 0.42,
        "PRODUCTION_NOISE_RANGE": (0.95, 1.05),
        "FAILURE_PROB": 0.2,
        "FAILURE_ROUNDS_RANGE": (1, 4),
    },

    "HOUSEHOLDS": {
        "DEMAND_RANGES": {
            "night": (0.5, 1.5),
            "morning": (1.5, 3.0),
            "afternoon": (2.0, 4.0),
            "evening": (1.0, 2.5),
        },
        "PANEL_AREA_RANGE_M2": (15.0, 25.0),
        "BATTERY_CAPACITY_KWH": 5.0,
        "BATTERY_CHARGE_RATE_KW": 2.0,
        "BATTERY_DISCHARGE_RATE_KW": 2.0,
        "BATTERY_EFFICIENCY": 0.95,
    },

    "STORAGE": {
        "CAPACITY_KWH": 50.0,
        "EMERGENCY_ONLY": True,
        "ASK_PRICE": 0.25,
        "MAX_PRICE": 0.35,
    },

    "ENVIRONMENT": {
        "BASE_WIND_SPEED": 6.0,
        "WIND_NOISE_RANGE": (-2.0, 2.0),
        "BASE_TEMPERATURE": 22.0,
        "TEMP_VARIATION": 5.0,
    },

    "METRICS": {
        "REPORT_INTERVAL_ROUNDS": 5,
    }
}


def clone_config():
    """Return a deep copy of the base scenario configuration."""
    return deepcopy(BASE_SCENARIO_CONFIG)


# Backwards compatibility: modules importing SCENARIO_CONFIG directly
# still receive an isolated copy of the base configuration.
SCENARIO_CONFIG = clone_config()

# ==========================
#  SIMULATION PARAMETERS
# ==========================
SIMULATION = {
    "XMPP_SERVER": "localhost",
    "NUM_CONSUMERS": 5,
    "NUM_PROSUMERS": 2,
    "ROUND_SLEEP_SECONDS": 10,
    "OFFERS_TIMEOUT": 10,
    "TRANSMISSION_LIMIT_KW": 3.0,
}

# ==========================
#  EXTERNAL GRID
# ==========================
EXTERNAL_GRID = {
    "BUY_PRICE": 0.30,      # microgrid IMPORTA
    "SELL_PRICE": 0.11,     # microgrid EXPORTA
    "MIN_DYNAMIC_PRICE": 0.10,
    "MAX_DYNAMIC_PRICE": 0.30,
    "ACCEPTANCE_PROB": 0.8,
}

# ==========================
#  PRODUCERS
# ==========================
PRODUCERS = {
    "SOLAR_CAPACITY_KW": 20.0,
    "WIND_CAPACITY_KW": 50.0,
    "SOLAR_EFFICIENCY": 0.20,
    "WIND_CAPACITY_FACTOR": 0.42,
    "PRODUCTION_NOISE_RANGE": (0.95, 1.05),
    "FAILURE_PROB": 0.2,
    "FAILURE_ROUNDS_RANGE": (1, 4),
}

# ==========================
#  HOUSEHOLDS & PROSUMERS
# ==========================
HOUSEHOLDS = {
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
}

# ==========================
#  STORAGE MANAGER
# ==========================
STORAGE = {
    "CAPACITY_KWH": 50.0,
    "EMERGENCY_ONLY": True,
    "ASK_PRICE": 0.25,
    "MAX_PRICE": 0.35,
}

# ==========================
#  ENVIRONMENT
# ==========================
ENVIRONMENT = {
    "BASE_WIND_SPEED": 6.0,
    "WIND_NOISE_RANGE": (-2.0, 2.0),
    "BASE_TEMPERATURE": 22.0,
    "TEMP_VARIATION": 5.0,
}

# ==========================
#  METRICS & LOGGING
# ==========================
METRICS = {
    "REPORT_INTERVAL_ROUNDS": 5,
}

"""Constants for the Linky integration."""

DOMAIN = "ha_linky"

CONSO_API_BASE_URL = "https://conso.boris.sh/api"
USER_AGENT = "ha-linky/2.0.0"

# Config keys
CONF_PRM = "prm"
CONF_TOKEN = "token"
CONF_NAME = "name"
CONF_PRODUCTION = "production"
CONF_COSTS = "costs"

# Statistic ID prefixes
STAT_PREFIX_CONSUMPTION = "linky"
STAT_PREFIX_PRODUCTION = "linky_prod"

# Cost suffix
STAT_COST_SUFFIX = "_cost"

# Sync schedule hours
SYNC_HOURS = [6, 9]

# API endpoints
ENDPOINT_DAILY_CONSUMPTION = "daily_consumption"
ENDPOINT_CONSUMPTION_LOAD_CURVE = "consumption_load_curve"
ENDPOINT_DAILY_PRODUCTION = "daily_production"
ENDPOINT_PRODUCTION_LOAD_CURVE = "production_load_curve"

# Enedis error messages indicating end of data
ENEDIS_LIMIT_ERRORS = [
    "The requested period cannot be anterior to the meter's last activation date",
    "The start date must be greater than the history deadline.",
    "no measure found for this usage point",
]

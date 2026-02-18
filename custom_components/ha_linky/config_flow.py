"""Config flow for the Linky integration."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConsoApiAuthError, ConsoApiClient, ConsoApiConnectionError
from .const import (
    CONF_COSTS,
    CONF_NAME,
    CONF_PRM,
    CONF_PRODUCTION,
    CONF_TOKEN,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PRM): str,
        vol.Required(CONF_TOKEN): str,
        vol.Optional(CONF_NAME, default="Linky"): str,
        vol.Optional(CONF_PRODUCTION, default=False): bool,
    }
)


class LinkyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Linky."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            prm = user_input[CONF_PRM].strip()
            token = user_input[CONF_TOKEN].strip()
            name = user_input.get(CONF_NAME, "Linky").strip()
            production = user_input.get(CONF_PRODUCTION, False)

            # Validate PRM format (14 digits)
            if not re.match(r"^\d{14}$", prm):
                errors[CONF_PRM] = "invalid_prm"
            else:
                # Set unique ID to prevent duplicates
                unique_id = f"{prm}_{production}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                # Validate token by calling the API
                session = async_get_clientsession(self.hass)
                client = ConsoApiClient(session, token, prm)

                try:
                    valid = await client.validate_token()
                    if not valid:
                        errors["base"] = "invalid_auth"
                except ConsoApiConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    _LOGGER.exception("Unexpected error during validation")
                    errors["base"] = "unknown"

                if not errors:
                    return self.async_create_entry(
                        title=name,
                        data={
                            CONF_PRM: prm,
                            CONF_TOKEN: token,
                            CONF_NAME: name,
                            CONF_PRODUCTION: production,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> LinkyOptionsFlow:
        """Get the options flow for this handler."""
        return LinkyOptionsFlow(config_entry)


class LinkyOptionsFlow(OptionsFlow):
    """Handle options for Linky (cost configuration)."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the cost configuration options."""
        errors: dict[str, str] = {}

        current_costs = self._config_entry.options.get(CONF_COSTS, "[]")
        if isinstance(current_costs, list):
            current_costs = json.dumps(current_costs, indent=2, ensure_ascii=False)

        if user_input is not None:
            costs_json = user_input.get(CONF_COSTS, "[]").strip()

            try:
                costs = json.loads(costs_json)
                if not isinstance(costs, list):
                    errors[CONF_COSTS] = "invalid_costs_format"
                else:
                    # Validate each cost config
                    for i, cost in enumerate(costs):
                        if not isinstance(cost, dict):
                            errors[CONF_COSTS] = "invalid_costs_format"
                            break
                        has_price = cost.get("price") is not None
                        has_entity = bool(cost.get("entity_id"))

                        if has_price and has_entity:
                            errors[CONF_COSTS] = "price_and_entity"
                            break
                        if not has_price and not has_entity:
                            errors[CONF_COSTS] = "no_price_or_entity"
                            break
                        if has_entity and any(
                            cost.get(k) for k in ("after", "before", "weekday")
                        ):
                            errors[CONF_COSTS] = "entity_with_time_filter"
                            break

                    if not errors:
                        return self.async_create_entry(
                            title="",
                            data={CONF_COSTS: costs},
                        )
            except json.JSONDecodeError:
                errors[CONF_COSTS] = "invalid_json"

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_COSTS,
                        default=current_costs,
                    ): str,
                }
            ),
            errors=errors,
        )

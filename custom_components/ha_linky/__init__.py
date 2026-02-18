"""The Linky integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConsoApiClient
from .const import CONF_COSTS, CONF_NAME, CONF_PRM, CONF_PRODUCTION, CONF_TOKEN, DOMAIN
from .coordinator import LinkyCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Linky from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    prm = entry.data[CONF_PRM]
    token = entry.data[CONF_TOKEN]
    name = entry.data.get(CONF_NAME, "Linky")
    is_production = entry.data.get(CONF_PRODUCTION, False)

    # Cost configs come from options
    cost_configs = entry.options.get(CONF_COSTS, [])

    client = ConsoApiClient(session, token, prm)
    coordinator = LinkyCoordinator(
        hass, client, prm, name, is_production, cost_configs
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start coordinator (schedules sync + runs initial sync)
    await coordinator.async_setup()

    # Listen for option changes (costs)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data:
        coordinator: LinkyCoordinator = data["coordinator"]
        await coordinator.async_teardown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)

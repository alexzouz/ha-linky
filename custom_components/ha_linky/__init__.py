"""The Linky integration."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ConsoApiClient
from .const import CONF_COSTS, CONF_NAME, CONF_PRM, CONF_PRODUCTION, CONF_TOKEN, DOMAIN
from .coordinator import LinkyCoordinator
from .statistics_helper import (
    format_as_statistics,
    format_history_file,
    group_by_hour,
    import_statistics,
    purge_statistics,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

SERVICE_IMPORT_CSV = "import_csv"
SERVICE_RESET = "reset_statistics"

SERVICE_IMPORT_CSV_SCHEMA = vol.Schema(
    {
        vol.Required("file_path"): str,
        vol.Required("prm"): str,
        vol.Optional("production", default=False): bool,
        vol.Optional("name", default="Linky"): str,
    }
)

SERVICE_RESET_SCHEMA = vol.Schema(
    {
        vol.Required("prm"): str,
        vol.Optional("production", default=False): bool,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Linky integration (register services)."""
    hass.data.setdefault(DOMAIN, {})

    async def handle_import_csv(call: ServiceCall) -> None:
        """Handle the import_csv service call."""
        file_path = call.data["file_path"]
        prm = call.data["prm"]
        is_production = call.data.get("production", False)
        name = call.data.get("name", "Linky")

        path = Path(file_path)
        if not path.is_file():
            _LOGGER.error("CSV file not found: %s", file_path)
            return

        _LOGGER.info("Importing CSV file: %s for PRM %s", file_path, prm)

        records = await hass.async_add_executor_job(_read_csv, path)

        if not records:
            _LOGGER.warning("No valid records found in CSV file: %s", file_path)
            return

        data_points = format_history_file(records)
        _LOGGER.info(
            "Found %d data points in CSV from %s to %s",
            len(data_points), data_points[0].date[:10], data_points[-1].date[:10],
        )

        stats = format_as_statistics(group_by_hour(data_points))
        await import_statistics(hass, prm, name, is_production, False, stats)
        _LOGGER.info("CSV import completed for PRM %s", prm)

    async def handle_reset_statistics(call: ServiceCall) -> None:
        """Handle the reset_statistics service call."""
        try:
            prm = call.data["prm"]
            is_production = call.data.get("production", False)
            await purge_statistics(hass, prm, is_production)
            _LOGGER.info("Statistics reset for PRM %s", prm)
        except Exception:
            _LOGGER.exception("Failed to reset statistics")
            raise

    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_CSV, handle_import_csv,
        schema=SERVICE_IMPORT_CSV_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESET, handle_reset_statistics,
        schema=SERVICE_RESET_SCHEMA,
    )

    return True


def _read_csv(path: Path) -> list[dict[str, str]]:
    """Read a Linky CSV export file (semicolon-delimited, BOM-aware)."""
    records: list[dict[str, str]] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            if row.get("debut") and row.get("kW"):
                records.append({"debut": row["debut"], "kW": row["kW"]})
    return records


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

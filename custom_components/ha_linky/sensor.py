"""Diagnostic sensor for the Linky integration."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_NAME, CONF_PRM, CONF_PRODUCTION, DOMAIN
from .coordinator import LinkyCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Linky sensor from a config entry."""
    coordinator: LinkyCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([LinkySyncStatusSensor(entry, coordinator)])


class LinkySyncStatusSensor(SensorEntity):
    """Sensor showing Linky sync status."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_translation_key = "sync_status"

    def __init__(
        self,
        entry: ConfigEntry,
        coordinator: LinkyCoordinator,
    ) -> None:
        self._coordinator = coordinator
        prm = entry.data[CONF_PRM]
        is_production = entry.data.get(CONF_PRODUCTION, False)
        mode = "prod" if is_production else "conso"

        self._attr_unique_id = f"{prm}_{mode}_sync_status"
        self._attr_device_info = None
        self._attr_extra_state_attributes = {
            "prm": prm,
            "production": is_production,
        }

    @property
    def native_value(self) -> str:
        """Return current sync status."""
        return self._coordinator.status

    @property
    def extra_state_attributes(self) -> dict:
        """Return extra attributes including last sync time."""
        attrs = {
            "prm": self._coordinator.prm,
            "production": self._coordinator.is_production,
        }
        if self._coordinator.last_sync:
            attrs["last_sync"] = self._coordinator.last_sync.isoformat()
        return attrs

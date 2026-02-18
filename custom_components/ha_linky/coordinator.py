"""Linky coordinator - orchestrates fetch, format, and import.

Port of index.ts main sync/init logic.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .api import ConsoApiClient, ConsoApiError
from .cost import EntityHistoryData, compute_costs
from .statistics_helper import (
    DataPoint,
    find_last_statistic,
    format_as_statistics,
    format_daily_data,
    format_load_curve,
    group_by_hour,
    import_statistics,
    increment_sums,
    is_new_prm,
)

_LOGGER = logging.getLogger(__name__)


class LinkyCoordinator:
    """Orchestrate Linky data fetching, formatting, and statistics import."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: ConsoApiClient,
        prm: str,
        name: str,
        is_production: bool,
        cost_configs: list[dict[str, Any]] | None = None,
    ) -> None:
        self.hass = hass
        self.client = client
        self.prm = prm
        self.name = name
        self.is_production = is_production
        self.cost_configs = cost_configs or []
        self.last_sync: datetime | None = None
        self.status: str = "pending"
        self._unsub_listeners: list[CALLBACK_TYPE] = []

    async def async_setup(self) -> None:
        """Set up scheduled sync and run initial sync."""
        random_minute = random.randint(0, 58)
        random_second = random.randint(0, 58)

        keyword = "production" if self.is_production else "consumption"

        _LOGGER.info(
            "Data synchronization for %s planned every day at "
            "06:%02d:%02d and 09:%02d:%02d",
            keyword,
            random_minute, random_second,
            random_minute, random_second,
        )

        # Schedule at 6:xx and 9:xx
        for hour in (6, 9):
            unsub = async_track_time_change(
                self.hass,
                self._async_scheduled_sync,
                hour=hour,
                minute=random_minute,
                second=random_second,
            )
            self._unsub_listeners.append(unsub)

        # Run initial sync
        await self._async_sync()

    async def async_teardown(self) -> None:
        """Remove scheduled listeners."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    @callback
    def _async_scheduled_sync(self, _now: datetime) -> None:
        """Handle scheduled sync callback."""
        self.hass.async_create_task(self._async_sync())

    async def _async_sync(self) -> None:
        """Main sync entry point: init new PRMs or incremental sync."""
        try:
            is_new = await is_new_prm(self.hass, self.prm, self.is_production)
            if is_new:
                await self._async_init()
            else:
                await self._async_incremental_sync()
            self.status = "ok"
            self.last_sync = dt_util.now()
        except Exception:
            self.status = "error"
            _LOGGER.exception(
                "Sync failed for PRM %s (%s)",
                self.prm,
                "production" if self.is_production else "consumption",
            )

    async def _async_init(self) -> None:
        """First-time data import (up to 1 year)."""
        keyword = "production" if self.is_production else "consumption"
        _LOGGER.info(
            "New PRM detected, historical %s data import is starting", keyword
        )

        raw_data = await self.client.get_energy_data(
            is_production=self.is_production, first_day=None
        )

        energy_data = self._format_raw_data(raw_data)
        if not energy_data:
            _LOGGER.warning("No history found for PRM %s", self.prm)
            return

        energy_stats = format_as_statistics(group_by_hour(energy_data))
        await import_statistics(
            self.hass, self.prm, self.name,
            self.is_production, False, energy_stats,
        )

        # Compute and import costs if configured
        if self.cost_configs:
            await self._import_costs(energy_data, energy_stats=None)

    async def _async_incremental_sync(self) -> None:
        """Incremental sync from last known statistic."""
        keyword = "production" if self.is_production else "consumption"
        _LOGGER.info("Synchronization started for %s data", keyword)

        last_stat = await find_last_statistic(
            self.hass, self.prm, self.is_production
        )
        if not last_stat:
            _LOGGER.warning(
                "Data synchronization failed, no previous statistic found"
            )
            return

        # Check if sync is needed: last stat > 2 days old AND current hour >= 6
        last_start = last_stat["start"]
        if isinstance(last_start, (int, float)):
            last_dt = datetime.fromtimestamp(last_start, tz=dt_util.DEFAULT_TIME_ZONE)
        else:
            last_dt = dt_util.parse_datetime(str(last_start))
            if last_dt is None:
                last_dt = datetime.fromisoformat(str(last_start))

        now = dt_util.now()
        is_syncing_needed = (
            last_dt < now - timedelta(days=2) and now.hour >= 6
        )
        if not is_syncing_needed:
            _LOGGER.debug("Everything is up-to-date, nothing to synchronize")
            return

        first_day = (last_dt + timedelta(days=1)).date()
        raw_data = await self.client.get_energy_data(
            is_production=self.is_production, first_day=first_day
        )

        energy_data = self._format_raw_data(raw_data)
        if not energy_data:
            return

        energy_stats = format_as_statistics(group_by_hour(energy_data))
        await import_statistics(
            self.hass, self.prm, self.name,
            self.is_production, False,
            increment_sums(energy_stats, last_stat["sum"]),
        )

        # Compute and import costs if configured
        if self.cost_configs:
            await self._import_costs(energy_data, last_stat=last_stat)

    async def _import_costs(
        self,
        energy_data: list[DataPoint],
        energy_stats: Any = None,
        last_stat: dict[str, Any] | None = None,
    ) -> None:
        """Compute costs and import them as statistics."""
        entity_history = await self._fetch_entity_history(energy_data)
        costs = compute_costs(energy_data, self.cost_configs, entity_history)
        cost_stats = format_as_statistics(group_by_hour(costs))

        if not cost_stats:
            return

        if last_stat is not None:
            # Incremental: find last cost stat and increment
            last_cost = await find_last_statistic(
                self.hass, self.prm, self.is_production, is_cost=True
            )
            base_sum = last_cost["sum"] if last_cost else 0.0
            cost_stats = increment_sums(cost_stats, base_sum)

        await import_statistics(
            self.hass, self.prm, self.name,
            self.is_production, True, cost_stats,
        )

    async def _fetch_entity_history(
        self,
        energy_data: list[DataPoint],
    ) -> EntityHistoryData | None:
        """Fetch entity history for dynamic pricing configurations."""
        if not self.cost_configs:
            return None

        entity_ids = list({
            c["entity_id"]
            for c in self.cost_configs
            if c.get("entity_id")
        })

        if not entity_ids:
            return None

        from homeassistant.components.recorder.history import get_significant_states

        # Determine time range with 1 day buffer
        first_date = energy_data[0].date
        last_date = energy_data[-1].date
        start_time = datetime.fromisoformat(first_date) - timedelta(days=1)
        end_time = datetime.fromisoformat(last_date) + timedelta(days=1)
        # Ensure timezone-aware
        if start_time.tzinfo is None:
            start_time = dt_util.as_local(start_time)
        if end_time.tzinfo is None:
            end_time = dt_util.as_local(end_time)

        entity_history: EntityHistoryData = {}

        for entity_id in entity_ids:
            try:
                states = await self.hass.async_add_executor_job(
                    get_significant_states,
                    self.hass,
                    start_time,
                    end_time,
                    [entity_id],
                )

                history_list = states.get(entity_id, [])
                entries: list[dict[str, Any]] = []

                for state_obj in history_list:
                    if state_obj.state in ("unavailable", "unknown", None):
                        continue
                    try:
                        value = float(state_obj.state)
                    except (ValueError, TypeError):
                        continue
                    unit = state_obj.attributes.get("unit_of_measurement")
                    entries.append({
                        "timestamp": state_obj.last_updated.isoformat(),
                        "value": value,
                        "unit": unit,
                    })

                entity_history[entity_id] = entries
            except Exception:
                _LOGGER.warning(
                    "Failed to fetch history for entity %s", entity_id,
                    exc_info=True,
                )
                entity_history[entity_id] = []

        return entity_history

    def _format_raw_data(
        self, raw_data: list[dict[str, Any]]
    ) -> list[DataPoint]:
        """Detect data type and format accordingly."""
        if not raw_data:
            return []

        # If interval_length is present, it's load curve data
        # If not, it's daily data
        # The raw_data may be mixed (load curve + daily), so check each point
        result: list[DataPoint] = []
        for point in raw_data:
            if point.get("interval_length"):
                result.extend(format_load_curve([point]))
            else:
                result.extend(format_daily_data([point]))
        return result

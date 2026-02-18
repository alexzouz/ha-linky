"""Statistics formatting and import helpers.

Port of format.ts and the statistics parts of ha.ts.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    list_statistic_ids,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    STAT_COST_SUFFIX,
    STAT_PREFIX_CONSUMPTION,
    STAT_PREFIX_PRODUCTION,
)

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------

class DataPoint:
    """Standardized data point (date ISO 8601, value in Wh or EUR)."""

    __slots__ = ("date", "value")

    def __init__(self, date: str, value: float) -> None:
        self.date = date
        self.value = value


class StatisticDataPoint:
    """Data point formatted for HA statistics with cumulative sum."""

    __slots__ = ("start", "state", "sum")

    def __init__(self, start: str, state: float, sum: float) -> None:
        self.start = start
        self.state = state
        self.sum = sum


# --------------------------------------------------------------------------
# Statistic ID helpers
# --------------------------------------------------------------------------

def get_statistic_id(
    prm: str,
    is_production: bool,
    is_cost: bool = False,
) -> str:
    """Build a statistic_id matching the add-on format."""
    prefix = STAT_PREFIX_PRODUCTION if is_production else STAT_PREFIX_CONSUMPTION
    suffix = STAT_COST_SUFFIX if is_cost else ""
    return f"{prefix}:{prm}{suffix}"


def get_source(is_production: bool) -> str:
    """Return the source string."""
    return STAT_PREFIX_PRODUCTION if is_production else STAT_PREFIX_CONSUMPTION


# --------------------------------------------------------------------------
# Formatting functions (port of format.ts)
# --------------------------------------------------------------------------

def format_daily_data(readings: list[dict[str, Any]]) -> list[DataPoint]:
    """Convert raw daily readings to DataPoints."""
    result: list[DataPoint] = []
    for r in readings:
        dt = _parse_date(r["date"])
        result.append(DataPoint(
            date=dt.isoformat(),
            value=float(r["value"]),
        ))
    return result


def format_load_curve(readings: list[dict[str, Any]]) -> list[DataPoint]:
    """Convert raw load curve readings to DataPoints.

    The API returns the interval END, so we subtract interval_length to get
    the interval START.
    """
    result: list[DataPoint] = []
    for r in readings:
        dt = _parse_date(r["date"])
        interval_str = r.get("interval_length", "")
        match = re.search(r"\d+", interval_str or "")
        minutes = float(match.group()) if match else 1.0
        dt = dt - timedelta(minutes=minutes)
        result.append(DataPoint(
            date=dt.isoformat(),
            value=float(r["value"]),
        ))
    return result


def format_history_file(records: list[dict[str, str]]) -> list[DataPoint]:
    """Convert CSV history records to DataPoints.

    CSV format: {'debut': ISO date, 'kW': power value}
    Converts kW to Wh (* 1000).
    """
    result: list[DataPoint] = []
    for r in records:
        raw_kw = r.get("kW", "0").replace(",", ".").replace("null", "0")
        value = float(raw_kw) * 1000  # kW -> Wh
        dt = _parse_date(r["debut"])
        result.append(DataPoint(date=dt.isoformat(), value=value))
    return result


def group_by_hour(data: list[DataPoint]) -> list[DataPoint]:
    """Group data points by hour and compute the average per hour.

    Round result to 2 decimal places.
    """
    grouped: dict[str, list[float]] = defaultdict(list)
    for point in data:
        dt = _parse_date(point.date)
        hour_dt = dt.replace(minute=0, second=0, microsecond=0)
        key = hour_dt.isoformat()
        grouped[key].append(point.value)

    result: list[DataPoint] = []
    for date_key in sorted(grouped.keys()):
        values = grouped[date_key]
        avg = round(sum(values) / len(values), 2)
        result.append(DataPoint(date=date_key, value=avg))
    return result


def format_as_statistics(data: list[DataPoint]) -> list[StatisticDataPoint]:
    """Convert DataPoints to StatisticDataPoints with cumulative sum."""
    result: list[StatisticDataPoint] = []
    for i, point in enumerate(data):
        cumsum = point.value + (result[i - 1].sum if i > 0 else 0.0)
        result.append(StatisticDataPoint(
            start=point.date,
            state=point.value,
            sum=cumsum,
        ))
    return result


def increment_sums(
    data: list[StatisticDataPoint],
    base_sum: float,
) -> list[StatisticDataPoint]:
    """Add base_sum offset to all cumulative sums."""
    return [
        StatisticDataPoint(start=p.start, state=p.state, sum=p.sum + base_sum)
        for p in data
    ]


# --------------------------------------------------------------------------
# HA statistics import functions (port of ha.ts save/find logic)
# --------------------------------------------------------------------------

async def import_statistics(
    hass: HomeAssistant,
    prm: str,
    name: str,
    is_production: bool,
    is_cost: bool,
    stats: list[StatisticDataPoint],
) -> None:
    """Import statistics into Home Assistant recorder."""
    statistic_id = get_statistic_id(prm, is_production, is_cost)
    source = get_source(is_production)

    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        mean_type=StatisticMeanType.NONE,
        name=f"{name} (costs)" if is_cost else name,
        source=source,
        statistic_id=statistic_id,
        unit_of_measurement="€" if is_cost else UnitOfEnergy.WATT_HOUR,
    )

    stat_data: list[StatisticData] = []
    for point in stats:
        dt = _parse_date(point.start)
        # Ensure timezone-aware
        if dt.tzinfo is None:
            dt = dt_util.as_local(dt)
        stat_data.append(StatisticData(
            start=dt,
            state=point.state,
            sum=point.sum,
        ))

    async_add_external_statistics(hass, metadata, stat_data)
    _LOGGER.debug(
        "Imported %d statistics for %s", len(stat_data), statistic_id
    )


async def find_last_statistic(
    hass: HomeAssistant,
    prm: str,
    is_production: bool,
    is_cost: bool = False,
) -> dict[str, Any] | None:
    """Find the most recent statistic for a given PRM.

    Returns dict with 'start', 'state', 'sum' keys or None.
    """
    statistic_id = get_statistic_id(prm, is_production, is_cost)

    result = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum", "state"}
    )

    if statistic_id not in result or not result[statistic_id]:
        if not is_cost:
            _LOGGER.warning("PRM %s not found in Home Assistant statistics", prm)
        return None

    last = result[statistic_id][0]
    _LOGGER.debug("Last saved statistic date is %s", last.get("start"))
    return last


async def is_new_prm(
    hass: HomeAssistant,
    prm: str,
    is_production: bool,
) -> bool:
    """Check if a PRM has no statistics yet."""
    statistic_id = get_statistic_id(prm, is_production)
    stat_ids = await hass.async_add_executor_job(
        list_statistic_ids, hass
    )
    return not any(s["statistic_id"] == statistic_id for s in stat_ids)


async def purge_statistics(
    hass: HomeAssistant,
    prm: str,
    is_production: bool,
) -> None:
    """Remove all statistics for a PRM (energy + cost)."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import clear_statistics

    stat_id = get_statistic_id(prm, is_production, is_cost=False)
    stat_id_cost = get_statistic_id(prm, is_production, is_cost=True)

    _LOGGER.warning("Removing all statistics for PRM %s", prm)
    instance = get_instance(hass)
    await hass.async_add_executor_job(
        clear_statistics, instance, [stat_id, stat_id_cost]
    )


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------

def _parse_date(date_str: str) -> datetime:
    """Parse an ISO 8601 date string to a datetime object."""
    try:
        dt = datetime.fromisoformat(date_str)
    except ValueError:
        dt = dt_util.parse_datetime(date_str)
        if dt is None:
            raise ValueError(f"Cannot parse date: {date_str}")
    return dt

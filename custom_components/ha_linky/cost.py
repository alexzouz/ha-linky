"""Cost calculation logic.

Direct port of cost.ts. Pure logic, no I/O.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from .statistics_helper import DataPoint, _parse_date

_LOGGER = logging.getLogger(__name__)

# Type alias for entity history data
EntityHistoryData = dict[str, list[dict[str, Any]]]

# Weekday mapping: Python weekday() -> 3-letter abbreviation
_WEEKDAY_MAP = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def compute_costs(
    energy: list[DataPoint],
    cost_configs: list[dict[str, Any]],
    entity_history: EntityHistoryData | None = None,
) -> list[DataPoint]:
    """Compute costs for energy data points based on cost configurations."""
    result: list[DataPoint] = []

    for point in energy:
        matching = _find_matching_cost_config(point, cost_configs)
        if not matching:
            continue

        price: float | None = None

        if matching.get("entity_id") and entity_history:
            price = _find_price_from_entity_history(
                point, matching["entity_id"], entity_history
            )
            if price is None:
                continue
        elif matching.get("price") is not None:
            price = matching["price"]
        else:
            continue

        # cost = price(EUR/kWh) * energy(Wh) / 1000
        cost = round(price * point.value) / 1000
        result.append(DataPoint(date=point.date, value=cost))

    if result:
        _LOGGER.info(
            "Successfully computed the cost of %d data points from %s to %s",
            len(result), result[0].date[:10], result[-1].date[:10],
        )
    else:
        _LOGGER.info(
            "No cost computed for the %d points. "
            "No matching cost configuration found (out of %d)",
            len(energy), len(cost_configs),
        )

    return result


def _find_matching_cost_config(
    point: DataPoint,
    configs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find the first cost config matching a data point."""
    for config in configs:
        if not config.get("price") and not config.get("entity_id"):
            continue

        point_dt = _parse_date(point.date)

        # Check start_date filter
        if config.get("start_date"):
            config_start = _parse_date(config["start_date"])
            if point_dt < config_start:
                continue

        # Check end_date filter
        if config.get("end_date"):
            config_end = _parse_date(config["end_date"])
            if point_dt >= config_end:
                continue

        # Check weekday filter
        weekdays = config.get("weekday")
        if weekdays and len(weekdays) > 0:
            day_abbr = _WEEKDAY_MAP.get(point_dt.weekday(), "")
            if day_abbr not in weekdays:
                continue

        # Check after filter
        after = config.get("after")
        if after:
            parts = after.split(":")
            after_hour = int(parts[0])
            after_minute = int(parts[1]) if len(parts) > 1 else 0
            if point_dt.hour < after_hour:
                continue
            if point_dt.hour == after_hour and point_dt.minute < after_minute:
                continue

        # Check before filter
        before = config.get("before")
        if before:
            parts = before.split(":")
            before_hour = int(parts[0])
            before_minute = int(parts[1]) if len(parts) > 1 else 0
            if point_dt.hour > before_hour:
                continue
            if point_dt.hour == before_hour and point_dt.minute >= before_minute:
                continue

        return config

    return None


def _find_price_from_entity_history(
    point: DataPoint,
    entity_id: str,
    entity_history: EntityHistoryData,
) -> float | None:
    """Find the most recent price from entity history at or before the data point."""
    history = entity_history.get(entity_id)
    if not history:
        return None

    point_dt = _parse_date(point.date)

    last_valid_price: float | None = None
    last_valid_unit: str | None = None

    for entry in history:
        entry_dt = _parse_date(entry["timestamp"])
        if entry_dt > point_dt:
            break
        last_valid_price = entry["value"]
        last_valid_unit = entry.get("unit")

    if last_valid_price is None:
        return None

    return _convert_price_unit(last_valid_price, last_valid_unit)


def _convert_price_unit(price: float, unit: str | None) -> float:
    """Convert price to EUR/kWh based on the unit string.

    Order matches the original TS implementation for backward compatibility.
    """
    if not unit:
        return price

    lower = unit.lower()

    # Handle cents (c€/kWh, cent/kWh, ¢/kWh, etc.)
    if "c\u20ac" in lower or "cent" in lower or "\u00a2" in lower:
        return price / 100

    # Handle EUR/MWh
    if "eur/mwh" in lower or "\u20ac/mwh" in lower:
        return price / 1000

    # Handle cents/MWh (note: effectively unreachable due to "cent" check above,
    # kept for parity with TS code)
    if "cent/mwh" in lower or "c\u20ac/mwh" in lower:
        return price / 100000

    # Default: assume EUR/kWh
    return price

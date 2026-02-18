"""Async client for the Conso API (conso.boris.sh)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import aiohttp

from .const import (
    CONSO_API_BASE_URL,
    ENDPOINT_CONSUMPTION_LOAD_CURVE,
    ENDPOINT_DAILY_CONSUMPTION,
    ENDPOINT_DAILY_PRODUCTION,
    ENDPOINT_PRODUCTION_LOAD_CURVE,
    ENEDIS_LIMIT_ERRORS,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class ConsoApiError(Exception):
    """Base exception for Conso API errors."""


class ConsoApiAuthError(ConsoApiError):
    """Authentication error."""


class ConsoApiConnectionError(ConsoApiError):
    """Connection error."""


class ConsoApiClient:
    """Async client for the Conso API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        prm: str,
    ) -> None:
        self._session = session
        self._token = token
        self._prm = prm

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "User-Agent": USER_AGENT,
        }

    async def _request(
        self,
        endpoint: str,
        start: str,
        end: str,
    ) -> list[dict[str, Any]]:
        """Make a request to the Conso API."""
        url = f"{CONSO_API_BASE_URL}/{endpoint}"
        params = {"prm": self._prm, "start": start, "end": end}

        try:
            async with self._session.get(
                url, headers=self._headers(), params=params
            ) as resp:
                if resp.status == 401:
                    raise ConsoApiAuthError("Invalid token")
                if resp.status == 403:
                    raise ConsoApiAuthError("Access forbidden")
                if resp.status != 200:
                    body = await resp.json()
                    error_desc = (
                        body.get("error", {}).get("error_description", "")
                        if isinstance(body, dict)
                        else ""
                    )
                    raise ConsoApiError(
                        f"API error {resp.status}: {error_desc or resp.reason}"
                    )
                data = await resp.json()
                return data.get("interval_reading", [])
        except aiohttp.ClientError as err:
            raise ConsoApiConnectionError(
                f"Connection error: {err}"
            ) from err

    async def get_daily_consumption(
        self, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Fetch daily consumption data."""
        return await self._request(ENDPOINT_DAILY_CONSUMPTION, start, end)

    async def get_consumption_load_curve(
        self, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Fetch consumption load curve (30min intervals)."""
        return await self._request(ENDPOINT_CONSUMPTION_LOAD_CURVE, start, end)

    async def get_daily_production(
        self, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Fetch daily production data."""
        return await self._request(ENDPOINT_DAILY_PRODUCTION, start, end)

    async def get_production_load_curve(
        self, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Fetch production load curve (30min intervals)."""
        return await self._request(ENDPOINT_PRODUCTION_LOAD_CURVE, start, end)

    async def validate_token(self) -> bool:
        """Validate the token by making a test API call."""
        today = date.today()
        start = (today - timedelta(days=2)).isoformat()
        end = today.isoformat()
        try:
            await self._request(ENDPOINT_DAILY_CONSUMPTION, start, end)
            return True
        except ConsoApiAuthError:
            return False
        except ConsoApiError:
            # Other errors (e.g. no data) still mean the token is valid
            return True

    async def get_energy_data(
        self,
        is_production: bool,
        first_day: date | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch energy data using the same 3-tier strategy as the TS add-on.

        1. Load curve for last 7 days (30min intervals)
        2. Daily data in 2 chunks of ~179 days each (up to 1 year)
        """
        history: list[list[dict[str, Any]]] = []
        offset = 0
        limit_reached = False
        today = date.today()
        keyword = "production" if is_production else "consumption"

        # Tier 1: Load curve for last 7 days
        interval = 7
        from_date = today - timedelta(days=offset + interval)
        from_str = from_date.isoformat()

        if first_day and from_date <= first_day:
            from_str = first_day.isoformat()
            limit_reached = True

        to_str = (today - timedelta(days=offset)).isoformat()

        try:
            if is_production:
                data = await self.get_production_load_curve(from_str, to_str)
            else:
                data = await self.get_consumption_load_curve(from_str, to_str)
            history.insert(0, data)
            _LOGGER.debug(
                "Retrieved %s load curve from %s to %s", keyword, from_str, to_str
            )
            offset += interval
        except ConsoApiError as err:
            _LOGGER.debug(
                "Cannot fetch %s load curve from %s to %s: %s",
                keyword, from_str, to_str, err,
            )

        # Tier 2 & 3: Daily data in 2 chunks
        max_loops = 2
        for loop in range(max_loops):
            if limit_reached:
                break

            interval = (365 - 7) / max_loops
            from_date = today - timedelta(days=offset + interval)
            from_str = from_date.isoformat()
            to_str = (today - timedelta(days=offset)).isoformat()

            if first_day and from_date <= first_day:
                from_str = first_day.isoformat()
                limit_reached = True

            try:
                if is_production:
                    data = await self.get_daily_production(from_str, to_str)
                else:
                    data = await self.get_daily_consumption(from_str, to_str)
                history.insert(0, data)
                _LOGGER.debug(
                    "Retrieved daily %s data from %s to %s",
                    keyword, from_str, to_str,
                )
                offset += interval
            except ConsoApiError as err:
                error_msg = str(err)
                if not first_day and any(
                    msg in error_msg for msg in ENEDIS_LIMIT_ERRORS
                ):
                    _LOGGER.info("All available %s data has been imported", keyword)
                    break
                _LOGGER.debug(
                    "Cannot fetch daily %s data from %s to %s: %s",
                    keyword, from_str, to_str, err,
                )
                break

        # Flatten all tiers
        all_points: list[dict[str, Any]] = []
        for tier in history:
            all_points.extend(tier)

        if not all_points:
            _LOGGER.warning("Data import returned nothing!")
        else:
            first_dt = all_points[0].get("date", "?")
            last_dt = all_points[-1].get("date", "?")
            _LOGGER.info(
                "Data import returned %d data points from %s to %s",
                len(all_points), first_dt, last_dt,
            )

        return all_points

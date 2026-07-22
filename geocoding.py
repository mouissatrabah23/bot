"""
geocoding.py — reverse-geocode hotspot coordinates to a real place name.

Uses the free Nominatim (OpenStreetMap) service — no API key needed — to turn a
hotspot's (lat, lon) into the nearest city/town/village/commune name, so alerts
can say "بجاية — 14 كم جنوب شرق" instead of just the wilaya.

We are careful to respect Nominatim's usage policy:
  * every request sends a descriptive ``User-Agent`` (required)
  * requests are rate-limited to <= 1 per second (we use ~1.1s)
  * results are cached in SQLite keyed by the ~1.1km cell (lat/lon rounded to
    2dp) and kept indefinitely, so the same area is queried at most once

Any failure (timeout, HTTP error, blocked) degrades gracefully: the caller
falls back to the existing wilaya-based naming. Geocoding must never block or
crash an alert.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"

# Nominatim's policy requires a descriptive User-Agent identifying the app.
DEFAULT_USER_AGENT = (
    "YaqadhaBot/1.0 (wildfire alert bot for Algeria; "
    "contact: @YaqadhaDZ_bot on Telegram)"
)

# Address keys we accept, most specific first, before falling back to wilaya.
_PLACE_KEYS = ("city", "town", "village", "municipality")


@dataclass(frozen=True)
class GeoPlace:
    """A resolved place: a display name and the place's center coordinates."""

    name: str
    lat: float
    lon: float


def cell_key(lat: float, lon: float) -> str:
    """Cache key: lat/lon rounded to 2 decimals (~1.1km)."""
    return f"{round(lat, 2)}:{round(lon, 2)}"


class Geocoder:
    """Rate-limited, cached reverse geocoder backed by Nominatim."""

    def __init__(
        self,
        db,
        user_agent: str = DEFAULT_USER_AGENT,
        language: str = "ar",
        min_interval: float = 1.1,
        timeout: float = 15.0,
    ):
        self.db = db
        self.user_agent = user_agent
        self.language = language
        self.min_interval = min_interval
        self.timeout = timeout
        # Monotonic timestamp of the last *network* call, for rate limiting.
        self._last_call = 0.0

    def is_cached(self, lat: float, lon: float) -> bool:
        """True if this cell has already been queried (hit or resolved-miss)."""
        return self.db.geocode_cache_get(cell_key(lat, lon)) is not None

    async def resolve(
        self, lat: float, lon: float, allow_network: bool = True
    ) -> tuple[Optional[GeoPlace], bool]:
        """
        Resolve (lat, lon) to a GeoPlace.

        Returns ``(place_or_None, network_used)``:
          * a GeoPlace when a name is known (from cache or a fresh query)
          * None when there's no usable name (resolved-miss, or a failure with
            ``allow_network`` False) — the caller should fall back to the wilaya
          * ``network_used`` lets the caller budget how many live requests it
            makes in one polling cycle.
        """
        cell = cell_key(lat, lon)
        cached = self.db.geocode_cache_get(cell)
        if cached is not None:
            name = cached["name"]
            if name:
                return GeoPlace(name, cached["place_lat"], cached["place_lon"]), False
            return None, False  # cached "no name here" — don't re-query

        if not allow_network:
            return None, False

        fetched = await self._fetch(lat, lon)
        if fetched is None:
            # Network/HTTP failure: do NOT cache, so we can retry later.
            return None, True

        name, place_lat, place_lon = fetched
        self.db.geocode_cache_set(cell, name, place_lat, place_lon)
        if name:
            return GeoPlace(name, place_lat, place_lon), True
        return None, True  # valid response but no city/town — resolved miss

    async def _fetch(
        self, lat: float, lon: float
    ) -> Optional[tuple[str, float, float]]:
        """
        Query Nominatim once (rate-limited). Returns ``(name, place_lat,
        place_lon)`` where ``name`` may be '' if no suitable place was found, or
        None on any network/parse failure.
        """
        # Enforce >= min_interval seconds since the last network call.
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

        params = {
            "lat": lat,
            "lon": lon,
            "format": "json",
            "accept-language": self.language,
            "zoom": 10,
        }
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(NOMINATIM_URL, params=params, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("Nominatim request failed (network): %s", exc)
            return None

        if response.status_code != 200:
            logger.warning("Nominatim returned HTTP %s", response.status_code)
            return None

        try:
            data = response.json()
        except ValueError as exc:
            logger.warning("Nominatim returned non-JSON: %s", exc)
            return None

        return self._parse(data, lat, lon)

    @staticmethod
    def _parse(data: dict, fallback_lat: float, fallback_lon: float):
        """Pull the best place name + its coordinates out of a Nominatim reply."""
        address = data.get("address") or {}
        name = ""
        for key in _PLACE_KEYS:
            value = address.get(key)
            if value:
                name = str(value).strip()
                break

        # The place's own centre, used to describe distance/direction to the
        # hotspot. Fall back to the queried point if the reply omits it.
        try:
            place_lat = float(data.get("lat", fallback_lat))
            place_lon = float(data.get("lon", fallback_lon))
        except (TypeError, ValueError):
            place_lat, place_lon = fallback_lat, fallback_lon

        return name, place_lat, place_lon

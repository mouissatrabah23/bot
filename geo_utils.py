"""
geo_utils.py — geospatial helpers for Yaqadha.

Provides:
  * haversine() — great-circle distance between two lat/lon points, in km
  * bearing_to_compass() — initial bearing between two points as N/NE/E/...
  * load_wilayas() — read wilayas.json once and cache it
  * find_wilaya_by_name() — fuzzy Arabic/French/English wilaya lookup
  * nearest_wilaya() — closest wilaya center to a given point
  * wilaya_contains() — coarse "is this point inside the wilaya" check
  * to_algeria_time() — convert a UTC datetime to Algeria local time (UTC+1)
"""

from __future__ import annotations

import json
import math
import os
import unicodedata
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

# Mean Earth radius in kilometers (used by the haversine formula).
EARTH_RADIUS_KM = 6371.0088

# Algeria's rough bounding box, reused by firms_client for the API query and by
# tests. (min_lon, min_lat, max_lon, max_lat) — FIRMS wants W,S,E,N order.
ALGERIA_BBOX = (-8.7, 18.9, 12.0, 37.1)

_WILAYAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wilayas.json")


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Great-circle distance between two (lat, lon) points in kilometers.

    Uses the haversine formula, which is numerically stable for the small-to-
    medium distances we care about here.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Initial compass bearing (degrees, 0..360) when travelling from point 1 to
    point 2 along a great circle. 0° = North, 90° = East.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_lambda = math.radians(lon2 - lon1)

    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        d_lambda
    )
    theta = math.atan2(x, y)
    return (math.degrees(theta) + 360.0) % 360.0


_COMPASS_POINTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def bearing_to_compass(bearing: float) -> str:
    """Convert a 0..360 bearing into one of 8 compass codes (N, NE, E, ...)."""
    # 8 sectors of 45°, offset by half a sector so N spans 337.5..22.5.
    index = int((bearing + 22.5) % 360 // 45)
    return _COMPASS_POINTS[index]


def direction_from_to(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    """Compass code describing the direction of point 2 as seen from point 1."""
    return bearing_to_compass(initial_bearing(lat1, lon1, lat2, lon2))


@lru_cache(maxsize=1)
def load_wilayas() -> list[dict]:
    """Load and cache the 58 wilayas from wilayas.json."""
    with open(_WILAYAS_FILE, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data["wilayas"]


def _strip_diacritics(text: str) -> str:
    """Lower-case, strip accents/Arabic diacritics and surrounding whitespace."""
    text = text.strip().lower()
    # Decompose and drop combining marks (handles French accents and the common
    # Arabic short-vowel diacritics users sometimes type).
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    # Normalize a few Arabic letter variants so "الجزائر"/"الجزاير" style typos
    # and hamza forms still match.
    replacements = {
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ة": "ه", "ى": "ي", "ئ": "ي", "ؤ": "و",
    }
    for src, dst in replacements.items():
        stripped = stripped.replace(src, dst)
    return stripped


def find_wilaya_by_name(name: str) -> Optional[dict]:
    """
    Look up a wilaya by its Arabic, French, or English name.

    Matching is diacritic-insensitive and tolerant of the leading Arabic
    article "ال". Returns the wilaya dict, or None if nothing matches.
    """
    if not name or not name.strip():
        return None

    query = _strip_diacritics(name)
    # Also try a variant without the Arabic definite article "ال".
    query_no_article = query[2:] if query.startswith("ال") else query

    for wilaya in load_wilayas():
        for key in ("ar", "fr", "en"):
            candidate = _strip_diacritics(wilaya[key])
            candidate_no_article = (
                candidate[2:] if candidate.startswith("ال") else candidate
            )
            if query in (candidate, candidate_no_article) or query_no_article in (
                candidate,
                candidate_no_article,
            ):
                return wilaya
    return None


def nearest_wilaya(lat: float, lon: float) -> dict:
    """Return the wilaya whose center is closest to the given point."""
    return min(
        load_wilayas(),
        key=lambda w: haversine(lat, lon, w["lat"], w["lon"]),
    )


def wilaya_contains(wilaya: dict, lat: float, lon: float) -> bool:
    """
    Coarse test for whether a point falls "inside" a wilaya.

    We don't have official polygons, so we approximate each wilaya as a circle
    of ``radius_km`` around its center. This is deliberately generous — for an
    early-warning tool an occasional over-inclusive match is safer than a miss.
    """
    return haversine(lat, lon, wilaya["lat"], wilaya["lon"]) <= wilaya["radius_km"]


def in_algeria_bbox(lat: float, lon: float) -> bool:
    """Quick check that a point is within Algeria's rough bounding box."""
    min_lon, min_lat, max_lon, max_lat = ALGERIA_BBOX
    return (min_lat <= lat <= max_lat) and (min_lon <= lon <= max_lon)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
# Algeria observes UTC+1 all year with no daylight saving. We prefer the IANA
# zone via zoneinfo (so we stay correct automatically if the policy ever
# changes), and fall back to a fixed +1 offset when the tz database isn't
# available (e.g. a minimal Windows install without the `tzdata` package).
try:
    from zoneinfo import ZoneInfo

    ALGERIA_TZ = ZoneInfo("Africa/Algiers")
except Exception:  # ZoneInfoNotFoundError, ImportError, ...
    ALGERIA_TZ = timezone(timedelta(hours=1), name="UTC+1")


def to_algeria_time(dt: datetime) -> datetime:
    """
    Convert a datetime to Algeria local time.

    A naive datetime is assumed to be UTC (FIRMS timestamps are UTC). The result
    is a timezone-aware datetime in Africa/Algiers (UTC+1).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ALGERIA_TZ)

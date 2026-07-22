"""
firms_client.py — thin async wrapper around the NASA FIRMS area API.

FIRMS ("Fire Information for Resource Management System") exposes active
fire / thermal-anomaly detections. We use the *area* endpoint, which returns
CSV for a bounding box:

    https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{DATASET}/{AREA}/{DAY_RANGE}[/{DATE}]

  * MAP_KEY   — your free FIRMS key
  * DATASET   — e.g. VIIRS_SNPP_NRT, VIIRS_NOAA20_NRT, MODIS_NRT
  * AREA      — "west,south,east,north" (we use Algeria's bbox)
  * DAY_RANGE — 1..10 days of data
  * DATE      — optional YYYY-MM-DD start; omitted = most recent

Free key limit: 5000 transactions / 10 minutes, so hourly polling is safe.

This module never raises on network / API problems: :func:`fetch_hotspots`
returns ``None`` on failure so the caller can simply retry next cycle, and an
empty list when the box genuinely has no detections.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from geo_utils import ALGERIA_BBOX

logger = logging.getLogger(__name__)

FIRMS_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# VIIRS confidence is categorical (low/nominal/high); we map to an order so
# MIN_CONFIDENCE filtering works uniformly.
_VIIRS_CONFIDENCE_ORDER = {"l": 0, "n": 1, "h": 2}


@dataclass(frozen=True)
class Hotspot:
    """A single FIRMS thermal detection, normalized across datasets."""

    latitude: float
    longitude: float
    confidence: str          # raw FIRMS value: "l"/"n"/"h" (VIIRS) or "0".."100" (MODIS)
    acq_date: str            # YYYY-MM-DD (UTC)
    acq_time: str            # HHMM (UTC)
    satellite: str
    instrument: str
    frp: Optional[float]     # Fire Radiative Power (MW), when provided
    daynight: str            # "D" / "N"

    @property
    def detected_at(self) -> datetime:
        """Detection timestamp as a timezone-aware UTC datetime."""
        time_str = self.acq_time.zfill(4)
        return datetime.strptime(
            f"{self.acq_date} {time_str}", "%Y-%m-%d %H%M"
        ).replace(tzinfo=timezone.utc)

    @property
    def detected_display(self) -> str:
        """Human-friendly UTC timestamp, e.g. '2026-07-22 14:30'."""
        return self.detected_at.strftime("%Y-%m-%d %H:%M")

    @property
    def cell(self) -> str:
        """
        The ~1km grid cell this detection falls in (rounded lat:lon). Used to
        track how many distinct days a spot recurs, which flags static thermal
        sources such as gas flares.
        """
        return f"{round(self.latitude, 2)}:{round(self.longitude, 2)}"

    def dedupe_key(self) -> str:
        """
        Stable identity for caching. Rounds coordinates to ~1km so the same
        pixel across satellites/passes in a cycle collapses, but a genuinely new
        or moved detection produces a new key.
        """
        return f"{self.cell}:{self.acq_date}:{self.acq_time}"


def _parse_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_csv(csv_text: str) -> list[Hotspot]:
    """
    Parse a FIRMS area-CSV response into a list of :class:`Hotspot`.

    Tolerant of the column differences between VIIRS and MODIS datasets and of
    blank / malformed rows (which are skipped rather than raising).
    """
    hotspots: list[Hotspot] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return hotspots

    for row in reader:
        lat = _parse_float(row.get("latitude", ""))
        lon = _parse_float(row.get("longitude", ""))
        if lat is None or lon is None:
            continue  # skip unusable rows without crashing the whole poll

        hotspots.append(
            Hotspot(
                latitude=lat,
                longitude=lon,
                confidence=(row.get("confidence") or "").strip(),
                acq_date=(row.get("acq_date") or "").strip(),
                acq_time=(row.get("acq_time") or "").strip(),
                satellite=(row.get("satellite") or "").strip(),
                instrument=(row.get("instrument") or "").strip(),
                frp=_parse_float(row.get("frp", "")),
                daynight=(row.get("daynight") or "").strip(),
            )
        )
    return hotspots


def passes_confidence(hotspot: Hotspot, min_confidence: Optional[str]) -> bool:
    """
    Return True if the hotspot meets the configured minimum confidence.

    ``min_confidence`` may be a VIIRS category (l/n/h) or a MODIS threshold
    (0-100). An empty/None value means "accept everything".
    """
    if not min_confidence:
        return True

    raw = (hotspot.confidence or "").strip().lower()
    threshold = min_confidence.strip().lower()

    # Categorical (VIIRS) comparison.
    if threshold in _VIIRS_CONFIDENCE_ORDER and raw in _VIIRS_CONFIDENCE_ORDER:
        return _VIIRS_CONFIDENCE_ORDER[raw] >= _VIIRS_CONFIDENCE_ORDER[threshold]

    # Numeric (MODIS) comparison.
    raw_num = _parse_float(raw)
    thr_num = _parse_float(threshold)
    if raw_num is not None and thr_num is not None:
        return raw_num >= thr_num

    # Datasets/thresholds we can't compare: don't silently drop real detections.
    return True


class FirmsClient:
    """Async client for one FIRMS dataset over Algeria's bounding box."""

    # FIRMS documents a 1..10 day range for the area endpoint, but the NRT
    # datasets currently reject anything above 5 with HTTP 400 ("Invalid day
    # range. Expects [1..5]."). We clamp to the documented ceiling and rely on
    # fetch_hotspots' graceful 400 handling if a given dataset is stricter.
    MAX_DAY_RANGE = 10

    def __init__(
        self,
        map_key: str,
        dataset: str = "VIIRS_SNPP_NRT",
        day_range: int = 5,
        bbox: tuple[float, float, float, float] = ALGERIA_BBOX,
        timeout: float = 30.0,
    ):
        self.map_key = map_key
        self.dataset = dataset
        # NOTE: day_range=1 is intentionally NOT the default. FIRMS "day 1" is
        # the single most-recent day bucket, which is often EMPTY because NRT
        # detections arrive with an ingestion delay — so a real, ongoing fire can
        # show up only at day_range>=2. We default to 5 (the API's accepted max)
        # both to reliably catch active fires AND to give the gas-flare filter
        # enough temporal depth (several distinct days) to spot static sources
        # from the very first poll.
        self.day_range = max(1, min(int(day_range), self.MAX_DAY_RANGE))
        self.bbox = bbox
        self.timeout = timeout

    def _build_url(self) -> str:
        west, south, east, north = self.bbox
        area = f"{west},{south},{east},{north}"
        return (
            f"{FIRMS_BASE_URL}/{self.map_key}/{self.dataset}/{area}/{self.day_range}"
        )

    def _masked_url(self) -> str:
        """The request URL with the MAP_KEY hidden, safe for logging."""
        return self._build_url().replace(self.map_key, "<MAP_KEY>")

    async def fetch_hotspots(self) -> Optional[list[Hotspot]]:
        """
        Fetch and parse current hotspots for the configured area.

        Returns:
          * ``list[Hotspot]`` (possibly empty) on success
          * ``None`` on any network/API/parse failure, so the caller retries
            next cycle instead of crashing.
        """
        url = self._build_url()
        # Log the exact request (key masked) so it can be verified by hand.
        logger.info("FIRMS request: %s", self._masked_url())
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("FIRMS request failed (network): %s", exc)
            return None

        if response.status_code != 200:
            logger.warning(
                "FIRMS returned HTTP %s: %s",
                response.status_code,
                response.text[:200],
            )
            return None

        body = response.text or ""

        # FIRMS reports key/quota problems as a plain-text body rather than an
        # HTTP error status, so detect that before trying to parse CSV.
        lowered = body.lstrip().lower()
        if lowered.startswith("invalid") or "error" in lowered[:60]:
            logger.warning("FIRMS returned an error message: %s", body[:200])
            return None

        try:
            hotspots = parse_csv(body)
        except Exception as exc:  # defensive: never let a parse bug kill polling
            logger.exception("Failed to parse FIRMS CSV: %s", exc)
            return None

        logger.info(
            "FIRMS %s returned %d hotspot(s) for Algeria.",
            self.dataset,
            len(hotspots),
        )
        return hotspots

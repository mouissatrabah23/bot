"""
Tests for the geocoding wall-clock budget in AlertEngine._resolve_places.

Regression coverage for a real perf bug found via live testing: with a
degraded/slow Nominatim connection, reverse-geocoding added 120+ seconds to a
single polling cycle because alerts waited for ALL new-cell lookups to finish
or time out. Real citizens waiting on a wildfire alert must never be delayed
minutes by an optional, best-effort place-name lookup.

These tests use a FakeGeocoder (no real network) and a monkeypatched clock so
they run instantly while still exercising the actual budget-cutoff logic in
alerts.py — no real sleeping, no real HTTP.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts as alerts_module  # noqa: E402
from alerts import AlertEngine  # noqa: E402
from db import Database  # noqa: E402
from firms_client import Hotspot  # noqa: E402
from geocoding import GeoPlace, cell_key  # noqa: E402


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True


class FakeGeocoder:
    """Same interface as geocoding.Geocoder, but instant and call-counted."""

    def __init__(self, db):
        self.db = db
        self.network_calls = 0

    def is_cached(self, lat, lon):
        return self.db.geocode_cache_get(cell_key(lat, lon)) is not None

    async def resolve(self, lat, lon, allow_network=True):
        cell = cell_key(lat, lon)
        cached = self.db.geocode_cache_get(cell)
        if cached is not None:
            name = cached["name"]
            if name:
                return GeoPlace(name, cached["place_lat"], cached["place_lon"]), False
            return None, False
        if not allow_network:
            return None, False
        self.network_calls += 1
        self.db.geocode_cache_set(cell, f"Place-{cell}", lat, lon)
        return GeoPlace(f"Place-{cell}", lat, lon), True


def _hs(lat, lon, date="2026-07-22", time="1200"):
    return Hotspot(
        latitude=lat, longitude=lon, confidence="n", acq_date=date, acq_time=time,
        satellite="N", instrument="VIIRS", frp=5.0, daynight="D",
    )


@pytest.fixture()
def db():
    d = Database(tempfile.mktemp(suffix=".db"))
    yield d
    d.close()


class _FakeClock:
    """A monotonically-increasing fake clock, advanced only when called."""

    def __init__(self, step=0.5):
        self.value = 0.0
        self.step = step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


async def test_time_budget_cuts_off_new_cells_but_keeps_cache_hits(db, monkeypatch):
    fake_geo = FakeGeocoder(db)
    engine = AlertEngine(bot=FakeBot(), db=db, geocoder=fake_geo,
                         geocode_max_seconds_per_cycle=1.0)

    # Cell C is pre-cached (simulating a place resolved in an earlier cycle).
    cached_lat, cached_lon = 40.0, 40.0
    db.geocode_cache_set(cell_key(cached_lat, cached_lon), "Cached Place", cached_lat, cached_lon)

    clock = _FakeClock(step=0.6)  # each monotonic() call advances 0.6s
    monkeypatch.setattr(alerts_module.time, "monotonic", clock)

    hotspots = [
        _hs(10.0, 10.0),   # cell A: new, resolved BEFORE budget trips
        _hs(20.0, 20.0),   # cell B: new, budget has now tripped -> skipped
        _hs(cached_lat, cached_lon),  # cell C: cached -> resolves regardless
    ]
    place_map = await engine._resolve_places(hotspots)

    assert fake_geo.network_calls == 1  # only cell A hit the network
    assert cell_key(10.0, 10.0) in place_map
    assert cell_key(20.0, 20.0) not in place_map  # skipped by the time budget
    assert cell_key(cached_lat, cached_lon) in place_map
    assert place_map[cell_key(cached_lat, cached_lon)].name == "Cached Place"


async def test_time_budget_default_is_20_seconds():
    engine = AlertEngine(bot=FakeBot(), db=None)
    assert engine.geocode_max_seconds_per_cycle == 20.0


async def test_process_hotspots_still_alerts_when_geocoding_budget_exhausted(db, monkeypatch):
    """
    End-to-end: even if geocoding hits its time budget for every cell, the
    actual personal alert must still be sent (using the wilaya fallback name).
    """
    fake_geo = FakeGeocoder(db)
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         geocoder=fake_geo, geocode_max_seconds_per_cycle=0.0)
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="ar")

    clock = _FakeClock(step=1.0)
    monkeypatch.setattr(alerts_module.time, "monotonic", clock)

    await engine.process_hotspots([_hs(36.90, 7.76)])

    assert len(engine.bot.sent) == 1
    assert fake_geo.network_calls == 0  # budget was 0 -> no network attempted
    # The alert still went out with SOME place name (wilaya fallback).
    assert "عنابة" in engine.bot.sent[0][1] or "🔥" in engine.bot.sent[0][1]

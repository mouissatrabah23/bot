"""Tests for the reverse-geocoding client and its SQLite cache."""

import os
import sys
import tempfile

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Database  # noqa: E402
from geocoding import GeoPlace, Geocoder, cell_key  # noqa: E402


# A representative Nominatim reverse response for a point near Béjaïa.
NOMINATIM_JSON = {
    "lat": "36.7509",
    "lon": "5.0567",
    "address": {
        "city": "بجاية",
        "state": "بجاية",
        "country": "الجزائر",
    },
}


@pytest.fixture()
def db():
    d = Database(tempfile.mktemp(suffix=".db"))
    yield d
    d.close()


def _geocoder_with_counter(db, response_json, status=200):
    """Build a Geocoder whose HTTP layer counts calls via a MockTransport."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json=response_json)

    transport = httpx.MockTransport(handler)
    geo = Geocoder(db, min_interval=0.0)  # no real sleeping in tests

    # Patch AsyncClient to use our transport.
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    geo._patched_init = patched_init  # keep a ref
    return geo, calls, patched_init


def test_cell_key_rounds_to_two_decimals():
    assert cell_key(36.7509, 5.0567) == "36.75:5.06"
    assert cell_key(36.7551, 5.0649) == "36.76:5.06"


def test_default_timeout_is_short(db):
    # Regression guard: this was 15s and, combined with a flaky Nominatim
    # connection, measured 120+ seconds of added delay to real fire alerts in
    # testing. Must stay short — a slow request is a dead end, not worth
    # waiting out, since every second here delays an active wildfire alert.
    geo = Geocoder(db)
    assert geo.timeout <= 8.0


async def test_resolve_hits_network_then_caches(db, monkeypatch):
    geo, calls, patched_init = _geocoder_with_counter(db, NOMINATIM_JSON)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    place, used_net = await geo.resolve(36.7509, 5.0567)
    assert isinstance(place, GeoPlace)
    assert place.name == "بجاية"
    assert used_net is True
    assert calls["n"] == 1

    # Second call for the SAME cell must be served from cache (no HTTP call).
    place2, used_net2 = await geo.resolve(36.7509, 5.0567)
    assert place2.name == "بجاية"
    assert used_net2 is False
    assert calls["n"] == 1  # still one — cache hit avoided the second request


async def test_cache_persists_in_db(db, monkeypatch):
    geo, calls, patched_init = _geocoder_with_counter(db, NOMINATIM_JSON)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    await geo.resolve(36.7509, 5.0567)

    row = db.geocode_cache_get("36.75:5.06")
    assert row is not None
    assert row["name"] == "بجاية"


async def test_resolved_miss_is_cached_and_not_requeried(db, monkeypatch):
    # A valid response with no city/town/village/municipality -> resolved miss.
    geo, calls, patched_init = _geocoder_with_counter(db, {"address": {"state": "ورقلة"}})
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    place, used_net = await geo.resolve(31.95, 5.32)
    assert place is None and used_net is True
    assert calls["n"] == 1

    place2, used_net2 = await geo.resolve(31.95, 5.32)
    assert place2 is None and used_net2 is False
    assert calls["n"] == 1  # cached miss, no second HTTP call


async def test_prefers_city_then_town_then_village(db, monkeypatch):
    geo, _, patched_init = _geocoder_with_counter(
        db, {"lat": "36.5", "lon": "4.2", "address": {"town": "أقبو", "village": "x"}}
    )
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    place, _ = await geo.resolve(36.5, 4.2)
    assert place.name == "أقبو"


async def test_allow_network_false_returns_none_without_calling(db, monkeypatch):
    geo, calls, patched_init = _geocoder_with_counter(db, NOMINATIM_JSON)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    place, used_net = await geo.resolve(36.75, 5.05, allow_network=False)
    assert place is None and used_net is False
    assert calls["n"] == 0


async def test_http_error_not_cached_and_falls_back(db, monkeypatch):
    geo, calls, patched_init = _geocoder_with_counter(db, {}, status=503)
    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

    place, used_net = await geo.resolve(35.0, 2.0)
    assert place is None and used_net is True
    # A failure must NOT be cached (so a later cycle can retry).
    assert db.geocode_cache_get("35.0:2.0") is None

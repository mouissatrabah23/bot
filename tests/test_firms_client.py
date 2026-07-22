"""Unit tests for firms_client: CSV parsing, confidence filtering, fetch flow."""

import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import firms_client  # noqa: E402
from firms_client import FirmsClient, Hotspot, parse_csv, passes_confidence  # noqa: E402

# A minimal, representative VIIRS area-CSV response.
VIIRS_CSV = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight\n"
    "36.5000,3.2000,330.1,0.4,0.4,2026-07-22,1430,N,VIIRS,n,2.0NRT,295.0,12.3,D\n"
    "35.1000,1.5000,360.5,0.4,0.4,2026-07-22,0210,N,VIIRS,h,2.0NRT,300.0,45.0,N\n"
    "34.0000,6.0000,301.0,0.4,0.4,2026-07-22,1432,N,VIIRS,l,2.0NRT,290.0,3.1,D\n"
)

MODIS_CSV = (
    "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_t31,frp,daynight\n"
    "36.0000,5.0000,320.0,1.0,1.0,2026-07-22,1200,Terra,MODIS,85,6.1NRT,295.0,20.0,D\n"
)


def test_parse_csv_reads_all_rows():
    hotspots = parse_csv(VIIRS_CSV)
    assert len(hotspots) == 3
    first = hotspots[0]
    assert isinstance(first, Hotspot)
    assert first.latitude == pytest.approx(36.5)
    assert first.longitude == pytest.approx(3.2)
    assert first.confidence == "n"
    assert first.frp == pytest.approx(12.3)
    assert first.daynight == "D"


def test_parse_csv_empty_input():
    assert parse_csv("") == []
    # Header only, no data rows.
    assert parse_csv("latitude,longitude,acq_date,acq_time\n") == []


def test_parse_csv_skips_malformed_rows():
    bad = (
        "latitude,longitude,acq_date,acq_time,confidence\n"
        ",,2026-07-22,1200,n\n"          # missing coords -> skipped
        "36.0,4.0,2026-07-22,1300,n\n"   # valid
    )
    hotspots = parse_csv(bad)
    assert len(hotspots) == 1
    assert hotspots[0].latitude == pytest.approx(36.0)


def test_parse_csv_modis_shape():
    hotspots = parse_csv(MODIS_CSV)
    assert len(hotspots) == 1
    assert hotspots[0].confidence == "85"
    assert hotspots[0].instrument == "MODIS"


def test_hotspot_detected_at_is_utc():
    hs = parse_csv(VIIRS_CSV)[0]
    dt = hs.detected_at
    assert dt.year == 2026 and dt.month == 7 and dt.day == 22
    assert dt.hour == 14 and dt.minute == 30
    assert dt.tzinfo is not None
    assert hs.detected_display == "2026-07-22 14:30"


def test_dedupe_key_is_stable_and_distinct():
    hotspots = parse_csv(VIIRS_CSV)
    keys = {hs.dedupe_key() for hs in hotspots}
    assert len(keys) == 3  # three distinct detections
    # Same hotspot yields the same key.
    assert hotspots[0].dedupe_key() == parse_csv(VIIRS_CSV)[0].dedupe_key()


@pytest.mark.parametrize(
    "conf,threshold,expected",
    [
        ("h", "n", True),
        ("n", "n", True),
        ("l", "n", False),
        ("l", None, True),   # no threshold accepts everything
        ("h", "", True),
        ("85", "70", True),  # MODIS numeric
        ("60", "70", False),
    ],
)
def test_passes_confidence(conf, threshold, expected):
    hs = Hotspot(
        latitude=36.0, longitude=3.0, confidence=conf, acq_date="2026-07-22",
        acq_time="1200", satellite="N", instrument="VIIRS", frp=1.0, daynight="D",
    )
    assert passes_confidence(hs, threshold) is expected


def test_build_url_contains_key_dataset_and_bbox():
    client = FirmsClient(map_key="MYKEY", dataset="VIIRS_SNPP_NRT", day_range=1)
    url = client._build_url()
    assert "MYKEY" in url
    assert "VIIRS_SNPP_NRT" in url
    assert url.endswith("/1")
    # Algeria bbox west corner should appear.
    assert "-8.7" in url


@pytest.mark.asyncio
async def test_fetch_hotspots_success(monkeypatch):
    """A 200 with CSV body yields parsed hotspots."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=VIIRS_CSV)

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    client = FirmsClient(map_key="MYKEY")
    hotspots = await client.fetch_hotspots()
    assert hotspots is not None
    assert len(hotspots) == 3


@pytest.mark.asyncio
async def test_fetch_hotspots_http_error_returns_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limit exceeded")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    client = FirmsClient(map_key="MYKEY")
    assert await client.fetch_hotspots() is None


@pytest.mark.asyncio
async def test_fetch_hotspots_invalid_key_body_returns_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Invalid MAP_KEY.")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    client = FirmsClient(map_key="BADKEY")
    assert await client.fetch_hotspots() is None


@pytest.mark.asyncio
async def test_fetch_hotspots_network_error_returns_none(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("boom")

    transport = httpx.MockTransport(handler)
    _patch_async_client(monkeypatch, transport)

    client = FirmsClient(map_key="MYKEY")
    assert await client.fetch_hotspots() is None


def _patch_async_client(monkeypatch, transport):
    """Force httpx.AsyncClient to use our MockTransport."""
    original_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)

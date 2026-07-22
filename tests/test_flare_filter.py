"""
Tests for gas-flare / static-source suppression.

A persistent thermal source (gas flare, industrial heat) recurs at the same
~1km cell day after day. The engine records each (cell, date) it sees and
suppresses cells that appear on >= flare_min_days distinct days, so oil/gas
regions like Ouargla don't drown subscribers in false wildfire alerts — while
transient wildfire detections pass through.
"""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import AlertEngine  # noqa: E402
from db import Database  # noqa: E402
from firms_client import Hotspot  # noqa: E402


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True


def _hs(lat, lon, date, time="1200", conf="n"):
    return Hotspot(
        latitude=lat, longitude=lon, confidence=conf, acq_date=date, acq_time=time,
        satellite="N", instrument="VIIRS", frp=1.0, daynight="D",
    )


@pytest.fixture()
def db():
    d = Database(tempfile.mktemp(suffix=".db"))
    yield d
    d.close()


def test_persistent_cell_detected(db):
    # Same cell on 3 distinct days -> flagged; a 2-day cell is not.
    db.record_cell_observations([
        ("31.95:5.32", "2026-07-18"),
        ("31.95:5.32", "2026-07-19"),
        ("31.95:5.32", "2026-07-20"),
        ("36.90:7.76", "2026-07-19"),
        ("36.90:7.76", "2026-07-20"),
    ])
    persistent = db.get_persistent_cells(min_days=3, window_days=10)
    assert "31.95:5.32" in persistent
    assert "36.90:7.76" not in persistent


def test_duplicate_same_day_counts_once(db):
    # Multiple detections in the SAME cell on the SAME day are one distinct day.
    db.record_cell_observations([("30.0:6.0", "2026-07-20")] * 5)
    assert db.get_persistent_cells(min_days=2, window_days=10) == set()


def test_window_excludes_old_observations(db):
    db.record_cell_observations([
        ("30.0:6.0", "2000-01-01"),
        ("30.0:6.0", "2000-01-02"),
        ("30.0:6.0", "2000-01-03"),
    ])
    # All three are far outside a 10-day window -> not counted.
    assert db.get_persistent_cells(min_days=3, window_days=10) == set()


async def test_flare_cell_is_suppressed_in_processing(db):
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         flare_min_days=3, flare_window_days=10)
    db.upsert_gps_subscriber(1, 31.95, 5.32, language="en")  # Ouargla

    # A flare pixel that has recurred for the last 3 days at the same spot.
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    flare = "31.95:5.32"
    db.record_cell_observations([
        (flare, (today - timedelta(days=d)).strftime("%Y-%m-%d")) for d in (1, 2, 3)
    ])

    # This cycle re-detects the flare -> must be suppressed, no alert.
    poll = [_hs(31.95, 5.32, today.strftime("%Y-%m-%d"))]
    await engine.process_hotspots(poll)
    assert len(engine.bot.sent) == 0


async def test_transient_wildfire_is_not_suppressed(db):
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         flare_min_days=3, flare_window_days=10)
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="en")  # Annaba

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Brand-new fire near Annaba, never seen before -> alerts through.
    poll = [_hs(36.90, 7.76, today)]
    await engine.process_hotspots(poll)
    assert len(engine.bot.sent) == 1


async def test_filter_can_be_disabled(db):
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         filter_static_sources=False, flare_min_days=3)
    db.upsert_gps_subscriber(1, 31.95, 5.32, language="en")

    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    db.record_cell_observations([
        ("31.95:5.32", (today - timedelta(days=d)).strftime("%Y-%m-%d")) for d in (1, 2, 3)
    ])
    poll = [_hs(31.95, 5.32, today.strftime("%Y-%m-%d"))]
    await engine.process_hotspots(poll)
    # With the filter off, even a persistent cell alerts.
    assert len(engine.bot.sent) == 1

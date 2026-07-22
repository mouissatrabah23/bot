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


def _hs(lat, lon, date, time="1200", conf="n", frp=1.0):
    return Hotspot(
        latitude=lat, longitude=lon, confidence=conf, acq_date=date, acq_time=time,
        satellite="N", instrument="VIIRS", frp=frp, daynight="D",
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


# ---------------------------------------------------------------------------
# Safety override: a real, sustained wildfire must NOT go silent just because
# it keeps burning in roughly the same spot for several days. Reported by a
# user who saw exactly this — one alert at subscription, then nothing for
# hours despite a real, ongoing, reported fire.
# ---------------------------------------------------------------------------

async def test_low_frp_persistent_cell_stays_suppressed(db):
    """Baseline: a low-FRP persistent cell (a real flare) is still suppressed."""
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         flare_min_days=3, flare_window_days=10,
                         flare_override_min_frp=10.0)
    db.upsert_gps_subscriber(1, 31.95, 5.32, language="en")

    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    db.record_cell_observations([
        ("31.95:5.32", (today - timedelta(days=d)).strftime("%Y-%m-%d")) for d in (1, 2, 3)
    ])
    poll = [_hs(31.95, 5.32, today.strftime("%Y-%m-%d"), frp=1.8)]  # flare-like FRP
    await engine.process_hotspots(poll)
    assert len(engine.bot.sent) == 0


async def test_high_frp_overrides_persistent_suppression(db):
    """A hot enough detection in a persistent cell is NOT suppressed."""
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         flare_min_days=3, flare_window_days=10,
                         flare_override_min_frp=10.0)
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="en")

    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    db.record_cell_observations([
        ("36.90:7.76", (today - timedelta(days=d)).strftime("%Y-%m-%d")) for d in (1, 2, 3)
    ])
    poll = [_hs(36.90, 7.76, today.strftime("%Y-%m-%d"), frp=25.0)]  # well above override
    await engine.process_hotspots(poll)
    assert len(engine.bot.sent) == 1


async def test_sustained_growing_wildfire_is_not_silenced_on_day3(db):
    """
    Regression test for the exact reported bug: a real fire that keeps burning
    in roughly the same footprint, with FRP rising day over day, must keep
    alerting on day 3 (when the old day-count-only rule would have gone silent)
    because its FRP crosses the override threshold.
    """
    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence="n",
                         flare_min_days=3, flare_window_days=10,
                         flare_override_min_frp=10.0)
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="ar")

    day1 = [_hs(36.885, 7.631, "2026-07-19", frp=8.0)]
    day2 = [_hs(36.885, 7.631, "2026-07-20", frp=25.0)]
    day3 = [_hs(36.885, 7.631, "2026-07-21", frp=60.0)]  # cell now has 3 distinct days

    await engine.process_hotspots(day1)
    assert len(engine.bot.sent) == 1
    engine.bot.sent.clear()

    await engine.process_hotspots(day2)
    assert len(engine.bot.sent) == 1
    engine.bot.sent.clear()

    await engine.process_hotspots(day3)
    # Cell IS persistent now (3 distinct days) but FRP=60 >> override -> still alerted.
    assert len(engine.bot.sent) == 1
    assert "36.88:7.63" in db.get_persistent_cells(3, 10)  # confirms it WAS flagged persistent


async def test_get_cell_day_count(db):
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    db.record_cell_observations([
        ("30.0:6.0", (today - timedelta(days=d)).strftime("%Y-%m-%d")) for d in (0, 1, 2)
    ])
    assert db.get_cell_day_count("30.0:6.0", window_days=10) == 3
    assert db.get_cell_day_count("30.0:6.0", window_days=0) <= 1  # only "today" qualifies
    assert db.get_cell_day_count("99.0:99.0", window_days=10) == 0


async def test_trace_logging_explains_flare_suppression(db, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="alerts")

    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         flare_min_days=3, flare_window_days=10,
                         flare_override_min_frp=10.0)
    db.upsert_gps_subscriber(1, 31.95, 5.32, language="en")

    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()
    db.record_cell_observations([
        ("31.95:5.32", (today - timedelta(days=d)).strftime("%Y-%m-%d")) for d in (1, 2, 3)
    ])
    poll = [_hs(31.95, 5.32, today.strftime("%Y-%m-%d"), frp=1.8)]
    await engine.process_hotspots(poll)

    trace_lines = [r.getMessage() for r in caplog.records if "[trace]" in r.getMessage()]
    assert len(trace_lines) == 1
    assert "SUPPRESSED" in trace_lines[0]
    assert "gas-flare" in trace_lines[0]


async def test_trace_logging_explains_pass_and_override(db, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="alerts")

    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None,
                         flare_min_days=3, flare_window_days=10,
                         flare_override_min_frp=10.0)
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="en")

    poll = [_hs(36.90, 7.76, "2026-07-22", frp=5.0)]  # brand-new, not persistent
    await engine.process_hotspots(poll)

    trace_lines = [r.getMessage() for r in caplog.records if "[trace]" in r.getMessage()]
    assert len(trace_lines) == 1
    assert "PASSED - will be alerted" in trace_lines[0]


async def test_trace_logging_ignores_hotspots_far_from_any_subscriber(db, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="alerts")

    engine = AlertEngine(bot=FakeBot(), db=db, radius_km=15, min_confidence=None)
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="en")  # Annaba

    # Far from the only subscriber (Tamanrasset, deep south).
    poll = [_hs(22.78, 5.52, "2026-07-22")]
    await engine.process_hotspots(poll)

    trace_lines = [r.getMessage() for r in caplog.records if "[trace]" in r.getMessage()]
    assert trace_lines == []

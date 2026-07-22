"""
Tests for the hotspot de-duplication cache in alerts.py.

These guard the invariant that matters most once FIRMS_DAY_RANGE > 1: consecutive
polls re-fetch the same detections (their day windows overlap), and the cache
must ensure each detection is alerted exactly once — while genuinely new
detections from a still-active fire are still delivered.
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
    """Records outgoing messages instead of calling Telegram."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return True


def _hs(lat, lon, date, time, conf="n"):
    return Hotspot(
        latitude=lat, longitude=lon, confidence=conf, acq_date=date, acq_time=time,
        satellite="N", instrument="VIIRS", frp=1.0, daynight="D",
    )


@pytest.fixture()
def engine_and_bot():
    db = Database(tempfile.mktemp(suffix=".db"))
    db.upsert_gps_subscriber(1, 36.90, 7.76, language="en")  # near Annaba
    bot = FakeBot()
    engine = AlertEngine(bot=bot, db=db, radius_km=15, min_confidence="n")
    yield engine, bot, db
    db.close()


async def test_same_hotspot_not_realerted_across_overlapping_polls(engine_and_bot):
    engine, bot, _ = engine_and_bot
    poll = [
        _hs(36.88, 7.63, "2026-07-20", "1128"),
        _hs(36.87, 7.64, "2026-07-21", "0128"),
        _hs(36.89, 7.62, "2026-07-22", "1300"),
    ]

    await engine.process_hotspots(poll)
    assert len(bot.sent) == 1  # one batched alert on first sighting

    # Second poll (1h later): the 3-day window re-delivers the very same rows.
    bot.sent.clear()
    await engine.process_hotspots(list(poll))
    assert len(bot.sent) == 0  # nothing new -> no message at all


async def test_new_detection_from_active_fire_is_alerted(engine_and_bot):
    engine, bot, _ = engine_and_bot
    first = [_hs(36.88, 7.63, "2026-07-20", "1128")]
    await engine.process_hotspots(first)
    assert len(bot.sent) == 1

    # Next poll: same old row PLUS a brand-new detection (fire still burning).
    bot.sent.clear()
    second = first + [_hs(36.90, 7.66, "2026-07-22", "1442")]
    await engine.process_hotspots(second)
    assert len(bot.sent) == 1
    # The message must reference only the NEW detection's time, not the old one.
    # Times are shown in Algeria local time (UTC+1): 14:42Z -> 15:42, 11:28Z -> 12:28.
    assert "15:42" in bot.sent[0][1]
    assert "12:28" not in bot.sent[0][1]


async def test_cache_marks_hotspots_known(engine_and_bot):
    engine, bot, db = engine_and_bot
    poll = [
        _hs(36.88, 7.63, "2026-07-20", "1128"),
        _hs(36.90, 7.66, "2026-07-22", "1442"),
    ]
    await engine.process_hotspots(poll)
    # Every processed detection is now cached under its stable dedupe key.
    assert all(db.is_hotspot_known(h.dedupe_key()) for h in poll)


async def test_dedupe_key_is_stable_across_polls(engine_and_bot):
    # The same detection always yields the same key -> cache hit next cycle.
    a = _hs(36.885, 7.631, "2026-07-21", "0128")
    b = _hs(36.884, 7.634, "2026-07-21", "0128")  # within ~1km, same time
    assert a.dedupe_key() == b.dedupe_key()
    c = _hs(36.885, 7.631, "2026-07-21", "0130")  # different time -> new fire pass
    assert c.dedupe_key() != a.dedupe_key()


async def test_current_status_alerts_even_when_hotspots_are_cached(engine_and_bot):
    """
    A user who subscribes DURING an active fire must be shown current fires
    immediately, even though those detections are already in the global cache
    (which would otherwise suppress them as 'already alerted').
    """
    engine, bot, db = engine_and_bot
    guelma_fires = [
        _hs(36.46, 7.42, "2026-07-22", "1200"),   # ~Guelma center
        _hs(36.47, 7.43, "2026-07-22", "1201"),
    ]
    # Simulate an earlier poll that already cached these (so a normal poll would
    # now treat them as known and send nothing new).
    await engine.process_hotspots(list(guelma_fires))
    for h in guelma_fires:
        assert db.is_hotspot_known(h.dedupe_key())

    # A user subscribes to Guelma now. The immediate status check must still
    # surface the ongoing fires, bypassing the cache.
    db.upsert_wilaya_subscriber(2, 24, 36.462, 7.426, language="ar")  # Guelma
    guelma_sub = db.get_subscriber(2)
    bot.sent.clear()
    matched = await engine.send_current_status(guelma_sub, guelma_fires)
    assert matched >= 1
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 2  # delivered to the Guelma subscriber


async def test_current_status_empty_when_no_nearby_fires(engine_and_bot):
    engine, bot, db = engine_and_bot
    far_fire = [_hs(22.78, 5.52, "2026-07-22", "1200")]  # Tamanrasset, far south
    guelma_sub = db.get_subscriber(1)  # the Annaba GPS sub from the fixture
    matched = await engine.send_current_status(guelma_sub, far_fire)
    assert matched == 0
    assert len(bot.sent) == 0


async def test_prune_window_exceeds_query_window(engine_and_bot):
    """
    A cached hotspot must survive longer than the FIRMS query window so it can't
    reappear as 'new'. prune keeps 7 days; the max day_range is 5 -> safe margin.
    """
    engine, bot, db = engine_and_bot
    await engine.process_hotspots([_hs(36.88, 7.63, "2026-07-20", "1128")])
    # Pruning entries older than 7 days must NOT drop a just-cached hotspot.
    removed = db.prune_old_hotspots(older_than_days=7)
    assert removed == 0
    assert len(db.get_active_subscribers()) == 1

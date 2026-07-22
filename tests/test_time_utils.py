"""Tests for to_algeria_time(): UTC -> Algeria local time (UTC+1, no DST)."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geo_utils  # noqa: E402


def _utc(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def test_basic_offset_is_plus_one():
    dz = geo_utils.to_algeria_time(_utc(2026, 7, 20, 13, 6))
    assert dz.strftime("%Y-%m-%d %H:%M") == "2026-07-20 14:06"


def test_naive_datetime_treated_as_utc():
    naive = datetime(2026, 7, 20, 13, 6)  # no tzinfo
    dz = geo_utils.to_algeria_time(naive)
    assert dz.strftime("%H:%M") == "14:06"


def test_midnight_rolls_to_next_day():
    dz = geo_utils.to_algeria_time(_utc(2026, 7, 20, 23, 30))
    assert dz.strftime("%Y-%m-%d %H:%M") == "2026-07-21 00:30"


def test_no_daylight_saving_in_winter():
    # Algeria stays UTC+1 year-round, so a January time is still +1 (not +2).
    dz = geo_utils.to_algeria_time(_utc(2026, 1, 15, 10, 0))
    assert dz.strftime("%H:%M") == "11:00"


def test_result_is_timezone_aware():
    dz = geo_utils.to_algeria_time(_utc(2026, 7, 20, 12, 0))
    assert dz.tzinfo is not None
    assert dz.utcoffset().total_seconds() == 3600  # +1 hour


def test_matches_firms_hotspot_timestamp():
    from firms_client import Hotspot

    hs = Hotspot(
        latitude=36.9, longitude=7.76, confidence="n", acq_date="2026-07-20",
        acq_time="1306", satellite="N", instrument="VIIRS", frp=5.0, daynight="D",
    )
    dz = geo_utils.to_algeria_time(hs.detected_at)
    assert dz.strftime("%Y-%m-%d %H:%M") == "2026-07-20 14:06"

"""
Tests for the startup instance-identity diagnostics in bot.py.

Added to diagnose `telegram.error.Conflict: terminated by other getUpdates
request` — which means two processes are polling with the same bot token at
once (typically a duplicate/leftover deployment). These tests verify the
identity block logs the expected fields, and that a Conflict error is routed
to a clear, actionable diagnostic rather than the generic exception path.
"""

import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as bot_module  # noqa: E402
from telegram.error import Conflict, TimedOut  # noqa: E402


def _fake_context(error):
    """on_error only reads context.error, so a lightweight stand-in is enough."""
    return types.SimpleNamespace(error=error)


async def test_log_instance_identity_includes_expected_fields(caplog):
    caplog.set_level(logging.INFO, logger="yaqadha")
    bot_module._log_instance_identity()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "instance_id" in text
    assert bot_module.INSTANCE_ID in text
    assert bot_module.HOSTNAME in text
    assert str(bot_module.PID) in text
    assert "railway_replica_id" in text


def test_instance_id_is_a_short_hex_string():
    assert len(bot_module.INSTANCE_ID) == 8
    int(bot_module.INSTANCE_ID, 16)  # raises ValueError if not valid hex


async def test_on_error_conflict_gets_actionable_diagnostic(caplog):
    caplog.set_level(logging.INFO, logger="yaqadha")
    err = Conflict("terminated by other getUpdates request")
    await bot_module.on_error(None, _fake_context(err))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "another process is polling" in text
    assert bot_module.INSTANCE_ID in text
    assert bot_module.HOSTNAME in text
    assert "Railway" in text  # actionable guidance mentions Railway checks


async def test_on_error_network_issue_stays_generic_warning(caplog):
    caplog.set_level(logging.INFO, logger="yaqadha")
    await bot_module.on_error(None, _fake_context(TimedOut()))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "network issue" in text
    assert "another process is polling" not in text  # must not be misclassified

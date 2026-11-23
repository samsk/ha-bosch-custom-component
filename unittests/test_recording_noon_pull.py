"""Recording day requests use noon to avoid client hour<1 rewind."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("homeassistant")

from custom_components.bosch.coordinator import _recording_day_noon  # noqa: E402


def test_recording_day_noon_keeps_calendar_day() -> None:
    """Noon on the target day always has hour >= 1."""
    midnight = datetime(2026, 11, 23, 0, 30, tzinfo=timezone.utc)
    noon = _recording_day_noon(midnight)
    assert noon.date() == midnight.date()
    assert noon.hour == 12
    assert noon.hour >= 1


def test_recording_day_noon_yesterday() -> None:
    """Yesterday noon stays on yesterday."""
    now = datetime(2026, 11, 23, 0, 15, tzinfo=timezone.utc)
    yesterday = now.replace(day=22)
    noon = _recording_day_noon(yesterday)
    assert noon == datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

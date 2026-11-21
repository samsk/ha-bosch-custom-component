"""Gap-safe cumulative-sum anchor lookup."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")


@pytest.mark.asyncio
async def test_async_sum_before_finds_row_beyond_14_days(monkeypatch) -> None:
    """Prior sum 40 days back is found by walking windows."""
    from custom_components.bosch import recorder_compat as rc

    before = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    prior = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)
    calls: list[tuple[datetime, datetime]] = []

    def fake_period(_hass, start, end, *_a, **_k):
        calls.append((start, end))
        if start <= prior < end:
            return {
                "recording:rcompressorexternal": [
                    {"start": prior.timestamp(), "state": 2.0, "sum": 42.0}
                ]
            }
        return {"recording:rcompressorexternal": []}

    hass = MagicMock()
    instance = MagicMock()

    async def run_job(fn, *a, **k):
        return fn(*a, **k)

    instance.async_add_executor_job = run_job
    monkeypatch.setattr(rc, "get_instance", lambda _h: instance)
    monkeypatch.setattr(rc, "statistics_during_period", fake_period)

    total = await rc.async_sum_before(
        hass, "recording:rcompressorexternal", before
    )
    assert total == 42.0
    assert len(calls) >= 2
    assert all(end <= before or start < before for start, end in calls)


@pytest.mark.asyncio
async def test_async_sum_before_ignores_row_at_before(monkeypatch) -> None:
    """A row at the rewrite timestamp is never used as the anchor."""
    from custom_components.bosch import recorder_compat as rc

    before = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    def fake_period(_hass, start, end, *_a, **_k):
        return {
            "recording:rcompressorexternal": [
                {"start": before.timestamp(), "state": 1.0, "sum": 1.0}
            ]
        }

    hass = MagicMock()
    instance = MagicMock()

    async def run_job(fn, *a, **k):
        return fn(*a, **k)

    instance.async_add_executor_job = run_job
    monkeypatch.setattr(rc, "get_instance", lambda _h: instance)
    monkeypatch.setattr(rc, "statistics_during_period", fake_period)
    # Cap windows so the test finishes quickly with only at-or-after rows.
    monkeypatch.setattr(rc, "_ANCHOR_MAX_WINDOWS", 2)

    total = await rc.async_sum_before(
        hass, "recording:rcompressorexternal", before
    )
    assert total is None

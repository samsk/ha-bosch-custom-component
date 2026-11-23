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


@pytest.mark.asyncio
async def test_async_period_total_subtracts_start(monkeypatch) -> None:
    """Period total is end sum minus start sum."""
    from custom_components.bosch import recorder_compat as rc

    start = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)

    async def fake_sum_before(_hass, _sid, before):
        if before == start:
            return 100.0
        # last finished hour cutoff = 09:00
        if before.hour == 9:
            return 149.0
        return None

    monkeypatch.setattr(rc, "async_sum_before", fake_sum_before)
    total = await rc.async_period_total(
        MagicMock(), "recording:routputproducedexternal", start, end=now
    )
    assert total == 49.0


@pytest.mark.asyncio
async def test_async_period_total_missing_end_is_none(monkeypatch) -> None:
    """No end sum → None."""
    from custom_components.bosch import recorder_compat as rc

    async def fake_sum_before(_hass, _sid, _before):
        return None

    monkeypatch.setattr(rc, "async_sum_before", fake_sum_before)
    total = await rc.async_period_total(
        MagicMock(),
        "recording:routputproducedexternal",
        datetime(2026, 11, 1, tzinfo=timezone.utc),
        end=datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc),
    )
    assert total is None


@pytest.mark.asyncio
async def test_async_period_total_missing_start_counts_zero(monkeypatch) -> None:
    """Missing start sum is treated as 0."""
    from custom_components.bosch import recorder_compat as rc

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)

    async def fake_sum_before(_hass, _sid, before):
        if before == start:
            return None
        if before.hour == 9:
            return 20.0
        return None

    monkeypatch.setattr(rc, "async_sum_before", fake_sum_before)
    total = await rc.async_period_total(
        MagicMock(), "recording:routputproducedexternal", start, end=now
    )
    assert total == 20.0


@pytest.mark.asyncio
async def test_async_window_total_sums_half_open_range(monkeypatch) -> None:
    """Window sum includes start and excludes end; missing hours are 0."""
    from custom_components.bosch import recorder_compat as rc

    start = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)
    # 09:00 yesterday = 46.1, 08:00 today = 62.2 → 108.3; row at end excluded.
    rows = [
        {"start": start.timestamp(), "state": 46.1},
        {"start": datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc).timestamp(), "state": 62.2},
        {"start": end.timestamp(), "state": 99.0},
    ]

    def fake_period(_hass, _start, _end, *_a, **_k):
        return {"recording:routputproducedexternal": rows}

    hass = MagicMock()
    instance = MagicMock()

    async def run_job(fn, *a, **k):
        return fn(*a, **k)

    instance.async_add_executor_job = run_job
    monkeypatch.setattr(rc, "get_instance", lambda _h: instance)
    monkeypatch.setattr(rc, "statistics_during_period", fake_period)

    total = await rc.async_window_total(
        hass, "recording:routputproducedexternal", start, end
    )
    assert total == 108.3


@pytest.mark.asyncio
async def test_async_window_total_empty_is_none(monkeypatch) -> None:
    """No rows in the window → None (fresh install fallback)."""
    from custom_components.bosch import recorder_compat as rc

    def fake_period(_hass, _start, _end, *_a, **_k):
        return {"recording:rcompressorexternal": []}

    hass = MagicMock()
    instance = MagicMock()

    async def run_job(fn, *a, **k):
        return fn(*a, **k)

    instance.async_add_executor_job = run_job
    monkeypatch.setattr(rc, "get_instance", lambda _h: instance)
    monkeypatch.setattr(rc, "statistics_during_period", fake_period)

    total = await rc.async_window_total(
        hass,
        "recording:rcompressorexternal",
        datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc),
    )
    assert total is None

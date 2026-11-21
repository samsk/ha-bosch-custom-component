"""Recording append and last-hour display option guards."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.components.sensor import SensorStateClass

from custom_components.bosch.const import VALUE
from custom_components.bosch.sensor.recording import RecordingSensor


def test_append_statistics_skips_unavailable() -> None:
    """float('unavailable') must not raise; hour is skipped."""
    entity = RecordingSensor.__new__(RecordingSensor)
    entity._short_id = "test"
    entity._unit_of_measurement = "kWh"
    entity._non_numeric_stat_logged = False
    entity.add_external_stats = MagicMock()
    entity._last_reset = None

    stats = [
        {"d": datetime(2026, 11, 10, 10, 0), "value": "unavailable"},
        {"d": datetime(2026, 11, 10, 11, 0), "value": 1.5},
    ]
    total = entity.append_statistics(stats=stats, sum=0.0, now=datetime.now())
    assert total == 1.5
    entity.add_external_stats.assert_called_once()
    pushed = entity.add_external_stats.call_args.kwargs["stats"]
    assert len(pushed) == 1
    row = pushed[0]
    state = row["state"] if isinstance(row, dict) else row.state
    assert state == 1.5


def test_native_value_is_minus_17_without_shifted_state() -> None:
    """New statistic API keeps entity state at -17 unless shifted display is on."""
    entity = RecordingSensor.__new__(RecordingSensor)
    entity._new_stats_api = True
    entity._recording_shifted_state = False
    entity._attr_state_class = SensorStateClass.TOTAL
    assert entity._native_value() == -17
    assert entity.last_reset is None


def test_shifted_state_shows_hour_without_last_reset_for_measurement() -> None:
    """Shifted display shows the hour; last_reset stays None unless total."""
    hour = datetime(2026, 11, 10, 10, 0)
    entity = RecordingSensor.__new__(RecordingSensor)
    entity._new_stats_api = True
    entity._recording_shifted_state = True
    entity._attr_state_class = SensorStateClass.MEASUREMENT
    entity._current_row = MagicMock(return_value={"d": hour, VALUE: 2.5})
    assert entity._native_value() == 2.5
    assert entity.last_reset is None


def test_shifted_state_last_reset_only_for_total() -> None:
    """last_reset follows the row hour only when state_class is total."""
    hour = datetime(2026, 11, 10, 10, 0)
    entity = RecordingSensor.__new__(RecordingSensor)
    entity._new_stats_api = True
    entity._recording_shifted_state = True
    entity._attr_state_class = SensorStateClass.TOTAL
    entity._current_row = MagicMock(return_value={"d": hour, VALUE: 2.5})
    assert entity._native_value() == 2.5
    assert entity.last_reset == hour


@pytest.mark.asyncio
async def test_upsert_uses_last_sum_across_gap() -> None:
    """Omitted zero hour must not reset cumulative sum (19 + 1 → 20)."""
    from datetime import timezone
    from unittest.mock import AsyncMock, PropertyMock, patch

    hour = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    sid = "recording:rcompressorexternal"
    entity = RecordingSensor.__new__(RecordingSensor)
    entity.hass = MagicMock()
    entity._unit_of_measurement = "kWh"
    entity._non_numeric_stat_logged = False
    entity._last_reset = None
    entity.add_external_stats = MagicMock()
    # Empty merge window (prior hour outside 1h lookback).
    entity.get_stats_from_ha_db = AsyncMock(return_value={sid: []})

    with (
        patch.object(
            RecordingSensor, "statistic_id", new_callable=PropertyMock, return_value=sid
        ),
        patch.object(
            RecordingSensor,
            "_is_temperature_recording",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch(
            "custom_components.bosch.recorder_compat.async_sum_before",
            AsyncMock(return_value=19.0),
        ),
    ):
        await entity.async_upsert_statistics(
            stats=[{"d": hour, "value": 1.0}],
            now=hour.replace(hour=1),
        )
    entity.add_external_stats.assert_called_once()
    pushed = entity.add_external_stats.call_args.kwargs["stats"]
    assert len(pushed) == 1
    row = pushed[0]
    state = row["state"] if isinstance(row, dict) else row.state
    total = row["sum"] if isinstance(row, dict) else row.sum
    assert state == 1.0
    assert total == 20.0


@pytest.mark.asyncio
async def test_upsert_anchor_skips_broken_row_at_change() -> None:
    """Broken row at change_ts is ignored; gap-safe prior sum anchors."""
    from datetime import timezone
    from unittest.mock import AsyncMock, PropertyMock, patch

    sid = "recording:rcompressorexternal"
    broken = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc).timestamp()
    rows = [{"start": broken, "state": 1.0, "sum": 1.0}]

    async def window_rows(start_time, end_time):
        lo, hi = start_time.timestamp(), end_time.timestamp()
        return {sid: [r for r in rows if lo <= r["start"] < hi]}

    hour = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    entity = RecordingSensor.__new__(RecordingSensor)
    entity.hass = MagicMock()
    entity._unit_of_measurement = "kWh"
    entity._non_numeric_stat_logged = False
    entity._last_reset = None
    entity.add_external_stats = MagicMock()
    entity.get_stats_from_ha_db = AsyncMock(side_effect=window_rows)

    with (
        patch.object(
            RecordingSensor, "statistic_id", new_callable=PropertyMock, return_value=sid
        ),
        patch.object(
            RecordingSensor,
            "_is_temperature_recording",
            new_callable=PropertyMock,
            return_value=False,
        ),
        patch(
            "custom_components.bosch.recorder_compat.async_sum_before",
            AsyncMock(return_value=19.0),
        ),
    ):
        await entity.async_upsert_statistics(
            stats=[{"d": hour, "value": 1.5}],
            now=hour.replace(hour=1),
        )
    pushed = entity.add_external_stats.call_args.kwargs["stats"]
    assert len(pushed) == 1
    row = pushed[0]
    total = row["sum"] if isinstance(row, dict) else row.sum
    assert total == 20.5

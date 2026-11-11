"""Recording append skips non-numeric values without raising."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")

from custom_components.bosch.sensor.recording import RecordingSensor


def test_append_statistics_skips_unavailable() -> None:
    """float('unavailable') must not raise; hour is skipped."""
    entity = RecordingSensor.__new__(RecordingSensor)
    entity._short_id = "test"
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

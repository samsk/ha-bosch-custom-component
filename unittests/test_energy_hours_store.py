"""Coordinator energy_hours store with read-only library state."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")


class ReadOnlyRecording:
    """Fake recording object whose state cannot be assigned (matches client)."""

    def __init__(self, attr_id: str) -> None:
        self.attr_id = attr_id
        self.path = attr_id
        self.name = attr_id.rsplit("/", 1)[-1]
        self.kind = "recording"
        self._rows: list[dict[str, Any]] = []
        self.updates: list[datetime] = []

    @property
    def state(self) -> list[dict[str, Any]]:
        """Read-only hour list."""
        return self._rows

    async def update(self, time: datetime | None = None) -> None:
        """Replace rows with a single day keyed by ``time``."""
        assert time is not None
        self.updates.append(time)
        day = time.strftime("%Y-%m-%d")
        # Distinct values so today vs yesterday are obvious.
        value = 7.0 if time.date() == datetime(2026, 11, 22).date() else 8.0
        self._rows = [{"d": f"{day}T12:00:00", "value": value}]


@pytest.mark.asyncio
async def test_energy_hours_keeps_yesterday_and_today(monkeypatch) -> None:
    """Store merges both days even when state assignment is impossible."""
    from custom_components.bosch.coordinator import BoschDataUpdateCoordinator

    monkeypatch.setattr(
        "custom_components.bosch.coordinator.TimestampDataUpdateCoordinator.__init__",
        lambda self, *a, **k: None,
    )
    # Pin merge clock so the 48h prune does not depend on CI wall time.
    clock = datetime(2026, 11, 22, 10, 6, 0)
    monkeypatch.setattr(
        "custom_components.bosch.coordinator.dt_util.now", lambda: clock
    )
    coord = BoschDataUpdateCoordinator.__new__(BoschDataUpdateCoordinator)
    coord.energy_hours = {}
    coord.hass = MagicMock()

    obj = ReadOnlyRecording(
        "/recordings/heatSources/total/energyMonitoring/outputProduced"
    )
    yesterday = datetime(2026, 11, 21, 10, 6, 0)
    today = datetime(2026, 11, 22, 10, 6, 0)

    # Reproduce old bug order would leave yesterday-only on obj.state.
    await coord._async_update_object(obj, time=yesterday)
    await coord._async_update_object(obj, time=today)

    key = obj.attr_id
    stored = coord.energy_hours[key]
    days = {row["d"][:10] for row in stored}
    assert days == {"2026-11-21", "2026-11-22"}
    # Object itself holds only the last pull (today).
    assert obj.state[0]["d"].startswith("2026-11-22")


@pytest.mark.asyncio
async def test_empty_pull_keeps_stored_hours(monkeypatch) -> None:
    """Empty current pull does not wipe the store."""
    from custom_components.bosch.coordinator import BoschDataUpdateCoordinator

    monkeypatch.setattr(
        "custom_components.bosch.coordinator.TimestampDataUpdateCoordinator.__init__",
        lambda self, *a, **k: None,
    )
    monkeypatch.setattr(
        "custom_components.bosch.coordinator.dt_util.now",
        lambda: datetime(2026, 11, 22, 10, 0, 0),
    )
    coord = BoschDataUpdateCoordinator.__new__(BoschDataUpdateCoordinator)
    coord.energy_hours = {}

    obj = ReadOnlyRecording(
        "/recordings/heatSources/total/energyMonitoring/compressor"
    )
    await coord._async_update_object(obj, time=datetime(2026, 11, 22, 9, 0, 0))
    assert coord.energy_hours[obj.attr_id]

    async def empty_update(time=None):
        obj._rows = [{"d": "2026-11-22T10:00:00", "value": "unavailable"}]

    obj.update = empty_update  # type: ignore[method-assign]
    await coord._async_update_object(obj, time=datetime(2026, 11, 22, 10, 0, 0))
    assert coord.energy_hours[obj.attr_id][0]["value"] == 7.0


def test_snapshot_uses_energy_hours_for_cop() -> None:
    """Overlay store rows so COP today and last 24h both work."""
    from custom_components.bosch import heatpump_diagnostics as hp

    gw = SimpleNamespace(sensors=[])
    path = "/recordings/heatSources/total/energyMonitoring/outputProduced"
    cpath = "/recordings/heatSources/total/energyMonitoring/compressor"
    hours = {
        path: [
            {"d": "2026-11-21T22:00:00", "value": 8.0},
            {"d": "2026-11-22T00:00:00", "value": 7.0},
        ],
        cpath: [
            {"d": "2026-11-21T22:00:00", "value": 2.0},
            {"d": "2026-11-22T00:00:00", "value": 2.0},
        ],
    }
    snap = hp.build_snapshot_from_gateway(
        gw, energy_hours=hours, now=datetime(2026, 11, 22, 1, 6, 0)
    )
    assert snap.produced_today == 7.0
    assert snap.compressor_today == 2.0
    assert snap.cop("today") == 3.5
    assert snap.produced_last_24h == 15.0
    assert snap.compressor_last_24h == 4.0
    assert snap.cop("last_24h") == 3.75
    assert snap.cop("last_24h") != snap.cop("today")


def test_spf_today_and_last_24h() -> None:
    """SPF today / last 24h use produced + eheater over consumed."""
    from custom_components.bosch.heatpump_diagnostics import HeatpumpSnapshot

    snap = HeatpumpSnapshot(
        produced_today=49.0,
        eheater_today=1.0,
        consumed_today=17.0,
        compressor_today=16.0,
        produced_last_24h=53.0,
        eheater_last_24h=2.0,
        consumed_last_24h=20.0,
        compressor_last_24h=18.0,
    )
    assert snap.spf("today") == 2.94
    assert snap.cop("today") == 3.06
    assert snap.spf("last_24h") == 2.75
    assert snap.spf("today") <= snap.cop("today")

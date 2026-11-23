"""Align LTS metadata unit with the entity."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")


SID = "sensor.heat_pump_diagnostics_cop_today"


def _hass_with_meta(monkeypatch, metas: dict) -> tuple[MagicMock, list]:
    """Stub get_instance / get_metadata; return hass and update call log."""
    from custom_components.bosch import recorder_compat as rc

    hass = MagicMock()
    instance = MagicMock()

    async def run_job(fn, *a, **k):
        return fn(*a, **k)

    instance.async_add_executor_job = run_job
    monkeypatch.setattr(rc, "get_instance", lambda _h: instance)

    def fake_get_metadata(_hass, *, statistic_ids=None, **_k):
        return {sid: metas[sid] for sid in (statistic_ids or set()) if sid in metas}

    monkeypatch.setattr(rc, "get_metadata", fake_get_metadata)
    calls: list[dict] = []

    def fake_update(_hass, statistic_id, **kwargs):
        calls.append({"statistic_id": statistic_id, **kwargs})

    monkeypatch.setattr(rc, "async_update_statistics_metadata", fake_update)
    return hass, calls


@pytest.mark.asyncio
async def test_align_updates_null_unitless(monkeypatch) -> None:
    """null / unitless metadata is updated to empty string / None."""
    from custom_components.bosch import recorder_compat as rc

    hass, calls = _hass_with_meta(
        monkeypatch,
        {
            SID: (
                1,
                {
                    "statistic_id": SID,
                    "unit_of_measurement": None,
                    "unit_class": "unitless",
                    "source": "recorder",
                    "name": None,
                    "has_mean": True,
                    "has_sum": False,
                },
            )
        },
    )
    updated = await rc.async_align_statistic_unit(hass, SID, "")
    assert updated is True
    assert calls == [
        {
            "statistic_id": SID,
            "new_unit_of_measurement": "",
            "new_unit_class": None,
        }
    ]


@pytest.mark.asyncio
async def test_align_skips_matching_empty_unit(monkeypatch) -> None:
    """Already matching empty-string unit is a no-op."""
    from custom_components.bosch import recorder_compat as rc

    hass, calls = _hass_with_meta(
        monkeypatch,
        {
            SID: (
                1,
                {
                    "statistic_id": SID,
                    "unit_of_measurement": "",
                    "unit_class": None,
                    "source": "recorder",
                    "name": None,
                    "has_mean": True,
                    "has_sum": False,
                },
            )
        },
    )
    updated = await rc.async_align_statistic_unit(hass, SID, "")
    assert updated is False
    assert calls == []


@pytest.mark.asyncio
async def test_align_skips_missing_metadata(monkeypatch) -> None:
    """No recorder metadata → no update."""
    from custom_components.bosch import recorder_compat as rc

    hass, calls = _hass_with_meta(monkeypatch, {})
    updated = await rc.async_align_statistic_unit(hass, SID, "")
    assert updated is False
    assert calls == []

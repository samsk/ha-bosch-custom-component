"""Unit tests for month/year and last-24h period totals helper."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("homeassistant")

from custom_components.bosch import heatpump_period as hp  # noqa: E402
from custom_components.bosch.heatpump_diagnostics import (  # noqa: E402
    HeatpumpSnapshot,
)


@pytest.mark.asyncio
async def test_refresh_period_totals_uses_statistics(monkeypatch) -> None:
    """Month, year, and last-24h totals come from statistics helpers."""

    async def fake_total(_hass, statistic_id, start, *, end=None):
        key = statistic_id.split(":")[1]
        month = {
            "routputproducedexternal": 100.0,
            "rcompressorexternal": 40.0,
            "rconsumedenergyexternal": 50.0,
            "reheaterexternal": 5.0,
        }
        year = {
            "routputproducedexternal": 1000.0,
            "rcompressorexternal": 400.0,
            "rconsumedenergyexternal": 500.0,
            "reheaterexternal": 50.0,
        }
        return (month if start.month == 11 else year)[key]

    async def fake_window(_hass, statistic_id, start, end):
        key = statistic_id.split(":")[1]
        return {
            "routputproducedexternal": 108.3,
            "rcompressorexternal": 37.1,
            "rconsumedenergyexternal": 37.1,
            "reheaterexternal": 0.0,
        }[key]

    monkeypatch.setattr(hp, "async_period_total", fake_total)
    monkeypatch.setattr(hp, "async_window_total", fake_window)
    coordinator = SimpleNamespace()
    now = datetime(2026, 11, 22, 9, 30, tzinfo=timezone.utc)
    totals = await hp.async_refresh_period_totals(
        MagicMock(), coordinator, now=now
    )
    assert totals["produced_month"] == 100.0
    assert totals["compressor_year"] == 400.0
    assert totals["produced_month_source"] == "statistics"
    assert totals["produced_last_24h"] == 108.3
    assert totals["compressor_last_24h"] == 37.1
    assert hp.period_totals(coordinator)["eheater_month"] == 5.0


def test_apply_period_totals_fills_compressor() -> None:
    """Statistics fill compressor; gateway produced wins."""
    snap = HeatpumpSnapshot(
        produced_month=781.95,
        consumed_month=313.81,
        eheater_month=72.23,
    )
    hp.apply_period_totals(
        snap,
        {
            "produced_month": 700.0,
            "compressor_month": 250.0,
            "consumed_month": 300.0,
            "eheater_month": 50.0,
        },
    )
    assert snap.produced_month == 781.95
    assert snap.consumed_month == 313.81
    assert snap.eheater_month == 72.23
    assert snap.compressor_month == 250.0
    assert snap.cop("month") == 3.13
    assert snap.period_source["month"] == "gateway"


def test_apply_period_totals_statistics_when_empty() -> None:
    """Empty gateway fields are filled from statistics."""
    snap = HeatpumpSnapshot()
    hp.apply_period_totals(
        snap,
        {
            "produced_month": 90.0,
            "compressor_month": 30.0,
            "consumed_month": 40.0,
            "eheater_month": 10.0,
        },
    )
    assert snap.produced_month == 90.0
    assert snap.compressor_month == 30.0
    assert snap.cop("month") == 3.0
    assert snap.spf("month") == 2.5
    assert snap.period_source["month"] == "statistics"


def test_apply_last_24h_from_statistics_differs_from_today() -> None:
    """Stats window overwrites list last-24h; today stays list-derived."""
    snap = HeatpumpSnapshot(
        produced_today=62.2,
        compressor_today=20.1,
        consumed_today=20.1,
        eheater_today=0.0,
        produced_last_24h=62.2,
        compressor_last_24h=20.1,
        consumed_last_24h=20.1,
        eheater_last_24h=0.0,
        period_source={"today": "list", "last_24h": "list"},
    )
    hp.apply_period_totals(
        snap,
        {
            "produced_last_24h": 108.3,
            "compressor_last_24h": 37.1,
            "consumed_last_24h": 37.1,
            "eheater_last_24h": 0.0,
        },
    )
    assert snap.produced_today == 62.2
    assert snap.compressor_today == 20.1
    assert snap.cop("today") == 3.09
    assert snap.produced_last_24h == 108.3
    assert snap.compressor_last_24h == 37.1
    assert snap.cop("last_24h") == 2.92
    assert snap.spf("last_24h") == 2.92
    assert snap.period_source["last_24h"] == "statistics"


def test_apply_last_24h_keeps_list_when_stats_missing() -> None:
    """Empty gateway list 0 is kept only when statistics have no rows."""
    snap = HeatpumpSnapshot(
        produced_last_24h=0.0,
        compressor_last_24h=0.0,
        period_source={"last_24h": "list"},
    )
    hp.apply_period_totals(
        snap,
        {
            "produced_last_24h": None,
            "compressor_last_24h": None,
            "consumed_last_24h": None,
            "eheater_last_24h": None,
        },
    )
    assert snap.produced_last_24h == 0.0
    assert snap.compressor_last_24h == 0.0
    assert snap.period_source["last_24h"] == "list"


def test_apply_last_24h_stats_override_empty_list() -> None:
    """Empty list 0 must not win when statistics have a window total."""
    snap = HeatpumpSnapshot(
        produced_last_24h=0.0,
        compressor_last_24h=0.0,
        consumed_last_24h=0.0,
        eheater_last_24h=0.0,
    )
    hp.apply_period_totals(
        snap,
        {
            "produced_last_24h": 108.3,
            "compressor_last_24h": 37.1,
            "consumed_last_24h": 37.1,
            "eheater_last_24h": 0.0,
        },
    )
    assert snap.cop("last_24h") == 2.92
    assert snap.period_source["last_24h"] == "statistics"

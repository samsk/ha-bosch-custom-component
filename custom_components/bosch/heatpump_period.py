"""Month/year and last-24h energy totals from HA stored statistics."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .recorder_compat import async_period_total, async_window_total

_LOGGER = logging.getLogger(__name__)

HP_PERIOD_ATTR = "_hp_period_totals"

PRODUCED_STAT_ID = "recording:routputproducedexternal"
COMPRESSOR_STAT_ID = "recording:rcompressorexternal"
CONSUMED_STAT_ID = "recording:rconsumedenergyexternal"
EHEATER_STAT_ID = "recording:reheaterexternal"

_STAT_KEYS = (
    ("produced", PRODUCED_STAT_ID),
    ("compressor", COMPRESSOR_STAT_ID),
    ("consumed", CONSUMED_STAT_ID),
    ("eheater", EHEATER_STAT_ID),
)


def period_totals(coordinator: Any) -> dict[str, Any]:
    """Cached month/year/last-24h totals on the coordinator."""
    cached = getattr(coordinator, HP_PERIOD_ATTR, None)
    return cached if isinstance(cached, dict) else {}


async def async_refresh_period_totals(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute month/year and last-24h kWh from recording statistics."""
    clock = now or dt_util.now()
    month_start = clock.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = clock.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    cutoff = clock.replace(minute=0, second=0, microsecond=0)
    window_start = cutoff - timedelta(hours=24)
    totals: dict[str, Any] = {}
    for period, start in (("month", month_start), ("year", year_start)):
        for key, statistic_id in _STAT_KEYS:
            value = await async_period_total(
                hass, statistic_id, start, end=clock
            )
            totals[f"{key}_{period}"] = value
            totals[f"{key}_{period}_source"] = (
                "statistics" if value is not None else None
            )
    for key, statistic_id in _STAT_KEYS:
        value = await async_window_total(
            hass, statistic_id, window_start, cutoff
        )
        totals[f"{key}_last_24h"] = value
        totals[f"{key}_last_24h_source"] = (
            "statistics" if value is not None else None
        )
    setattr(coordinator, HP_PERIOD_ATTR, totals)
    _LOGGER.debug("Heat-pump period totals refreshed: %s", totals)
    return totals


def apply_period_totals(
    snapshot: Any,
    totals: dict[str, Any],
) -> Any:
    """Fill month/year; overwrite last 24h from statistics when present."""
    sources = dict(getattr(snapshot, "period_source", {}) or {})
    sources.setdefault("today", "list")
    sources.setdefault("last_24h", "list")
    if not totals:
        snapshot.period_source = sources
        return snapshot
    # Gateway scalars win for produced/consumed/eheater when already set.
    for period in ("month", "year"):
        had_produced = getattr(snapshot, f"produced_{period}", None) is not None
        for key in ("produced", "consumed", "eheater"):
            field = f"{key}_{period}"
            if getattr(snapshot, field, None) is None and totals.get(field) is not None:
                setattr(snapshot, field, totals[field])
        # Compressor month/year always from statistics.
        cfield = f"compressor_{period}"
        if totals.get(cfield) is not None:
            setattr(snapshot, cfield, totals[cfield])
        if getattr(snapshot, f"produced_{period}", None) is not None:
            sources[period] = "gateway" if had_produced else "statistics"
    # Last 24h: statistics overwrite list totals when any window sum exists.
    applied_24h = False
    for key in ("produced", "compressor", "consumed", "eheater"):
        field = f"{key}_last_24h"
        if totals.get(field) is not None:
            setattr(snapshot, field, totals[field])
            applied_24h = True
    if applied_24h:
        sources["last_24h"] = "statistics"
    snapshot.period_source = sources
    return snapshot
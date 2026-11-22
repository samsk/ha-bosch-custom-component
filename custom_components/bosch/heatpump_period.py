"""Month/year energy totals from HA stored statistics."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .recorder_compat import async_period_total

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
    """Cached month/year totals on the coordinator."""
    cached = getattr(coordinator, HP_PERIOD_ATTR, None)
    return cached if isinstance(cached, dict) else {}


async def async_refresh_period_totals(
    hass: HomeAssistant,
    coordinator: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute month/year kWh from recording statistics."""
    clock = now or dt_util.now()
    month_start = clock.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    year_start = clock.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
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
    setattr(coordinator, HP_PERIOD_ATTR, totals)
    _LOGGER.debug("Heat-pump period totals refreshed: %s", totals)
    return totals


def apply_period_totals(
    snapshot: Any,
    totals: dict[str, Any],
) -> Any:
    """Fill missing month/year fields; compressor always from statistics."""
    sources = dict(getattr(snapshot, "period_source", {}) or {})
    sources.setdefault("today", "list")
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
    snapshot.period_source = sources
    return snapshot

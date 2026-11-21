"""Repair helpers for EasyControl energy and IVT recording statistics."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_metadata,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .energy_fields import missing_history_days, row_for_day
from .recorder_compat import async_clear_statistics_range, async_sum_before
from .recording_merge import plan_recording_sum_repair
from .sensor.statistic_helper import import_lock_for

if TYPE_CHECKING:
    from .sensor.energy import EnergySensor

_LOGGER = logging.getLogger(__name__)

# IVT energy recording series (manual sum repair only; never auto-called).
DEFAULT_RECORDING_SUM_IDS: tuple[str, ...] = (
    "recording:rconsumedenergyexternal",
    "recording:routputproducedexternal",
    "recording:rcompressorexternal",
    "recording:reheaterexternal",
)
ALLOWED_RECORDING_SUM_IDS: frozenset[str] = frozenset(DEFAULT_RECORDING_SUM_IDS)


async def repair_energy_statistics_window(
    entity: EnergySensor, *, days: int
) -> bool:
    """Clear and re-import the last N full days for one energy statistic series.

    Never includes today. Uses range clear when available; refuses a full wipe.
    Returns whether a re-import was performed.
    """
    if not entity._uses_external_statistics:
        return False
    days = max(0, min(int(days), 30))
    if days <= 0:
        return False

    now = dt_util.now()
    start = dt_util.start_of_local_day(now - timedelta(days=days))
    end = dt_util.start_of_local_day(now)  # exclusive end = start of today

    if not entity._resolve_read_key():
        _LOGGER.warning("Repair skipped; energy field unresolved for %s", entity.name)
        return False

    statistic_id = entity.statistic_id
    bosch_data = await entity.fetch_past_data(
        start_time=start, stop_time=end + timedelta(hours=1)
    )
    missing = missing_history_days(
        bosch_data,
        start=start,
        end=end,
        read_key=entity._attr_read_key or "",
    )
    if missing:
        _LOGGER.warning(
            "Repair aborted for %s: gateway missing %s of %s days (%s)",
            statistic_id,
            len(missing),
            days,
            ", ".join(missing[:10]),
        )
        return False

    _LOGGER.info(
        "Repairing energy statistics %s for %s days (%s .. %s)",
        statistic_id,
        days,
        start,
        end,
    )

    cleared = await async_clear_statistics_range(
        entity.hass,
        [statistic_id],
        start_time=start,
        end_time=end,
    )
    if not cleared:
        _LOGGER.warning(
            "Repair aborted for %s: range clear unavailable", statistic_id
        )
        return False

    # Sum anchor strictly before the repair window.
    before = await entity.get_stats_from_ha_db(
        start_time=start - timedelta(hours=48),
        end_time=start,
    )
    anchor = entity.get_last_stats_before_date(
        last_stats=before,
        day=start,
        allow_window_fallback=False,
    )
    _sum = cast(float, anchor.get("sum", 0.0) or 0.0)

    day = start
    while day < end:
        day_key = day.strftime("%d-%m-%Y")
        row = row_for_day(bosch_data, day_key)
        if isinstance(row, dict) and entity._attr_read_key in row:
            try:
                daily = float(row[entity._attr_read_key])
            except (TypeError, ValueError):
                day += timedelta(days=1)
                continue
            hourly = round(daily / 24, 2)
            _sum, stats = entity._generate_easycontrol_statistics(
                start=day,
                end=day + timedelta(days=1),
                single_value=hourly,
                init_value=_sum,
            )
            entity.add_external_stats(stats=stats)
        day += timedelta(days=1)

    _LOGGER.info("Repair finished for %s with sum=%s", statistic_id, _sum)
    return True


async def clear_energy_statistics(
    entity: EnergySensor,
    *,
    reimport: bool = False,
) -> None:
    """Fully clear one statistic series; optionally bootstrap from fetch_all."""
    statistic_id = entity.statistic_id
    await async_clear_statistics_range(entity.hass, [statistic_id])
    if not reimport:
        return
    entity._resolve_read_key()
    all_stats = list((await entity._bosch_object.fetch_all()).values())
    if all_stats:
        entity.append_statistics(stats=all_stats, sum=0.0)


def filter_recording_sum_ids(
    statistic_ids: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Split requested ids into allowed and rejected lists."""
    requested = list(statistic_ids or DEFAULT_RECORDING_SUM_IDS)
    allowed = sorted({sid for sid in requested if sid in ALLOWED_RECORDING_SUM_IDS})
    rejected = sorted({sid for sid in requested if sid not in ALLOWED_RECORDING_SUM_IDS})
    return allowed, rejected


def _write_backup_file(path: str, payload: dict[str, Any]) -> None:
    """Serialize and write the repair backup (executor only)."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=str)


async def _async_hourly_stats(
    hass: HomeAssistant,
    statistic_ids: list[str],
    start: datetime,
    end: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch hourly statistics for the given ids."""
    return await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        end,
        statistic_ids,
        "hour",
        None,
        {"state", "sum"},
    )


async def _async_metadata(
    hass: HomeAssistant, statistic_ids: list[str]
) -> dict[str, tuple[int, StatisticMetaData]]:
    """Fetch existing recorder metadata keyed by statistic id."""
    return await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids=set(statistic_ids))
    )


async def repair_recording_sums(
    hass: HomeAssistant,
    *,
    start_date: date,
    statistic_ids: list[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Recompute cumulative sums from ``start_date``; keep hourly states.

    Manual service only. Never called from setup, migration, or hourly pull.
    Holds each series' import lock so hourly upserts cannot interleave.
    ``dry_run`` (default True) logs a plan and writes nothing. A real run
    writes a JSON backup under the HA config directory first.
    """
    ids, rejected = filter_recording_sum_ids(statistic_ids)
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "series": {},
        "rejected": rejected,
    }
    if rejected:
        _LOGGER.warning("Repair rejected unsupported statistic_ids: %s", rejected)
    if not ids:
        return report
    async with AsyncExitStack() as stack:
        for statistic_id in ids:
            await stack.enter_async_context(import_lock_for(statistic_id))
        locked = await _async_repair_locked(
            hass, ids=ids, start_date=start_date, dry_run=dry_run
        )
    locked["rejected"] = rejected
    return locked


async def _async_repair_locked(
    hass: HomeAssistant,
    *,
    ids: list[str],
    start_date: date,
    dry_run: bool,
) -> dict[str, Any]:
    """Plan and optionally write repaired sums; caller holds the locks."""
    start = dt_util.start_of_local_day(
        datetime.combine(start_date, datetime.min.time())
    )
    end = dt_util.now().replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=1
    )
    raw = await _async_hourly_stats(hass, ids, start, end)
    metas = await _async_metadata(hass, ids)
    start_ts = float(dt_util.as_timestamp(start))
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "start": start.isoformat(),
        "series": {},
        "rejected": [],
    }
    backup: dict[str, Any] = {
        "start": start.isoformat(),
        "created": dt_util.utcnow().isoformat(),
        "series": {},
    }
    to_write: list[tuple[StatisticMetaData, list[StatisticData]]] = []

    for statistic_id in ids:
        if statistic_id not in metas:
            _LOGGER.warning("Repair skipped %s: no recorder metadata", statistic_id)
            report["series"][statistic_id] = {"skipped": "no_metadata"}
            continue
        rows = raw.get(statistic_id, [])
        last_sum = await async_sum_before(hass, statistic_id, start)
        anchor, rebuilt, negatives = plan_recording_sum_repair(
            rows, start_ts=start_ts, last_sum=last_sum
        )
        first_sum = rebuilt[0][2] if rebuilt else None
        last_rebuilt = rebuilt[-1][2] if rebuilt else None
        report["series"][statistic_id] = {
            "rows": len(rebuilt),
            "anchor": anchor,
            "first_sum": first_sum,
            "last_sum": last_rebuilt,
            "negative_changes_before": negatives,
        }
        backup["series"][statistic_id] = rows
        if not rebuilt:
            continue
        stats = [
            StatisticData(
                start=dt_util.utc_from_timestamp(ts),
                state=state,
                sum=total,
            )
            for ts, state, total in rebuilt
        ]
        to_write.append((metas[statistic_id][1], stats))

    _LOGGER.info(
        "Recording sum repair plan dry_run=%s start=%s: %s",
        dry_run,
        start.date(),
        report["series"],
    )
    if dry_run:
        return report

    stamp = dt_util.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = hass.config.path(f"bosch_stats_backup_{stamp}.json")
    await hass.async_add_executor_job(_write_backup_file, backup_path, backup)
    _LOGGER.info("Wrote recording stats backup to %s", backup_path)
    report["backup"] = backup_path

    for meta, stats in to_write:
        async_add_external_statistics(hass, meta, stats)
    return report

"""Pure helpers to recompute hourly external-statistic tails."""

from __future__ import annotations

from typing import Any


def environment_kwh(produced: float | None, compressor: float | None) -> float | None:
    """Environment heat for one hour, or None when produced is missing."""
    if produced is None:
        return None
    return max(0.0, float(produced) - float(compressor or 0.0))


def degree_hours(outdoor: float | None, *, base_c: float = 15.0) -> float | None:
    """Heating degree-hours for one hour, or None when outdoor is missing."""
    if outdoor is None:
        return None
    return max(0.0, float(base_c) - float(outdoor))


def recompute_hourly_tail(
    hour_states: dict[float, float],
    *,
    from_ts: float,
    anchor_sum: float,
) -> list[tuple[float, float, float]]:
    """Recompute (start_ts, state, sum) from ``from_ts`` onward.

    ``anchor_sum`` is the cumulative sum of the hour before ``from_ts``.
    """
    running = float(anchor_sum)
    out: list[tuple[float, float, float]] = []
    for ts in sorted(t for t in hour_states if t >= from_ts):
        state = float(hour_states[ts])
        running += state
        out.append((ts, state, running))
    return out


def first_changed_hour(
    existing_states: dict[float, float],
    incoming_states: dict[float, float],
) -> float | None:
    """Earliest timestamp where incoming differs from existing or is new."""
    change: float | None = None
    for ts, state in incoming_states.items():
        old = existing_states.get(ts)
        if old is None or abs(float(old) - float(state)) > 1e-9:
            change = ts if change is None else min(change, ts)
    return change


def anchor_sum_before(
    existing_sums: dict[float, float],
    before_ts: float,
) -> float:
    """Cumulative sum of the latest existing hour strictly before ``before_ts``."""
    prior = [ts for ts in existing_sums if ts < before_ts]
    if not prior:
        return 0.0
    return float(existing_sums[max(prior)])


def resolve_anchor(
    existing_sums: dict[float, float],
    change_ts: float,
    last_sum: float | None = None,
) -> float:
    """Cumulative sum just before ``change_ts`` across omitted zero hours.

    Use the latest existing row before ``change_ts``. Else use ``last_sum``
    (caller must pass a row older than ``change_ts``), or 0.0.
    """
    prior = [ts for ts in existing_sums if ts < change_ts]
    if prior:
        return float(existing_sums[max(prior)])
    if last_sum is not None:
        return float(last_sum)
    return 0.0


def merge_hour_states(
    existing_states: dict[float, float],
    incoming_states: dict[float, float],
) -> dict[float, float]:
    """Existing hours with incoming overrides."""
    merged = dict(existing_states)
    merged.update(incoming_states)
    return merged


def rows_to_maps(
    rows: list[dict[str, Any]] | None,
) -> tuple[dict[float, float], dict[float, float]]:
    """Split statistic rows into state and sum maps keyed by start ts."""
    states: dict[float, float] = {}
    sums: dict[float, float] = {}
    for row in rows or []:
        start = row.get("start")
        if start is None:
            continue
        ts = float(start)
        try:
            states[ts] = float(row.get("state") or 0.0)
        except (TypeError, ValueError):
            states[ts] = 0.0
        try:
            sums[ts] = float(row.get("sum") or 0.0)
        except (TypeError, ValueError):
            sums[ts] = 0.0
    return states, sums


def plan_recording_sum_repair(
    rows: list[dict[str, Any]],
    *,
    start_ts: float,
    last_sum: float | None = None,
) -> tuple[float, list[tuple[float, float, float]], int]:
    """Plan a sum repair: (anchor, rebuilt rows, negative-change count).

    Keeps every ``state`` unchanged. ``start_ts`` is the first hour to rewrite.
    ``last_sum`` is used when ``rows`` has no hour strictly before ``start_ts``.
    """
    states, sums = rows_to_maps(rows)
    negatives = 0
    ordered = sorted(states)
    prev_sum: float | None = None
    for ts in ordered:
        total = float(sums.get(ts, 0.0))
        if prev_sum is not None and total < prev_sum - 1e-9:
            negatives += 1
        prev_sum = total
    anchor = resolve_anchor(sums, start_ts, last_sum=last_sum)
    from_ts = min((ts for ts in states if ts >= start_ts), default=None)
    if from_ts is None:
        return anchor, [], negatives
    window = {ts: states[ts] for ts in states if ts >= start_ts}
    rebuilt = recompute_hourly_tail(window, from_ts=from_ts, anchor_sum=anchor)
    return anchor, rebuilt, negatives

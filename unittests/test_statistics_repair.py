"""Unit tests for manual recording sum repair (plan, guards, writes, locks)."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parent.parent
BOSCH_DIR = REPO / "custom_components" / "bosch"
MODULE_PATH = BOSCH_DIR / "recording_merge.py"
SID = "recording:rcompressorexternal"


def _load_merge():
    """Load recording_merge without package __init__."""
    name = "bosch_recording_merge_repair_tests"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rm = _load_merge()


def _broken_rows(base: float = 200.0) -> list[dict[str, float]]:
    """Prior sum 19 one hour before base; reset to 1 at base."""
    return [
        {"start": base - 3600.0, "state": 2.0, "sum": 19.0},
        {"start": base, "state": 1.0, "sum": 1.0},
        {"start": base + 3600.0, "state": 3.0, "sum": 4.0},
    ]


def test_plan_recording_sum_repair_keeps_states() -> None:
    """Prior sum 19, gap, then state 1 → rebuilt sum 20; state unchanged."""
    rows = [
        {"start": 100.0, "state": 2.0, "sum": 19.0},
        {"start": 200.0, "state": 1.0, "sum": 1.0},  # broken reset
        {"start": 300.0, "state": 3.0, "sum": 4.0},
    ]
    anchor, rebuilt, negatives = rm.plan_recording_sum_repair(rows, start_ts=200.0)
    assert anchor == 19.0
    assert negatives == 1
    assert rebuilt == [
        (200.0, 1.0, 20.0),
        (300.0, 3.0, 23.0),
    ]


def test_plan_recording_sum_repair_uses_last_sum() -> None:
    """No prior in rows uses last_sum from gap-safe lookup."""
    rows = [
        {"start": 200.0, "state": 1.0, "sum": 1.0},
        {"start": 300.0, "state": 3.0, "sum": 4.0},
    ]
    anchor, rebuilt, negatives = rm.plan_recording_sum_repair(
        rows, start_ts=200.0, last_sum=19.0
    )
    assert anchor == 19.0
    assert negatives == 0
    assert rebuilt == [
        (200.0, 1.0, 20.0),
        (300.0, 3.0, 23.0),
    ]


def test_plan_recording_sum_repair_empty_window() -> None:
    """No rows at/after start_ts yields empty rebuild."""
    rows = [{"start": 50.0, "state": 1.0, "sum": 5.0}]
    anchor, rebuilt, negatives = rm.plan_recording_sum_repair(rows, start_ts=100.0)
    assert anchor == 5.0
    assert rebuilt == []
    assert negatives == 0


def test_filter_recording_sum_ids_rejects_unsupported() -> None:
    """Only the four IVT energy series are allowed."""
    pytest.importorskip("homeassistant")
    from custom_components.bosch.statistics_repair import filter_recording_sum_ids

    allowed, rejected = filter_recording_sum_ids(
        [SID, "recording:routdoortempexternal", SID]
    )
    assert allowed == [SID]
    assert rejected == ["recording:routdoortempexternal"]


def test_repair_service_defaults_to_dry_run() -> None:
    """Service schema defaults dry_run to True (manual-only safety)."""
    pytest.importorskip("homeassistant")

    from custom_components.bosch.services import SERVICE_REPAIR_RECORDING_SUMS_SCHEMA

    data = SERVICE_REPAIR_RECORDING_SUMS_SCHEMA(
        {"device_id": ["abc"], "start_date": "2026-11-13"}
    )
    assert data["dry_run"] is True


def test_repair_service_rejects_unsupported_statistic_id() -> None:
    """Schema refuses statistic ids outside the allowlist."""
    pytest.importorskip("homeassistant")
    import voluptuous as vol

    from custom_components.bosch.services import SERVICE_REPAIR_RECORDING_SUMS_SCHEMA

    with pytest.raises(vol.Invalid):
        SERVICE_REPAIR_RECORDING_SUMS_SCHEMA(
            {
                "device_id": ["abc"],
                "start_date": "2026-11-13",
                "statistic_ids": ["recording:routdoortempexternal"],
            }
        )


def test_repair_recording_sums_not_imported_outside_services() -> None:
    """Only services.py may call repair_recording_sums (never auto paths)."""
    offenders: list[str] = []
    for path in BOSCH_DIR.rglob("*.py"):
        if path.name in ("services.py", "statistics_repair.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "repair_recording_sums":
                        offenders.append(str(path.relative_to(REPO)))
            if isinstance(node, ast.Name) and node.id == "repair_recording_sums":
                offenders.append(str(path.relative_to(REPO)))
            if isinstance(node, ast.Attribute) and node.attr == "repair_recording_sums":
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


@pytest.fixture
def repair_env(monkeypatch, tmp_path):
    """Patch HA I/O and clock; return (module, recorded writes)."""
    pytest.importorskip("homeassistant")

    from custom_components.bosch import statistics_repair as sr_mod

    now = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    base = datetime(2026, 11, 13).timestamp()
    adds: list[tuple[object, list[object]]] = []
    order: list[str] = []

    async def fake_stats(*_a, **_k):
        # Rewrite window only: broken rows at/after start, no prior.
        return {
            SID: [
                {"start": base, "state": 1.0, "sum": 1.0},
                {"start": base + 3600.0, "state": 3.0, "sum": 4.0},
            ]
        }

    async def fake_sum_before(*_a, **_k):
        return 19.0

    async def run_job(fn, *a, **k):
        order.append("backup")
        return fn(*a, **k)

    monkeypatch.setattr(sr_mod, "_async_hourly_stats", fake_stats)
    monkeypatch.setattr(sr_mod, "async_sum_before", fake_sum_before)
    monkeypatch.setattr(
        sr_mod,
        "async_add_external_statistics",
        lambda _hass, meta, stats: (order.append("import"), adds.append((meta, stats))),
    )
    monkeypatch.setattr(sr_mod.dt_util, "now", lambda: now)
    monkeypatch.setattr(sr_mod.dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(
        sr_mod.dt_util,
        "start_of_local_day",
        lambda d=None: (d or now).replace(hour=0, minute=0, second=0, microsecond=0),
    )
    monkeypatch.setattr(sr_mod.dt_util, "as_timestamp", lambda d: d.timestamp())
    hass = MagicMock()
    hass.config.path = lambda name: str(tmp_path / name)
    hass.async_add_executor_job = run_job
    return sr_mod, hass, adds, tmp_path, order


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(repair_env, monkeypatch) -> None:
    """dry_run must not write a backup or call async_add_external_statistics."""
    sr_mod, hass, adds, tmp_path, order = repair_env
    meta = object()

    async def fake_meta(*_a, **_k):
        return {SID: (1, meta)}

    monkeypatch.setattr(sr_mod, "_async_metadata", fake_meta)

    report = await sr_mod.repair_recording_sums(
        hass,
        start_date=date(2026, 11, 13),
        statistic_ids=[SID],
        dry_run=True,
    )
    assert report["dry_run"] is True
    assert report["series"][SID]["last_sum"] == 23.0
    assert report["series"][SID]["anchor"] == 19.0
    assert adds == []
    assert order == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_real_run_reuses_metadata_and_backs_up(repair_env, monkeypatch) -> None:
    """Real run keeps metadata, writes backup via executor, then imports."""
    sr_mod, hass, adds, tmp_path, order = repair_env
    meta = object()

    async def fake_meta(*_a, **_k):
        return {SID: (1, meta)}

    monkeypatch.setattr(sr_mod, "_async_metadata", fake_meta)

    report = await sr_mod.repair_recording_sums(
        hass,
        start_date=date(2026, 11, 13),
        statistic_ids=[SID],
        dry_run=False,
    )
    assert report["backup"].endswith(".json")
    assert len(list(tmp_path.iterdir())) == 1
    assert len(adds) == 1
    used_meta, stats = adds[0]
    assert used_meta is meta
    assert [row["sum"] for row in stats] == [20.0, 23.0]
    assert order == ["backup", "import"]


@pytest.mark.asyncio
async def test_series_without_metadata_is_skipped(repair_env, monkeypatch) -> None:
    """No recorder metadata → series skipped, nothing written for it."""
    sr_mod, hass, adds, _tmp, _order = repair_env

    async def no_meta(*_a, **_k):
        return {}

    monkeypatch.setattr(sr_mod, "_async_metadata", no_meta)

    report = await sr_mod.repair_recording_sums(
        hass,
        start_date=date(2026, 11, 13),
        statistic_ids=[SID],
        dry_run=False,
    )
    assert report["series"][SID] == {"skipped": "no_metadata"}
    assert adds == []


@pytest.mark.asyncio
async def test_repair_waits_for_import_lock(repair_env, monkeypatch) -> None:
    """Repair cannot start while the hourly upsert holds the series lock."""
    sr_mod, hass, _adds, _tmp, _order = repair_env
    from custom_components.bosch.sensor.statistic_helper import import_lock_for

    async def fake_meta(*_a, **_k):
        return {}

    monkeypatch.setattr(sr_mod, "_async_metadata", fake_meta)

    lock = import_lock_for(SID)
    await lock.acquire()
    try:
        task = asyncio.ensure_future(
            sr_mod.repair_recording_sums(
                hass, start_date=date(2026, 11, 13), statistic_ids=[SID]
            )
        )
        await asyncio.sleep(0.05)
        assert not task.done()
    finally:
        lock.release()
    report = await asyncio.wait_for(task, timeout=2)
    assert SID in report["series"]

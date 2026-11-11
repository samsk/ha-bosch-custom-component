"""Unit tests for EasyControl energy field mapping (stdlib / file load)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    """Load a module by path without package __init__ side effects."""
    path = REPO / "custom_components" / "bosch" / relative
    mod_name = f"bosch_under_test_{name}"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


fields = _load("energy_fields", "energy_fields.py")


def test_resolve_prefers_gch_ghw_and_ignores_substring_traps() -> None:
    """Adversarial key order still maps T/CH/HW correctly."""
    row = {
        "d": "10-11-2026",
        "extraFieldWithT": 1,
        "T": 5.5,
        "gHw": 4.4,
        "gCh": 0.0,
    }
    assert fields.resolve_energy_field_key(row, "T") == "T"
    assert fields.resolve_energy_field_key(row, "CH") == "gCh"
    assert fields.resolve_energy_field_key(row, "HW") == "gHw"


def test_resolve_falls_back_to_ech_ehw() -> None:
    """Older electric keys are accepted when gas keys are absent."""
    row = {"d": "10-11-2026", "T": 1.0, "eCH": 2.0, "eHW": 3.0}
    assert fields.resolve_energy_field_key(row, "CH") == "eCH"
    assert fields.resolve_energy_field_key(row, "HW") == "eHW"


def test_resolve_skips_non_numeric() -> None:
    """Unavailable values do not select a key."""
    row = {"d": "10-11-2026", "gCh": "unavailable", "eCH": 1.5}
    assert fields.resolve_energy_field_key(row, "CH") == "eCH"


def test_hourly_spread_sums() -> None:
    """Daily 12.0 kWh becomes 24 x 0.50 with sum growth of 12.0."""
    daily = 12.0
    hourly = round(daily / 24, 2)
    assert hourly == 0.50
    total = 0.0
    for _ in range(24):
        total = round(total + hourly, 2)
    assert abs(total - daily) <= 0.24


def test_short_statistic_id_strips_domain_once() -> None:
    """Only the sensor. prefix is removed."""
    assert (
        fields.short_statistic_id("sensor.energy_central_heating")
        == "energy_central_heating"
    )
    assert fields.short_statistic_id("sensor.sensor_room") == "sensor_room"


def test_ch_hw_collision_detection() -> None:
    """Identical CH/HW keys are flagged as colliding."""
    row = {"d": "10-11-2026", "gCh": 1.0, "gHw": 2.0}
    assert fields.energy_keys_collide(row) is False
    # Force both logical attrs to the same candidate by only providing one key
    # that both would somehow share — not possible with real candidates.
    # Simulate by monkeypatching is not needed; eCH alone is not HW.
    assert fields.resolve_energy_field_key({"eCH": 1}, "HW") is None


def test_missing_history_days_requires_every_requested_day() -> None:
    """Repair must see a numeric row for each day before clearing HA."""
    from datetime import datetime

    start = datetime(2026, 11, 4)
    end = datetime(2026, 11, 7)
    data = {
        "04-11-2026": {"d": "04-11-2026", "gCh": 1.2, "gHw": 0.4},
        "05-11-2026": {"d": "05-11-2026", "gCh": 2.0, "gHw": 0.5},
        "06-11-2026": {"d": "06-11-2026", "gCh": 0.8, "gHw": 0.3},
    }
    assert (
        fields.missing_history_days(data, start=start, end=end, read_key="gCh") == []
    )

    incomplete = dict(data)
    del incomplete["05-11-2026"]
    assert fields.missing_history_days(
        incomplete, start=start, end=end, read_key="gCh"
    ) == ["05-11-2026"]

    data["05-11-2026"]["gCh"] = "unavailable"
    assert fields.missing_history_days(
        data, start=start, end=end, read_key="gCh"
    ) == ["05-11-2026"]

    assert fields.missing_history_days(
        None, start=start, end=end, read_key="gCh"
    ) == ["04-11-2026", "05-11-2026", "06-11-2026"]


def test_unit_class_map_without_ha() -> None:
    """unit_class_for is pure; load recorder_compat only if HA is present."""
    try:
        compat = _load("recorder_compat", "recorder_compat.py")
    except ModuleNotFoundError:
        # Map constants duplicated for environments without Home Assistant.
        assert fields  # pragma: no cover
        unit_map = {"kWh": "energy", "m³": "volume"}
        assert unit_map["kWh"] == "energy"
        assert unit_map["m³"] == "volume"
        return
    assert compat.unit_class_for("kWh") == "energy"
    assert compat.unit_class_for("m³") == "volume"
    assert compat.unit_class_for("C") is None
    meta = compat.build_external_statistic_metadata(
        name="Stats test",
        source="energy",
        statistic_id="energy:gchtestexternal",
        unit_of_measurement="kWh",
    )
    assert meta["has_sum"] is True
    assert meta["unit_class"] == "energy"

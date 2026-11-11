"""Unit tests for IVT heat-pump diagnostics (no gateway / no HA)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "custom_components" / "bosch" / "heatpump_diagnostics.py"


def _load_module():
    """Load heatpump_diagnostics without importing package __init__."""
    name = "bosch_heatpump_diagnostics_under_test"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hp = _load_module()
FIXTURE = Path(__file__).parent / "fixtures" / "km200_snapshot_standby.json"


def _standby() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_standby_snapshot_mode_and_metrics() -> None:
    """KM200 standby scan classifies as circulation with COP and backup share."""
    snap = hp.build_snapshot_from_path_map(_standby())
    assert snap.is_heatpump
    assert hp.classify_heatpump_mode(snap) == "circulation"
    assert snap.compressor_on is False
    assert snap.dhw_charging is False
    assert snap.dhw_requested is True
    assert snap.heat_requestors == ["dhw"]
    assert snap.dhw_gap == 3.9
    assert snap.pool_heating is False
    assert snap.pool_gap == -0.3
    assert len(snap.requesting_circuits) == 0
    assert snap.primary_circuit == "hc2"
    assert any(c.circuit_id == "hc1" and c.room_sensor_fault for c in snap.circuits)
    assert snap.heat_source_delta == -0.1
    assert snap.cop("today") == 3.3
    assert snap.backup_share("today") == 0.17
    assert (snap.eheater_today or 0) > 0
    hint, attrs = hp.classify_efficiency_hint(snap)
    assert hint == "ok"
    assert attrs["cop_min"] == 2.8


def test_pool_mode_requires_compressor() -> None:
    """poolStatus on with compressor selects pool / pool_and_heating."""
    data = _standby()
    data["heatSources/poolStatus"] = "on"
    data["heatSources/actualModulation"] = 40
    snap = hp.build_snapshot_from_path_map(data)
    assert hp.classify_heatpump_mode(snap) == "pool"

    data["heatingCircuits/hc2/supplyTemperatureSetpoint"] = 30
    data["heatSources/applianceSupplyTemperature"] = 30.4
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.space_heating_active
    assert hp.classify_heatpump_mode(snap) == "pool_and_heating"

    data["heatSources/poolStatus"] = "off"
    snap = hp.build_snapshot_from_path_map(data)
    assert hp.classify_heatpump_mode(snap) != "pool"


def test_circuit_requestor_matches_appliance_supply() -> None:
    """The circuit whose flow setpoint matches appliance supply is requesting."""
    data = _standby()
    data["heatSources/actualModulation"] = 40
    data["heatSources/applianceSupplyTemperature"] = 22.4
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.requesting_circuits == ["hc2"]
    assert hp.classify_heatpump_mode(snap) == "space_heating"

    data["heatSources/actualModulation"] = 0
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.requesting_circuits == []
    assert hp.classify_heatpump_mode(snap) == "circulation"


def test_requestors_can_overlap() -> None:
    """DHW setpoint, matching circuits, and pool are independent requestors."""
    data = _standby()
    data["heatSources/actualModulation"] = 40
    data["dhwCircuits/dhw1/currentSetpoint"] = 0
    data["heatSources/poolStatus"] = "off"
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.dhw_requested is False
    assert snap.heat_requestors == []

    data["dhwCircuits/dhw1/currentSetpoint"] = 51
    data["heatSources/applianceSupplyTemperature"] = 22.0
    data["heatingCircuits/hc1/supplyTemperatureSetpoint"] = 22.2
    data["heatSources/poolStatus"] = "on"
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.heat_requestors == ["dhw", "hc1", "hc2", "pool"]
    assert hp.classify_heatpump_mode(snap) == "pool_and_heating"


def test_dhw_mode() -> None:
    """DHW charge with compressor selects dhw."""
    data = _standby()
    data["heatSources/actualModulation"] = 40
    data["dhwCircuits/dhw1/charge"] = "start"
    snap = hp.build_snapshot_from_path_map(data)
    assert hp.classify_heatpump_mode(snap) == "dhw"


def test_efficiency_hint_branches() -> None:
    """Hint cutoffs for backup, COP, spread, and unknown."""
    data = _standby()
    data["recordings/heatSources/total/energyMonitoring/eheater_Today_kWh"] = 6.0
    hint, _ = hp.classify_efficiency_hint(hp.build_snapshot_from_path_map(data))
    assert hint == "check_backup"

    data = _standby()
    data["recordings/heatSources/total/energyMonitoring/outputProduced_Today_kWh"] = 46.0
    hint, _ = hp.classify_efficiency_hint(hp.build_snapshot_from_path_map(data))
    assert hint == "check_cop"

    data = _standby()
    data["heatSources/actualModulation"] = 40
    data["heatSources/actualSupplyTemperature"] = 40.1
    data["heatSources/returnTemperature"] = 40.0
    hint, _ = hp.classify_efficiency_hint(hp.build_snapshot_from_path_map(data))
    assert hint == "check_spread"

    data = _standby()
    data["recordings/heatSources/total/energyMonitoring/consumedEnergy_Today_kWh"] = 0.4
    hint, _ = hp.classify_efficiency_hint(hp.build_snapshot_from_path_map(data))
    assert hint == "unknown"


def test_last_hour_from_recording_rows() -> None:
    """Latest hourly row is used; day totals are not treated as an hour."""
    data = _standby()
    data["recordings/heatSources/total/energyMonitoring/consumedEnergy"] = [
        {"d": "2026-11-10T17:00:00", "value": 1.2},
        {"d": "2026-11-10T18:00:00", "value": "unavailable"},
        {"d": "2026-11-10T16:00:00", "value": 0.4},
    ]
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.last_hour_electricity == 1.2
    assert snap.consumed_today == 23.0


def test_water_flow_does_not_mean_charging() -> None:
    """charge=stop keeps dhw_charging false regardless of flow."""
    data = _standby()
    data["dhwCircuits/dhw1/waterFlow"] = 5
    data["dhwCircuits/dhw1/charge"] = "stop"
    snap = hp.build_snapshot_from_path_map(data)
    assert snap.dhw_charging is False
    assert snap.dhw_requested is True


class _FakeObj:
    """Bosch-like object with path, get_property, and nested _data."""

    def __init__(
        self,
        path: str,
        attr_id: str,
        *,
        value=None,
        nested=None,
        kind: str = "regular",
        name: str = "x",
    ):
        self.path = path
        self.attr_id = attr_id
        self.kind = kind
        self.name = name
        self.sensors = []
        if nested is not None:
            self._data = nested
            self.state = False
        else:
            result = {"value": value} if value is not None else {}
            self._data = {attr_id: {"result": result, "uri": path, "type": kind}}
            self.state = "unavailable" if value is None else value

    def get_property(self, name):
        return self._data.get(name, {}).get("result", {})

    def get_value(self, name, default=None):
        return self.get_property(name).get("value", default)

    @property
    def get_data(self):
        return self._data


class _FakeGw:
    """Gateway with sensors list and get_circuits."""

    def __init__(self, sensors, circuits=None):
        self.sensors = sensors
        self._circuits = circuits or {}

    def get_circuits(self, ctype):
        return self._circuits.get(ctype, [])


def test_unupdated_type_is_not_heatpump() -> None:
    """Empty RESULT / unavailable state must not be treated as hs type."""
    sensor = _FakeObj("/heatSources/hs1/type", "type")
    snap = hp.build_snapshot_from_gateway(_FakeGw([sensor]))
    assert snap.hs_type is None
    assert snap.is_heatpump is False


def test_gateway_flattens_circuit_properties() -> None:
    """Circuit child keys become heatingCircuits/hc2/... snapshot fields."""
    circuit = _FakeObj(
        "/heatingCircuits/hc2",
        "hc2",
        nested={
            "status": {
                "result": {"value": "ACTIVE"},
                "uri": "/heatingCircuits/hc2/status",
                "type": "regular",
            },
            "pumpModulation": {
                "result": {"value": 100},
                "uri": "/heatingCircuits/hc2/pumpModulation",
                "type": "regular",
            },
            "supplyTemperatureSetpoint": {
                "result": {"value": 22},
                "uri": "/heatingCircuits/hc2/supplyTemperatureSetpoint",
                "type": "regular",
            },
        },
    )
    type_sensor = _FakeObj("/heatSources/hs1/type", "type", value="Heatpump")
    mod = _FakeObj("/heatSources/actualModulation", "actualModulation", value=40)
    supply = _FakeObj(
        "/heatSources/applianceSupplyTemperature",
        "applianceSupplyTemperature",
        value=22.4,
    )
    snap = hp.build_snapshot_from_gateway(
        _FakeGw([type_sensor, mod, supply], {"hc": [circuit]})
    )
    assert snap.is_heatpump
    assert snap.requesting_circuits == ["hc2"]
    assert snap.primary_circuit == "hc2"


def test_recording_hours_become_today_total() -> None:
    """Hourly recording lists are summed as consumedEnergy_Today_kWh."""
    rec = _FakeObj(
        "/recordings/heatSources/total/energyMonitoring/consumedEnergy",
        "consumedEnergy",
        value=[
            {"d": "2026-11-11T10:00:00", "value": 1.5},
            {"d": "2026-11-11T11:00:00", "value": 2.5},
        ],
        kind="recording",
    )
    snap = hp.build_snapshot_from_gateway(_FakeGw([rec]))
    assert snap.consumed_today == 4.0
    assert snap.last_hour_electricity == 2.5


def test_source_objects_skip_unrelated_and_child_sensors() -> None:
    """Only matching sensors plus hc/dhw circuits are registered."""
    outdoor = _FakeObj(
        "/system/sensors/temperatures/outdoor_t1", "outdoor_t1", value=5
    )
    noise = _FakeObj("/gateway/uuid", "uuid", value="x")
    circuit = _FakeObj("/heatingCircuits/hc1", "hc1", nested={})
    child = _FakeObj(
        "/heatingCircuits/hc1/pumpModulation", "pumpModulation", value=10
    )
    circuit.sensors = [child]
    rec = _FakeObj(
        "/recordings/heatSources/total/energyMonitoring/consumedEnergy",
        "consumedEnergy",
        kind="recording",
    )
    gw = _FakeGw([outdoor, noise, rec], {"hc": [circuit]})
    sources = hp.heatpump_source_objects(gw)
    paths = {obj.path for obj in sources}
    assert outdoor.path in paths
    assert rec.path in paths
    assert circuit.path in paths
    assert noise.path not in paths
    assert child.path not in paths
    assert hp.is_recording_source(rec)

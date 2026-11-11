"""Pure heat-pump diagnostics from IVT/KM200 gateway paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Room sensor sentinel / impossible reading (e.g. -3276.8).
INVALID_ROOM_TEMP = -50.0
SUPPLY_MARGIN_C = 1.0
PUMP_CIRCULATION_PCT = 5.0
BACKUP_SHARE_WARN = 0.20
MIN_KWH_FOR_HINT = 1.0


@dataclass
class CircuitSnapshot:
    """One heating circuit snapshot."""

    circuit_id: str
    status: str | None = None
    pump_modulation: float | None = None
    supply_setpoint: float | None = None
    supply_actual: float | None = None
    room_temperature: float | None = None

    def matches_appliance_supply(self, appliance_supply: float | None) -> bool:
        """True when this circuit's flow setpoint is what the appliance is supplying.

        Pump speed is not a heat request. Setpoint 0 means the circuit is off.
        """
        if (self.status or "").upper() != "ACTIVE":
            return False
        if (
            appliance_supply is None
            or self.supply_setpoint is None
            or self.supply_setpoint <= 0
        ):
            return False
        return abs(appliance_supply - self.supply_setpoint) <= SUPPLY_MARGIN_C

    @property
    def pump_running(self) -> bool:
        """Circuit pump at or above circulation threshold."""
        return (self.pump_modulation or 0) >= PUMP_CIRCULATION_PCT

    @property
    def room_sensor_fault(self) -> bool:
        """Room temperature looks like a gateway sentinel."""
        return (
            self.room_temperature is not None
            and self.room_temperature <= INVALID_ROOM_TEMP
        )


@dataclass
class HeatpumpSnapshot:
    """Normalized gateway values for classification."""

    hs_type: str | None = None
    actual_modulation: float = 0.0
    hs1_modulation: float = 0.0
    appliance_supply_temp: float | None = None
    supply_temp: float | None = None
    return_temp: float | None = None
    outdoor_temp: float | None = None
    dhw_charge: str | None = None
    dhw_actual: float | None = None
    dhw_setpoint: float | None = None
    pool_status: str | None = None
    pool_temp: float | None = None
    pool_setpoint: float | None = None
    circuits: list[CircuitSnapshot] = field(default_factory=list)
    consumed_today: float | None = None
    consumed_month: float | None = None
    consumed_year: float | None = None
    produced_today: float | None = None
    produced_month: float | None = None
    produced_year: float | None = None
    eheater_today: float | None = None
    eheater_month: float | None = None
    eheater_year: float | None = None
    last_hour_electricity: float | None = None
    last_hour_heat: float | None = None
    last_hour_eheater: float | None = None

    @property
    def is_heatpump(self) -> bool:
        """Whether heat source type is Heatpump."""
        return (self.hs_type or "").lower() == "heatpump"

    @property
    def compressor_on(self) -> bool:
        """Compressor active from modulation."""
        return self.actual_modulation > 0 or self.hs1_modulation > 0

    @property
    def dhw_charging(self) -> bool:
        """DHW charge active (charge path only)."""
        return (self.dhw_charge or "").lower() == "start"

    @property
    def dhw_requested(self) -> bool:
        """Water heating is requested while the DHW setpoint is not zero."""
        return self.dhw_setpoint is not None and self.dhw_setpoint != 0

    @property
    def pool_requested(self) -> bool:
        """Pool heating is requested while pool status is not off."""
        status = (self.pool_status or "").lower()
        return status not in ("", "off")

    @property
    def pool_heating(self) -> bool:
        """Pool requested and compressor running."""
        return self.pool_requested and self.compressor_on

    @property
    def dhw_gap(self) -> float | None:
        """Setpoint minus actual DHW temperature."""
        if self.dhw_setpoint is None or self.dhw_actual is None:
            return None
        return round(self.dhw_setpoint - self.dhw_actual, 2)

    @property
    def pool_gap(self) -> float | None:
        """Setpoint minus pool temperature."""
        if self.pool_setpoint is None or self.pool_temp is None:
            return None
        return round(self.pool_setpoint - self.pool_temp, 2)

    @property
    def heat_source_delta(self) -> float | None:
        """Supply minus return."""
        if self.supply_temp is None or self.return_temp is None:
            return None
        return round(self.supply_temp - self.return_temp, 2)

    @property
    def requesting_circuits(self) -> list[str]:
        """Circuits whose flow setpoint matches the appliance supply.

        Only while the compressor is on. A leftover supply temperature
        during circulation is not a heat request.
        """
        if not self.compressor_on:
            return []
        return [
            c.circuit_id
            for c in self.circuits
            if c.matches_appliance_supply(self.appliance_supply_temp)
        ]

    @property
    def heat_requestors(self) -> list[str]:
        """DHW, heating circuits, and pool. Any combination can be active.

        A circuit is included only while the compressor is on and the
        appliance supply matches that circuit's flow setpoint. More than
        one circuit can match; usually it is one.
        """
        names: list[str] = []
        if self.dhw_requested:
            names.append("dhw")
        names.extend(self.requesting_circuits)
        if self.pool_requested:
            names.append("pool")
        return names

    @property
    def space_heating_active(self) -> bool:
        """Any circuit requests heat."""
        return bool(self.requesting_circuits)

    @property
    def primary_circuit(self) -> str | None:
        """Circuit with highest pump modulation."""
        if not self.circuits:
            return None
        best = max(
            self.circuits,
            key=lambda c: c.pump_modulation if c.pump_modulation is not None else -1.0,
        )
        if best.pump_modulation is None:
            return None
        return best.circuit_id

    @property
    def circulation(self) -> bool:
        """Any circuit pump running while compressor is off."""
        return (not self.compressor_on) and any(c.pump_running for c in self.circuits)

    def _ratio(self, num: float | None, den: float | None) -> float | None:
        if num is None or den is None or den <= 0:
            return None
        return round(num / den, 2)

    def cop(self, period: str) -> float | None:
        """COP for today/month/year."""
        mapping = {
            "today": (self.produced_today, self.consumed_today),
            "month": (self.produced_month, self.consumed_month),
            "year": (self.produced_year, self.consumed_year),
        }
        produced, consumed = mapping[period]
        return self._ratio(produced, consumed)

    def backup_share(self, period: str) -> float | None:
        """E-heater share of consumed electricity (0-1)."""
        mapping = {
            "today": (self.eheater_today, self.consumed_today),
            "month": (self.eheater_month, self.consumed_month),
            "year": (self.eheater_year, self.consumed_year),
        }
        eheater, consumed = mapping[period]
        return self._ratio(eheater, consumed)


def classify_heatpump_mode(snapshot: HeatpumpSnapshot) -> str:
    """Classify operating mode from a snapshot."""
    pool = snapshot.pool_heating
    dhw = snapshot.dhw_charging
    space = snapshot.space_heating_active
    compressor = snapshot.compressor_on

    if compressor and pool and (dhw or space):
        return "pool_and_heating"
    if compressor and pool:
        return "pool"
    if compressor and dhw and space:
        return "dhw_and_heating"
    if compressor and dhw:
        return "dhw"
    if compressor and space:
        return "space_heating"
    if compressor:
        return "compressor"
    if snapshot.circulation:
        return "circulation"
    return "idle"


def _cop_min_for_outdoor(outdoor: float | None) -> float:
    """Minimum acceptable COP for outdoor temperature."""
    if outdoor is None:
        return 2.3
    if outdoor >= 5:
        return 2.8
    if outdoor >= 0:
        return 2.3
    if outdoor >= -7:
        return 1.8
    return 1.5


def classify_efficiency_hint(snapshot: HeatpumpSnapshot) -> tuple[str, dict[str, Any]]:
    """Return efficiency hint and diagnostic attributes."""
    cop_today = snapshot.cop("today")
    backup = snapshot.backup_share("today")
    outdoor = snapshot.outdoor_temp
    cop_min = _cop_min_for_outdoor(outdoor)
    spread = snapshot.heat_source_delta
    attrs = {
        "cop_today": cop_today,
        "backup_share": backup,
        "outdoor": outdoor,
        "cop_min": cop_min,
        "spread": spread,
    }
    consumed = snapshot.consumed_today
    if consumed is None or consumed < MIN_KWH_FOR_HINT or snapshot.produced_today is None:
        return "unknown", attrs
    if backup is not None and backup >= BACKUP_SHARE_WARN:
        return "check_backup", attrs
    if cop_today is not None and cop_today < cop_min:
        return "check_cop", attrs
    if (
        snapshot.compressor_on
        and spread is not None
        and abs(spread) < SUPPLY_MARGIN_C
    ):
        return "check_spread", attrs
    return "ok", attrs


def _as_float(value: Any) -> float | None:
    """Parse float or return None."""
    if value in (None, "", "unavailable"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _path_value(index: dict[str, Any], *suffixes: str) -> Any:
    """Return first matching path value by suffix."""
    for path, value in index.items():
        for suffix in suffixes:
            if path.endswith(suffix) or path == suffix.lstrip("/"):
                return value
    return None


def _latest_hour_value(value: Any) -> float | None:
    """Latest numeric hourly recording row ({d, value} or {d, y})."""
    rows: list[Any] | None = None
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict) and isinstance(value.get("value"), list):
        rows = value["value"]
    if not rows:
        return None
    best_num: float | None = None
    best_stamp: Any = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get("value", row.get("y"))
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        stamp = row.get("d")
        if best_num is None or (
            stamp is not None and (best_stamp is None or stamp > best_stamp)
        ):
            best_num = number
            best_stamp = stamp
    return best_num


def _recording_hour(index: dict[str, Any], marker: str) -> float | None:
    """Latest hour from a recording series, ignoring Today/Month/Year totals."""
    for path, value in index.items():
        if marker not in path:
            continue
        if any(token in path for token in ("Today", "ThisMonth", "ThisYear")):
            continue
        found = _latest_hour_value(value)
        if found is not None:
            return found
    return None


def build_snapshot_from_path_map(path_values: dict[str, Any]) -> HeatpumpSnapshot:
    """Build snapshot from path → raw value map (tests and gateway adapter)."""
    index = {p.lstrip("/"): v for p, v in path_values.items()}

    circuits: list[CircuitSnapshot] = []
    circuit_ids: set[str] = set()
    for path in index:
        if "/heatingCircuits/" in f"/{path}" or path.startswith("heatingCircuits/"):
            parts = path.split("/")
            try:
                idx = parts.index("heatingCircuits")
                circuit_ids.add(parts[idx + 1])
            except (ValueError, IndexError):
                continue
    for cid in sorted(circuit_ids):
        prefix = f"heatingCircuits/{cid}"
        room = _as_float(_path_value(index, f"{prefix}/roomtemperature"))
        circuits.append(
            CircuitSnapshot(
                circuit_id=cid,
                status=_path_value(index, f"{prefix}/status"),
                pump_modulation=_as_float(
                    _path_value(index, f"{prefix}/pumpModulation")
                ),
                supply_setpoint=_as_float(
                    _path_value(index, f"{prefix}/supplyTemperatureSetpoint")
                ),
                supply_actual=_as_float(
                    _path_value(index, f"{prefix}/actualSupplyTemperature")
                ),
                room_temperature=room,
            )
        )

    return HeatpumpSnapshot(
        hs_type=_path_value(index, "heatSources/hs1/type"),
        actual_modulation=_as_float(
            _path_value(index, "heatSources/actualModulation")
        )
        or 0.0,
        appliance_supply_temp=_as_float(
            _path_value(index, "heatSources/applianceSupplyTemperature")
        ),
        hs1_modulation=_as_float(
            _path_value(index, "heatSources/hs1/actualModulation")
        )
        or 0.0,
        supply_temp=_as_float(
            _path_value(
                index,
                "heatSources/actualSupplyTemperature",
                "system/sensors/temperatures/supply_t1",
            )
        ),
        return_temp=_as_float(
            _path_value(
                index,
                "heatSources/returnTemperature",
                "system/sensors/temperatures/return",
            )
        ),
        outdoor_temp=_as_float(
            _path_value(index, "system/sensors/temperatures/outdoor_t1")
        ),
        dhw_charge=_path_value(index, "dhwCircuits/dhw1/charge"),
        dhw_actual=_as_float(_path_value(index, "dhwCircuits/dhw1/actualTemp")),
        dhw_setpoint=_as_float(
            _path_value(index, "dhwCircuits/dhw1/currentSetpoint")
        ),
        pool_status=_path_value(index, "heatSources/poolStatus"),
        pool_temp=_as_float(_path_value(index, "heatSources/poolTemperature")),
        pool_setpoint=_as_float(
            _path_value(index, "heatSources/poolSetpointTemperature")
        ),
        circuits=circuits,
        consumed_today=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/consumedEnergy_Today_kWh",
            )
        ),
        consumed_month=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/consumedEnergy_ThisMonth_kWh",
            )
        ),
        consumed_year=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/consumedEnergy_ThisYear_kWh",
            )
        ),
        produced_today=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/outputProduced_Today_kWh",
            )
        ),
        produced_month=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/outputProduced_ThisMonth_kWh",
            )
        ),
        produced_year=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/outputProduced_ThisYear_kWh",
            )
        ),
        eheater_today=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/eheater_Today_kWh",
            )
        ),
        eheater_month=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/eheater_ThisMonth_kWh",
            )
        ),
        eheater_year=_as_float(
            _path_value(
                index,
                "recordings/heatSources/total/energyMonitoring/eheater_ThisYear_kWh",
            )
        ),
        last_hour_electricity=_recording_hour(index, "consumedEnergy"),
        last_hour_heat=_recording_hour(index, "outputProduced"),
        last_hour_eheater=_recording_hour(index, "eheater"),
    )


def build_snapshot_from_gateway(gateway: Any) -> HeatpumpSnapshot | None:
    """Collect path values from gateway sensors/circuits into a snapshot."""
    path_values: dict[str, Any] = {}

    def _add_obj(obj: Any) -> None:
        path = getattr(obj, "path", None) or getattr(obj, "attr_id", None)
        if not path:
            return
        attr = getattr(obj, "attr_id", None) or path
        state: Any = None
        # Prefer the property payload. ``state`` is often a message, not the value.
        if hasattr(obj, "get_property"):
            try:
                prop = obj.get_property(attr)
            except Exception:  # noqa: BLE001
                prop = None
            if isinstance(prop, dict) and "value" in prop:
                state = prop["value"]
        if state is None:
            state = getattr(obj, "state", None)
        path_values[str(path)] = state

    for sensor in getattr(gateway, "sensors", []) or []:
        _add_obj(sensor)

    for ctype in ("hc", "dhw", "sc", "zn", "dv"):
        getter = getattr(gateway, "get_circuits", None)
        if not getter:
            break
        try:
            circuits = getter(ctype) or []
        except Exception:  # noqa: BLE001
            continue
        for circuit in circuits:
            for sensor in getattr(circuit, "sensors", []) or []:
                _add_obj(sensor)

    snapshot = build_snapshot_from_path_map(path_values)
    if not snapshot.is_heatpump:
        return None
    return snapshot

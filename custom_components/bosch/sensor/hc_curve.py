"""Heating-circuit curve and supply diagnostic sensors on HC devices."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTemperature,
    EntityCategory,
)
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN
from ..heatpump_diagnostics import (
    CircuitSnapshot,
    HeatpumpSnapshot,
    circuit_id_of,
    heating_circuit_ids,
    heating_circuit_objects,
)
from .heatpump_virtual import cached_snapshot

if TYPE_CHECKING:
    from ..coordinator import BoschDataUpdateCoordinator
    from ..types import BoschGateway

_LOGGER = logging.getLogger(__name__)

_HC_CURVE_ATTR = "_hc_curve_sources"

# key, name, CircuitSnapshot field, unit, device_class
_HC_SENSORS: tuple[tuple[str, str, str, str | None, SensorDeviceClass | None], ...] = (
    (
        "SupplySetpoint",
        "Supply setpoint",
        "supply_setpoint",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
    (
        "SupplyActual",
        "Supply actual",
        "supply_actual",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
    (
        "PumpModulation",
        "Pump modulation",
        "pump_modulation",
        PERCENTAGE,
        None,
    ),
    (
        "DesignTemp",
        "Design temperature",
        "design_temp",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
    (
        "HeatCurveMax",
        "Heat curve max",
        "heat_curve_max",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
    (
        "RoomInfluence",
        "Room influence",
        "room_influence",
        PERCENTAGE,
        None,
    ),
    (
        "RoomTempOffset",
        "Room temperature offset",
        "room_temp_offset",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
)


def _circuit_from_snap(
    snap: HeatpumpSnapshot, circuit_id: str
) -> CircuitSnapshot | None:
    """Find one circuit in the snapshot."""
    for circuit in snap.circuits:
        if circuit.circuit_id == circuit_id:
            return circuit
    return None


def _field_getter(
    circuit_id: str, field: str
) -> Callable[[HeatpumpSnapshot], float | None]:
    """Snapshot getter for one circuit float field."""

    def _getter(snap: HeatpumpSnapshot) -> float | None:
        circuit = _circuit_from_snap(snap, circuit_id)
        if circuit is None:
            return None
        value = getattr(circuit, field, None)
        return float(value) if value is not None else None

    return _getter


def _register_hc_circuits(
    coordinator: BoschDataUpdateCoordinator, gateway: BoschGateway
) -> None:
    """Poll heating circuits once, shared by all HC curve sensors."""
    bucket = getattr(coordinator, _HC_CURVE_ATTR, None)
    if bucket is not None:
        bucket["refs"] += 1
        return
    regular: list[Any] = []
    for obj in heating_circuit_objects(gateway):
        coordinator.register_object(obj)
        regular.append(obj)
    setattr(coordinator, _HC_CURVE_ATTR, {"refs": 1, "regular": regular})
    _LOGGER.debug("HC curve sensors polling %d circuits", len(regular))


def _unregister_hc_circuits(coordinator: BoschDataUpdateCoordinator) -> None:
    """Drop HC circuit polls when the last curve sensor is removed."""
    bucket = getattr(coordinator, _HC_CURVE_ATTR, None)
    if not bucket:
        return
    bucket["refs"] -= 1
    if bucket["refs"] > 0:
        return
    for obj in bucket["regular"]:
        coordinator.unregister_object(obj)
    setattr(coordinator, _HC_CURVE_ATTR, None)


class HcCurveNumericSensor(CoordinatorEntity, SensorEntity):
    """Numeric heating-circuit diagnostic on the HC climate device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        gateway: BoschGateway,
        uuid: str,
        circuit_id: str,
        key: str,
        name: str,
        getter: Callable[[HeatpumpSnapshot], float | None],
        *,
        unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
    ) -> None:
        """Initialize HC curve/supply sensor."""
        super().__init__(coordinator)
        self._gateway = gateway
        self._uuid = uuid
        self._circuit_id = circuit_id
        self._getter = getter
        self._attr_name = name
        self._attr_unique_id = f"HcCurve{key}{circuit_id}{uuid}"
        self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = device_class

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to the same device as the HC climate entity."""
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._uuid}_{self._circuit_id}")},
        )
        if self.coordinator.gateway_device_id:
            info["via_device_id"] = self.coordinator.gateway_device_id
        return info

    async def async_added_to_hass(self) -> None:
        """Register HC circuits for polling (refcount shared)."""
        await super().async_added_to_hass()
        _register_hc_circuits(self.coordinator, self._gateway)

    async def async_will_remove_from_hass(self) -> None:
        """Unregister HC circuits when the last sensor leaves."""
        _unregister_hc_circuits(self.coordinator)
        await super().async_will_remove_from_hass()

    def _snapshot(self) -> HeatpumpSnapshot:
        """Reuse snapshot for the current coordinator refresh."""
        return cached_snapshot(self.coordinator, self._gateway)

    @property
    def available(self) -> bool:
        """Available when the circuit appears in the live snapshot."""
        if not self.coordinator.last_update_success:
            return False
        return _circuit_from_snap(self._snapshot(), self._circuit_id) is not None

    @property
    def native_value(self) -> float | None:
        """Return circuit field value."""
        return self._getter(self._snapshot())


def build_hc_curve_entities(
    coordinator: BoschDataUpdateCoordinator,
    gateway: BoschGateway,
    uuid: str,
) -> list[HcCurveNumericSensor]:
    """Create disabled-by-default curve/supply sensors for each HC."""
    entities: list[HcCurveNumericSensor] = []
    circuit_ids = heating_circuit_ids(gateway)
    if not circuit_ids:
        # Fallback: ids from circuit objects only (no snapshot seed).
        for obj in heating_circuit_objects(gateway):
            cid = circuit_id_of(obj)
            if cid and cid not in circuit_ids:
                circuit_ids.append(cid)
    for cid in circuit_ids:
        for key, name, field, unit, device_class in _HC_SENSORS:
            entities.append(
                HcCurveNumericSensor(
                    coordinator,
                    gateway,
                    uuid,
                    cid,
                    key,
                    name,
                    _field_getter(cid, field),
                    unit=unit,
                    device_class=device_class,
                )
            )
    return entities

"""Optional IVT heat-pump virtual diagnostic sensors."""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfTemperature, EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from bosch_thermostat_client.const import HTTP

from ..const import CONF_PROTOCOL, DOMAIN
from ..heatpump_diagnostics import (
    HeatpumpSnapshot,
    build_snapshot_from_gateway,
    classify_efficiency_hint,
    classify_heatpump_mode,
    heating_circuit_ids,
)

if TYPE_CHECKING:
    from ..coordinator import BoschDataUpdateCoordinator
    from ..types import BoschGateway

_LOGGER = logging.getLogger(__name__)

MODE_OPTIONS = [
    "idle",
    "compressor",
    "dhw",
    "space_heating",
    "dhw_and_heating",
    "pool",
    "pool_and_heating",
    "circulation",
]


class _HeatpumpVirtualBase(CoordinatorEntity):
    """Shared base for heat-pump virtual entities."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        gateway: BoschGateway,
        uuid: str,
        key: str,
        name: str,
    ) -> None:
        """Initialize virtual entity."""
        super().__init__(coordinator)
        self._gateway = gateway
        self._uuid = uuid
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"HeatpumpDiag{key}{uuid}"

    @property
    def device_info(self) -> DeviceInfo:
        """Attach to heat-pump diagnostics device."""
        info = DeviceInfo(
            identifiers={(DOMAIN, f"heatpump_diag_{self._uuid}")},
            name="Heat pump diagnostics",
            manufacturer="Bosch",
        )
        if self.coordinator.gateway_device_id:
            info["via_device_id"] = self.coordinator.gateway_device_id
        return info

    def _snapshot(self) -> HeatpumpSnapshot | None:
        """Rebuild snapshot from gateway objects."""
        return build_snapshot_from_gateway(self._gateway)

    @property
    def available(self) -> bool:
        """Available when a heatpump snapshot can be built."""
        return self._snapshot() is not None


class HeatpumpModeSensor(_HeatpumpVirtualBase, SensorEntity):
    """Heat pump operating mode enum."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = MODE_OPTIONS

    @property
    def native_value(self) -> str | None:
        """Return classified mode."""
        snap = self._snapshot()
        return classify_heatpump_mode(snap) if snap else None


class HeatpumpBinarySensor(_HeatpumpVirtualBase, BinarySensorEntity):
    """Binary virtual sensor from a snapshot predicate."""

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        gateway: BoschGateway,
        uuid: str,
        key: str,
        name: str,
        predicate: Callable[[HeatpumpSnapshot], bool],
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        """Initialize binary virtual sensor."""
        super().__init__(coordinator, gateway, uuid, key, name)
        self._predicate = predicate
        if device_class:
            self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        """Return predicate result."""
        snap = self._snapshot()
        return self._predicate(snap) if snap else None


class HeatpumpNumericSensor(_HeatpumpVirtualBase, SensorEntity):
    """Numeric virtual sensor from a snapshot getter."""

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        gateway: BoschGateway,
        uuid: str,
        key: str,
        name: str,
        getter: Callable[[HeatpumpSnapshot], float | None],
        *,
        unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
    ) -> None:
        """Initialize numeric virtual sensor."""
        super().__init__(coordinator, gateway, uuid, key, name)
        self._getter = getter
        self._attr_native_unit_of_measurement = unit
        if device_class:
            self._attr_device_class = device_class

    @property
    def native_value(self) -> float | None:
        """Return getter value."""
        snap = self._snapshot()
        return self._getter(snap) if snap else None


class HeatpumpTextSensor(_HeatpumpVirtualBase, SensorEntity):
    """Text virtual sensor with optional attributes."""

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        gateway: BoschGateway,
        uuid: str,
        key: str,
        name: str,
        getter: Callable[[HeatpumpSnapshot], str | None],
        attrs_getter: Callable[[HeatpumpSnapshot], dict[str, Any]] | None = None,
    ) -> None:
        """Initialize text virtual sensor."""
        super().__init__(coordinator, gateway, uuid, key, name)
        self._getter = getter
        self._attrs_getter = attrs_getter

    @property
    def native_value(self) -> str | None:
        """Return text value."""
        snap = self._snapshot()
        return self._getter(snap) if snap else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Optional attributes."""
        snap = self._snapshot()
        if snap and self._attrs_getter:
            return self._attrs_getter(snap)
        return {}


def should_register_heatpump_virtual(config_entry: Any) -> bool:
    """True when the option is on and the entry uses local HTTP."""
    if not config_entry.options.get("virtual_heatpump_diagnostics", False):
        return False
    protocol = str(config_entry.data.get(CONF_PROTOCOL, "")).lower()
    return protocol in (str(HTTP).lower(), "http")


def build_heatpump_virtual_entities(
    coordinator: BoschDataUpdateCoordinator,
    gateway: BoschGateway,
    uuid: str,
) -> list[Any]:
    """Create all virtual heat-pump entities (disabled by default).

    Registers even when the live snapshot is not Heatpump yet, so entities
    show up after the option is enabled. They stay unavailable until the
    gateway reports a heat-pump type.
    """
    snap = build_snapshot_from_gateway(gateway)
    if snap is None:
        _LOGGER.warning(
            "Heat-pump diagnostics registered, but hs1/type is not Heatpump yet"
        )

    entities: list[Any] = [
        HeatpumpModeSensor(coordinator, gateway, uuid, "Mode", "Heat pump mode"),
        HeatpumpBinarySensor(
            coordinator,
            gateway,
            uuid,
            "Compressor",
            "Compressor active",
            lambda s: s.compressor_on,
            BinarySensorDeviceClass.RUNNING,
        ),
        HeatpumpBinarySensor(
            coordinator,
            gateway,
            uuid,
            "DhwCharging",
            "DHW charging",
            lambda s: s.dhw_charging,
        ),
        HeatpumpBinarySensor(
            coordinator,
            gateway,
            uuid,
            "DhwRequested",
            "DHW requested",
            lambda s: s.dhw_requested,
        ),
        HeatpumpNumericSensor(
            coordinator,
            gateway,
            uuid,
            "DhwGap",
            "DHW temperature gap",
            lambda s: s.dhw_gap,
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
        ),
        HeatpumpBinarySensor(
            coordinator,
            gateway,
            uuid,
            "PoolHeating",
            "Pool requested",
            lambda s: s.pool_requested,
        ),
        HeatpumpNumericSensor(
            coordinator,
            gateway,
            uuid,
            "PoolGap",
            "Pool temperature gap",
            lambda s: s.pool_gap,
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
        ),
        HeatpumpBinarySensor(
            coordinator,
            gateway,
            uuid,
            "SpaceHeating",
            "Space heating active",
            lambda s: s.space_heating_active,
        ),
        HeatpumpNumericSensor(
            coordinator,
            gateway,
            uuid,
            "RequestingCount",
            "Heating circuits requesting",
            lambda s: float(len(s.requesting_circuits)),
        ),
        HeatpumpTextSensor(
            coordinator,
            gateway,
            uuid,
            "PrimaryCircuit",
            "Primary heating circuit",
            lambda s: s.primary_circuit,
        ),
        HeatpumpTextSensor(
            coordinator,
            gateway,
            uuid,
            "Requestors",
            "Heat requestors",
            lambda s: ",".join(s.heat_requestors) or None,
        ),
        HeatpumpNumericSensor(
            coordinator,
            gateway,
            uuid,
            "Delta",
            "Heat source delta",
            lambda s: s.heat_source_delta,
            unit=UnitOfTemperature.CELSIUS,
            device_class=SensorDeviceClass.TEMPERATURE,
        ),
    ]

    circuit_ids = heating_circuit_ids(gateway, snap)
    if not circuit_ids and snap:
        circuit_ids = [c.circuit_id for c in snap.circuits]
    for cid in circuit_ids:
        entities.append(
            HeatpumpBinarySensor(
                coordinator,
                gateway,
                uuid,
                f"CircuitRequest{cid}",
                f"{cid} requesting heat",
                lambda s, c=cid: c in s.requesting_circuits,
            )
        )
        entities.append(
            HeatpumpBinarySensor(
                coordinator,
                gateway,
                uuid,
                f"RoomFault{cid}",
                f"{cid} room sensor fault",
                lambda s, c=cid: any(
                    x.circuit_id == c and x.room_sensor_fault for x in s.circuits
                ),
                BinarySensorDeviceClass.PROBLEM,
            )
        )

    for period, label in (("today", "today"), ("month", "month"), ("year", "year")):
        entities.append(
            HeatpumpNumericSensor(
                coordinator,
                gateway,
                uuid,
                f"Cop{period.title()}",
                f"COP {label}",
                lambda s, p=period: s.cop(p),
            )
        )
        entities.append(
            HeatpumpNumericSensor(
                coordinator,
                gateway,
                uuid,
                f"BackupShare{period.title()}",
                f"Backup heater share {label}",
                lambda s, p=period: (
                    None
                    if s.backup_share(p) is None
                    else round(float(s.backup_share(p)) * 100, 1)
                ),
                unit=PERCENTAGE,
            )
        )

    entities.append(
        HeatpumpBinarySensor(
            coordinator,
            gateway,
            uuid,
            "BackupUsedToday",
            "Backup heater used today",
            lambda s: (s.eheater_today or 0) > 0,
        )
    )
    entities.append(
        HeatpumpTextSensor(
            coordinator,
            gateway,
            uuid,
            "EfficiencyHint",
            "Heat pump efficiency hint",
            lambda s: classify_efficiency_hint(s)[0],
            lambda s: classify_efficiency_hint(s)[1],
        )
    )
    entities.extend(
        [
            HeatpumpNumericSensor(
                coordinator,
                gateway,
                uuid,
                "LastHourElectricity",
                "Last hour electricity",
                lambda s: s.last_hour_electricity,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                device_class=SensorDeviceClass.ENERGY,
            ),
            HeatpumpNumericSensor(
                coordinator,
                gateway,
                uuid,
                "LastHourHeat",
                "Last hour heat",
                lambda s: s.last_hour_heat,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                device_class=SensorDeviceClass.ENERGY,
            ),
            HeatpumpNumericSensor(
                coordinator,
                gateway,
                uuid,
                "LastHourEheater",
                "Last hour backup heater",
                lambda s: s.last_hour_eheater,
                unit=UnitOfEnergy.KILO_WATT_HOUR,
                device_class=SensorDeviceClass.ENERGY,
            ),
        ]
    )
    return entities

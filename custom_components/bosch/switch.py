"""
Support for water heaters connected to Bosch thermostat.

For more details about this platform, please refer to the documentation at...
"""
from __future__ import annotations
import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import BoschDataUpdateCoordinator

from .bosch_entity import BoschEntity
from .const import (
    CIRCUITS,
    CIRCUITS_SENSOR_NAMES,
    DOMAIN,
    BOSCH_GATEWAY_ENTRY,
    SWITCH,
    UUID,
)
from .pool import (
    PoolWriteError,
    async_pool_available,
    get_or_create_pool_poller,
    status_to_hvac,
    HVAC_HEAT,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the Bosch Switch from a config entry."""
    uuid = config_entry.data[UUID]
    data = hass.data[DOMAIN][uuid]
    entry = data[BOSCH_GATEWAY_ENTRY]
    coordinator = entry.coordinator
    enabled_switches = config_entry.data.get(SWITCH, [])
    
    entities = []
    for switch in entry.gateway.regular_switches:
        entities.append(
            BoschSwitch(
                coordinator=coordinator,
                uuid=uuid,
                bosch_object=switch,
                gateway=entry.gateway,
                name=switch.name,
                attr_uri=switch.attr_id,
                domain_name="Switches",
                is_enabled=switch.attr_id in enabled_switches,
            )
        )
    for circ_type in CIRCUITS:
        circuits = entry.gateway.get_circuits(circ_type)
        for circuit in circuits:
            for switch in circuit.regular_switches:
                entities.append(
                    CircuitSwitch(
                        coordinator=coordinator,
                        uuid=uuid,
                        bosch_object=switch,
                        gateway=entry.gateway,
                        name=switch.name,
                        attr_uri=switch.attr_id,
                        domain_name=circuit.name,
                        circuit_type=circ_type,
                        is_enabled=switch.attr_id in enabled_switches,
                    )
                )
    if await async_pool_available(entry.gateway):
        entities.append(
            BoschPoolHeatingSwitch(
                coordinator=coordinator,
                uuid=uuid,
                gateway=entry.gateway,
            )
        )
    async_add_entities(entities)
    return True


class BoschBaseSwitch(BoschEntity, SwitchEntity):
    """Representation of a Bosch charge."""

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        uuid: str,
        bosch_object: Any,
        gateway: Any,
        name: str,
        attr_uri: str,
        domain_name: str,
        circuit_type: str | None = None,
        is_enabled: bool = False,
    ) -> None:
        """Set up device and add update callback to get data from websocket."""
        super().__init__(
            coordinator=coordinator,
            uuid=uuid,
            bosch_object=bosch_object,
            gateway=gateway,
            domain_name=domain_name,
        )
        self._attr_name = name
        self._attr_uri = attr_uri
        self._attr_unique_id = self._domain_name + self._attr_name + self._uuid
        self._attrs = {}
        self._circuit_type = circuit_type
        self._attr_entity_registry_enabled_default = is_enabled

    @property
    def is_on(self):
        """Return true if switch is on."""
        return self._bosch_object.state

    async def async_turn_on(self, **kwargs):
        """Turn on switch."""
        _LOGGER.debug("Turning on %s switch.", self._attr_name)
        await self._bosch_object.turn_on()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs):
        """Turn off switch."""
        _LOGGER.debug("Turning off %s switch.", self._attr_name)
        await self._bosch_object.turn_off()
        await self.coordinator.async_request_refresh()



class BoschSwitch(BoschBaseSwitch):
    """Representation of a Bosch switch."""

    @property
    def device_name(self):
        return "Bosch switches"


class CircuitSwitch(BoschBaseSwitch):
    """Representation of a Bosch circuit switch."""

    @property
    def device_name(self):
        return CIRCUITS_SENSOR_NAMES[self._circuit_type] + " " + self._domain_name


class BoschPoolHeatingSwitch(CoordinatorEntity, SwitchEntity):
    """One-tap pool heating on/off, shared with pool climate."""

    _attr_has_entity_name = True
    _attr_name = "Pool heating"
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: BoschDataUpdateCoordinator,
        uuid: str,
        gateway: Any,
    ) -> None:
        """Bind shared pool poller."""
        super().__init__(coordinator)
        self._uuid = uuid
        self._gateway = gateway
        self._poller = get_or_create_pool_poller(coordinator, gateway)
        self._attr_unique_id = f"{uuid}pool_heating"

    @property
    def device_info(self) -> DeviceInfo:
        """Child Pool device under the gateway."""
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._uuid}_pool")},
            name="Pool",
            manufacturer=self._gateway.device_model,
            model=self._gateway.device_type,
            sw_version=self._gateway.firmware,
            hw_version=self._uuid,
        )
        if self.coordinator.gateway_device_id:
            info["via_device_id"] = self.coordinator.gateway_device_id
        return info

    async def async_added_to_hass(self) -> None:
        """Register shared poller and seed values."""
        await super().async_added_to_hass()
        self.coordinator.register_object(self._poller)
        await self._poller.update()

    async def async_will_remove_from_hass(self) -> None:
        """Unregister shared poller."""
        self.coordinator.unregister_object(self._poller)
        await super().async_will_remove_from_hass()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh switch state from poller."""
        super()._handle_coordinator_update()

    @property
    def is_on(self) -> bool:
        """True when pool heating is requested."""
        return status_to_hvac(self._poller.status) == HVAC_HEAT

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn pool heating on."""
        try:
            await self._poller.async_set_status("on")
        except PoolWriteError as err:
            raise HomeAssistantError(f"Failed to turn pool heating on: {err}") from err
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn pool heating off."""
        try:
            await self._poller.async_set_status("off")
        except PoolWriteError as err:
            raise HomeAssistantError(f"Failed to turn pool heating off: {err}") from err
        self.async_write_ha_state()
        await self.coordinator.async_request_refresh()

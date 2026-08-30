"""Binary sensor showing a manually triggered thermal disinfection run."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import INPUT_COILS, OUTPUT_COILS
from .coordinator import DimplexDataCoordinator
from .disinfection import ThermalDisinfectionManager
from .entity import build_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    device_info = build_device_info(entry)
    entities: list = [
        DimplexDisinfectionRunSensor(runtime.disinfection, entry, device_info)
    ]
    entities += [
        DimplexCoilBinarySensor(runtime.data_coordinator, entry, coil, device_info)
        for coil in (*INPUT_COILS, *OUTPUT_COILS)
    ]
    async_add_entities(entities)


class DimplexDisinfectionRunSensor(BinarySensorEntity):
    """On while a manually triggered disinfection run is being supervised.

    This reflects the manual run, not the heat pump's own weekly schedule:
    the status coil it is based on sits behind the multiplexer and is only
    polled while a manual run is in progress.
    """

    _attr_has_entity_name = True
    _attr_name = "Thermal disinfection manual run"
    _attr_icon = "mdi:water-thermometer"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_should_poll = False

    def __init__(
        self, manager: ThermalDisinfectionManager, entry, device_info: DeviceInfo
    ) -> None:
        self._manager = manager
        self._attr_unique_id = f"{entry.entry_id}_disinfection_run"
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._manager.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self._manager.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._manager.attributes


class DimplexCoilBinarySensor(
    CoordinatorEntity[DimplexDataCoordinator], BinarySensorEntity
):
    """A digital input or output of the heat pump manager.

    The 2nd heat generator output is the interesting one here: together with its
    operating-hours counter it answers what that second heat generator
    actually is. The WPM can only ever switch on something electrical or a
    boiler contact - it cannot light a wood stove, so a stove is wired to the
    separate "Regenerativ" input instead.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, coil, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._coil = coil
        self._attr_unique_id = f"{entry.entry_id}_{coil.key}"
        self._attr_name = coil.name
        self._attr_icon = coil.icon
        self._attr_device_info = device_info
        self._attr_entity_registry_enabled_default = coil.enabled_default
        if coil.device_class == "running":
            self._attr_device_class = BinarySensorDeviceClass.RUNNING
        elif coil.device_class == "problem":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._coil.key in self.coordinator.data
        )

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._coil.key)

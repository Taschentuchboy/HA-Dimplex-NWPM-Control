"""Sensor entities for the Dimplex NWPM integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ENERGY_REGISTERS,
    ENUM_SENSORS,
    OPTIONAL_SENSOR_REGISTERS,
    RUNTIME_REGISTERS,
    SENSOR_REGISTERS,
)
from .coordinator import DimplexDataCoordinator
from .entity import build_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DimplexDataCoordinator = entry.runtime_data.data_coordinator
    device_info = build_device_info(entry)

    entities: list = [
        DimplexRegisterSensor(coordinator, entry, reg, device_info)
        for reg in SENSOR_REGISTERS
    ]
    entities += [
        DimplexRegisterSensor(coordinator, entry, reg, device_info, enabled=False)
        for reg in OPTIONAL_SENSOR_REGISTERS
    ]
    entities += [
        DimplexRuntimeSensor(coordinator, entry, reg, device_info)
        for reg in RUNTIME_REGISTERS
    ]
    entities += [
        DimplexEnumSensor(coordinator, entry, enum_def, device_info)
        for enum_def in ENUM_SENSORS
    ]
    entities += [
        DimplexEnergySensor(coordinator, entry, energy, device_info)
        for energy in ENERGY_REGISTERS
    ]
    entities.append(DimplexPartyHoursSensor(coordinator, entry, device_info))
    async_add_entities(entities)


class DimplexRegisterSensor(CoordinatorEntity[DimplexDataCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator, entry, reg, device_info: DeviceInfo, enabled: bool = True
    ) -> None:
        super().__init__(coordinator)
        self._reg = reg
        self._attr_unique_id = f"{entry.entry_id}_{reg.key}"
        self._attr_name = reg.name
        self._attr_native_unit_of_measurement = reg.unit
        self._attr_device_info = device_info
        self._attr_entity_registry_enabled_default = enabled
        if reg.device_class == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
        elif reg.device_class == "humidity":
            self._attr_device_class = SensorDeviceClass.HUMIDITY
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        # Datapoints the installation does not provide are dropped by the
        # coordinator; reporting them as unavailable is honest, whereas
        # showing 0 would look like a real measurement.
        return (
            super().available
            and self.coordinator.data is not None
            and self._reg.key in self.coordinator.data
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._reg.key)


class DimplexPartyHoursSensor(CoordinatorEntity[DimplexDataCoordinator], SensorEntity):
    """Read-only mirror of the party hours; a writable Number is also exposed."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_party_hours_sensor"
        self._attr_name = "Party hours remaining"
        self._attr_device_info = device_info

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("party_hours")


class DimplexRuntimeSensor(CoordinatorEntity[DimplexDataCoordinator], SensorEntity):
    """Cumulative operating-hours counter."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, entry, reg, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._reg = reg
        self._attr_unique_id = f"{entry.entry_id}_{reg.key}"
        self._attr_name = reg.name
        self._attr_native_unit_of_measurement = reg.unit
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._reg.key in self.coordinator.data
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._reg.key)


class DimplexEnumSensor(CoordinatorEntity[DimplexDataCoordinator], SensorEntity):
    """Status / block reason / fault as readable text instead of a raw code."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, enum_def, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._def = enum_def
        self._attr_unique_id = f"{entry.entry_id}_{enum_def.key}"
        self._attr_name = enum_def.name
        self._attr_icon = enum_def.icon
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._def.key in self.coordinator.data
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        raw = self.coordinator.data.get(self._def.key)
        if raw is None:
            return None
        # Unknown codes are surfaced as-is rather than swallowed: the value
        # tables differ between WPM software generations, so an unmapped code
        # is a useful hint that this system uses a different one.
        return self._def.mapping.get(raw, f"Unbekannt ({raw})")

    @property
    def extra_state_attributes(self) -> dict:
        if self.coordinator.data is None:
            return {}
        raw = self.coordinator.data.get(self._def.key)
        return {} if raw is None else {"raw_value": raw}


class DimplexEnergySensor(CoordinatorEntity[DimplexDataCoordinator], SensorEntity):
    """Heat meter total, reassembled from three 4-digit registers."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "kWh"
    _attr_icon = "mdi:counter"
    # Only present with an integrated or external heat meter (WMZ 25/32).
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, entry, energy, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._energy = energy
        self._attr_unique_id = f"{entry.entry_id}_{energy.key}"
        self._attr_name = energy.name
        self._attr_device_info = device_info

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self._energy.key in self.coordinator.data
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._energy.key)

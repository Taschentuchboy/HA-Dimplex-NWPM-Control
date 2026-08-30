"""Number entities: direct settings plus the per-schedule extra value."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ADDR_HOLIDAY_DAYS,
    ADDR_HOT_WATER_TARGET_TEMP,
    ADDR_PARALLEL_DISPLACEMENT,
    ADDR_PARTY_HOURS,
    PARALLEL_DISPLACEMENT_OFFSET,
    SCHEDULE_FUNCTIONS,
)
from .entity import build_device_info
from .coordinator import DimplexDataCoordinator
from .modbus_hub import DimplexModbusHub
from .schedule import ScheduleCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    device_info = build_device_info(entry)

    entities: list = [
        DimplexPartyHoursNumber(runtime.data_coordinator, runtime.hub, entry, device_info),
        DimplexHolidayDaysNumber(runtime.data_coordinator, runtime.hub, entry, device_info),
        DimplexParallelDisplacementNumber(runtime.data_coordinator, runtime.hub, entry, device_info),
        DimplexHotWaterTargetTempNumber(runtime.data_coordinator, runtime.hub, entry, device_info),
    ]
    for func in SCHEDULE_FUNCTIONS:
        if func.value is not None:
            entities.append(
                DimplexScheduleValueNumber(
                    runtime.schedule_coordinator, runtime.hub, entry, device_info, func
                )
            )
    async_add_entities(entities)


class _DirectRegisterNumber(CoordinatorEntity[DimplexDataCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, hub: DimplexModbusHub, entry, device_info: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._attr_device_info = device_info


class DimplexPartyHoursNumber(_DirectRegisterNumber):
    _attr_name = "Party hours"
    _attr_native_min_value = 0
    _attr_native_max_value = 72
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"

    def __init__(self, coordinator, hub, entry, device_info) -> None:
        super().__init__(coordinator, hub, entry, device_info)
        self._attr_unique_id = f"{entry.entry_id}_party_hours_number"

    @property
    def native_value(self):
        return self.coordinator.data.get("party_hours") if self.coordinator.data else None

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.async_write_register(ADDR_PARTY_HOURS, int(value))
        if self.coordinator.data is not None:
            self.coordinator.data["party_hours"] = int(value)
            self.coordinator.async_set_updated_data(self.coordinator.data)


class DimplexHolidayDaysNumber(_DirectRegisterNumber):
    _attr_name = "Holiday days"
    _attr_native_min_value = 0
    _attr_native_max_value = 150
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "d"

    def __init__(self, coordinator, hub, entry, device_info) -> None:
        super().__init__(coordinator, hub, entry, device_info)
        self._attr_unique_id = f"{entry.entry_id}_holiday_days_number"

    @property
    def native_value(self):
        return self.coordinator.data.get("holiday_days") if self.coordinator.data else None

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.async_write_register(ADDR_HOLIDAY_DAYS, int(value))
        if self.coordinator.data is not None:
            self.coordinator.data["holiday_days"] = int(value)
            self.coordinator.async_set_updated_data(self.coordinator.data)


class DimplexParallelDisplacementNumber(_DirectRegisterNumber):
    """Heizkurve Parallelverschiebung, real value -19..+19 K (raw 0-38)."""

    _attr_name = "Heating curve parallel shift"
    _attr_native_min_value = -PARALLEL_DISPLACEMENT_OFFSET
    _attr_native_max_value = PARALLEL_DISPLACEMENT_OFFSET
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "K"

    def __init__(self, coordinator, hub, entry, device_info) -> None:
        super().__init__(coordinator, hub, entry, device_info)
        self._attr_unique_id = f"{entry.entry_id}_parallel_displacement_number"

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        raw = self.coordinator.data.get("parallel_displacement_raw")
        return None if raw is None else raw - PARALLEL_DISPLACEMENT_OFFSET

    async def async_set_native_value(self, value: float) -> None:
        raw = int(value) + PARALLEL_DISPLACEMENT_OFFSET
        await self._hub.async_write_register(ADDR_PARALLEL_DISPLACEMENT, raw)
        if self.coordinator.data is not None:
            self.coordinator.data["parallel_displacement_raw"] = raw
            self.coordinator.async_set_updated_data(self.coordinator.data)


class DimplexHotWaterTargetTempNumber(_DirectRegisterNumber):
    _attr_name = "Hot water target temperature"
    _attr_native_min_value = 30
    _attr_native_max_value = 85
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "°C"
    _attr_device_class = NumberDeviceClass.TEMPERATURE

    def __init__(self, coordinator, hub, entry, device_info) -> None:
        super().__init__(coordinator, hub, entry, device_info)
        self._attr_unique_id = f"{entry.entry_id}_hot_water_target_temp_number"

    @property
    def native_value(self):
        return self.coordinator.data.get("hot_water_target_temp") if self.coordinator.data else None

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.async_write_register(ADDR_HOT_WATER_TARGET_TEMP, int(value))
        if self.coordinator.data is not None:
            self.coordinator.data["hot_water_target_temp"] = int(value)
            self.coordinator.async_set_updated_data(self.coordinator.data)


class DimplexScheduleValueNumber(CoordinatorEntity[ScheduleCoordinator], NumberEntity):
    """The extra register at the end of a block.

    This is the Absenk-/Anhebwert in K for the heating-circuit schedules and
    the target temperature in degC for thermal disinfection, so range, unit
    and device class all come from the function's ValueDef.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: ScheduleCoordinator,
        hub: DimplexModbusHub,
        entry,
        device_info: DeviceInfo,
        func,
    ) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._func = func
        self._value_def = func.value
        self._attr_unique_id = f"{entry.entry_id}_{func.key}_value"
        self._attr_name = f"{func.name} {func.value.label}"
        self._attr_native_min_value = func.value.min_value
        self._attr_native_max_value = func.value.max_value
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = func.value.unit
        if func.value.device_class == "temperature":
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        self._attr_device_info = device_info


    @property
    def available(self) -> bool:
        # Deliberately not tied to the coordinator's last_update_success.
        # Schedules are only read on demand, so the cached values stay valid
        # between refreshes; a single failed read must not blank every
        # schedule entity on the device. Unavailable means "never read this
        # function successfully", not "the most recent attempt failed".
        data = self.coordinator.data
        return data is not None and self._func.key in data

    @property
    def native_value(self):
        return self.coordinator.get(self._func.key, "value")

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.async_write_schedule_register(
            self._func.mux_value, self._value_def.offset, int(value)
        )
        self.coordinator.set_cached(self._func.key, "value", int(value))

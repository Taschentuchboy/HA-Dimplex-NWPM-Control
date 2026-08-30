"""Select entities: operating mode plus per-schedule weekday behaviour."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ADDR_MODE,
    OPERATING_MODES,
    SCHEDULE_FUNCTIONS,
    WEEKDAY_OFFSETS,
)
from .coordinator import DimplexDataCoordinator
from .entity import build_device_info
from .modbus_hub import DimplexModbusHub
from .schedule import ScheduleCoordinator

_MODE_NAME_TO_VALUE = {v: k for k, v in OPERATING_MODES.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    device_info = build_device_info(entry)

    entities: list = [
        DimplexModeSelect(runtime.data_coordinator, runtime.hub, entry, device_info)
    ]
    for func in SCHEDULE_FUNCTIONS:
        for wd_key, wd_label, offset, _py_weekday in WEEKDAY_OFFSETS:
            entities.append(
                DimplexWeekdaySelect(
                    runtime.schedule_coordinator,
                    runtime.hub,
                    entry,
                    device_info,
                    func,
                    wd_key,
                    wd_label,
                    offset,
                )
            )
    async_add_entities(entities)


class DimplexModeSelect(CoordinatorEntity[DimplexDataCoordinator], SelectEntity):
    """Operating mode (Summer / Auto / Holiday / Party / 2nd generator / Cooling)."""

    _attr_has_entity_name = True
    _attr_options = list(OPERATING_MODES.values())
    _attr_icon = "mdi:heat-pump"

    def __init__(
        self, coordinator, hub: DimplexModbusHub, entry, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._attr_unique_id = f"{entry.entry_id}_mode"
        self._attr_name = "Operating mode"
        self._attr_device_info = device_info

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return OPERATING_MODES.get(self.coordinator.data.get("mode"))

    async def async_select_option(self, option: str) -> None:
        value = _MODE_NAME_TO_VALUE[option]
        await self._hub.async_write_register(ADDR_MODE, value)
        if self.coordinator.data is not None:
            self.coordinator.data["mode"] = value
            self.coordinator.async_set_updated_data(self.coordinator.data)


class DimplexWeekdaySelect(CoordinatorEntity[ScheduleCoordinator], SelectEntity):
    """Weekday behaviour for one schedule function.

    The available options differ per function: schedule-style functions offer
    Ja/Nein/Zeit 1/Zeit 2, thermal disinfection only Ja/Nein.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: ScheduleCoordinator,
        hub: DimplexModbusHub,
        entry,
        device_info: DeviceInfo,
        func,
        wd_key: str,
        wd_label: str,
        offset: int,
    ) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._func = func
        self._wd_key = wd_key
        self._offset = offset
        self._name_to_value = {v: k for k, v in func.weekday_modes.items()}
        self._attr_options = list(func.weekday_modes.values())
        self._attr_unique_id = f"{entry.entry_id}_{func.key}_{wd_key}"
        self._attr_name = f"{func.name} {wd_label}"
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
    def current_option(self) -> str | None:
        raw = self.coordinator.get(self._func.key, self._wd_key)
        if raw is None:
            return None
        return self._func.weekday_modes.get(raw)

    async def async_select_option(self, option: str) -> None:
        value = self._name_to_value[option]
        await self._hub.async_write_schedule_register(
            self._func.mux_value, self._offset, value
        )
        self.coordinator.set_cached(self._func.key, self._wd_key, value)

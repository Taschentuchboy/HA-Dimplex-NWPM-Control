"""Time entities: start/end times of each schedule function's windows."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import SCHEDULE_FUNCTIONS
from .entity import build_device_info
from .modbus_hub import DimplexModbusHub
from .schedule import ScheduleCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    device_info = build_device_info(entry)

    entities = [
        DimplexScheduleTime(
            runtime.schedule_coordinator,
            runtime.hub,
            entry,
            device_info,
            func,
            window,
        )
        for func in SCHEDULE_FUNCTIONS
        for window in func.windows
    ]
    async_add_entities(entities)


class DimplexScheduleTime(CoordinatorEntity[ScheduleCoordinator], TimeEntity):
    """A clock time backed by an hour+minute register pair."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: ScheduleCoordinator,
        hub: DimplexModbusHub,
        entry,
        device_info: DeviceInfo,
        func,
        window,
    ) -> None:
        super().__init__(coordinator)
        self._hub = hub
        self._func = func
        self._window = window
        self._attr_unique_id = f"{entry.entry_id}_{func.key}_{window.key}"
        self._attr_name = f"{func.name} {window.label}"
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
    def native_value(self) -> dt_time | None:
        return self.coordinator.get(self._func.key, self._window.key)

    async def async_set_value(self, value: dt_time) -> None:
        # Hour and minute sit in consecutive registers, so a single
        # write_registers call keeps them consistent on the device.
        await self._hub.async_write_schedule_registers(
            self._func.mux_value, self._window.offset, [value.hour, value.minute]
        )
        self.coordinator.set_cached(self._func.key, self._window.key, value)

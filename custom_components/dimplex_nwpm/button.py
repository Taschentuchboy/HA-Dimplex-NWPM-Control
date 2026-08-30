"""Button to force a fresh read of all schedule functions from the device.

Useful after changing a schedule via the heat pump's own display, since the
schedule entities in Home Assistant otherwise only reflect what was last
read at startup or written from HA itself.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_BYPASS_MINUTES
from .bypass import HotWaterBypassManager
from .disinfection import ThermalDisinfectionManager
from .entity import build_device_info
from .schedule import ScheduleCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    device_info = build_device_info(entry)
    async_add_entities(
        [
            DimplexRefreshSchedulesButton(
                runtime.schedule_coordinator, entry, device_info
            ),
            DimplexBypassHotWaterLockButton(runtime.bypass, entry, device_info),
            DimplexStartDisinfectionButton(runtime.disinfection, entry, device_info),
        ]
    )


class DimplexRefreshSchedulesButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Refresh schedules"

    def __init__(self, coordinator: ScheduleCoordinator, entry, device_info: DeviceInfo) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh_schedules"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        await self._coordinator.async_refresh()


class DimplexBypassHotWaterLockButton(ButtonEntity):
    """One press = hot water available for the next hour, then back to plan."""

    _attr_has_entity_name = True
    _attr_name = f"Suspend hot water block for {DEFAULT_BYPASS_MINUTES} minutes"
    _attr_icon = "mdi:water-boiler-off"

    def __init__(
        self, bypass: HotWaterBypassManager, entry, device_info: DeviceInfo
    ) -> None:
        self._bypass = bypass
        self._attr_unique_id = f"{entry.entry_id}_bypass_hot_water_lock"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        await self._bypass.async_start(DEFAULT_BYPASS_MINUTES)


class DimplexStartDisinfectionButton(ButtonEntity):
    """Start a thermal disinfection run now; the schedule is restored after."""

    _attr_has_entity_name = True
    _attr_name = "Start thermal disinfection now"
    _attr_icon = "mdi:water-thermometer"

    def __init__(
        self, manager: ThermalDisinfectionManager, entry, device_info: DeviceInfo
    ) -> None:
        self._manager = manager
        self._attr_unique_id = f"{entry.entry_id}_start_disinfection"
        self._attr_device_info = device_info

    async def async_press(self) -> None:
        await self._manager.async_start()

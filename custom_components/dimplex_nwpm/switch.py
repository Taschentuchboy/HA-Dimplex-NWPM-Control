"""Switch showing (and cancelling) the temporary hot-water-lock bypass."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bypass import HotWaterBypassManager
from .const import DEFAULT_BYPASS_MINUTES
from .entity import build_device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = entry.runtime_data
    async_add_entities(
        [DimplexHotWaterBypassSwitch(runtime.bypass, entry, build_device_info(entry))]
    )


class DimplexHotWaterBypassSwitch(SwitchEntity):
    """On = hot-water lock suspended; off = normal schedule active.

    Turning it on starts a bypass of the default duration; turning it off
    restores the schedule immediately, before the timer would have expired.
    """

    _attr_has_entity_name = True
    _attr_name = "Hot water block suspended"
    _attr_icon = "mdi:water-boiler-alert"
    _attr_should_poll = False

    def __init__(
        self, bypass: HotWaterBypassManager, entry, device_info: DeviceInfo
    ) -> None:
        self._bypass = bypass
        self._attr_unique_id = f"{entry.entry_id}_hot_water_bypass_switch"
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self._bypass.async_add_listener(self.async_write_ha_state)
        )

    @property
    def is_on(self) -> bool:
        return self._bypass.active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the end time so a dashboard can show the remaining time.

        The keys are omitted entirely while no bypass is running rather than
        being reported as None: templates that call as_timestamp() on the
        attribute raise if handed None, and Home Assistant evaluates such
        templates even when the card displaying them is hidden.
        """
        ends_at = self._bypass.ends_at
        if ends_at is None:
            return {}
        return {
            "ends_at": ends_at.isoformat(),
            "weekday": self._bypass.weekday_key,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._bypass.async_start(DEFAULT_BYPASS_MINUTES)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._bypass.async_cancel()

"""Cache and read/write helpers for the multiplexed schedule functions.

Schedule entities do NOT poll the device on a fixed interval - that would
constantly flip the shared multiplexer register (5065) back and forth and
race with the user editing a value. Instead:

  * On startup (and whenever the user presses the "Refresh schedules"
    button), the coordinator reads every configured schedule function once,
    sequentially, and caches the result.
  * Each schedule entity (time / select / number) shows the cached value.
  * When the user changes a value in Home Assistant, the entity writes just
    that one register (or a 2-register hour/minute pair) through the hub -
    which transparently re-selects the multiplexer if needed - and then
    updates the local cache so the UI reflects the change immediately
    without paying for a full re-read.
"""

from __future__ import annotations

import logging
from datetime import time as dt_time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    SCHEDULE_FUNCTIONS,
    WEEKDAY_OFFSETS,
    ScheduleFunctionDef,
)
from .modbus_hub import DimplexModbusError, DimplexModbusHub

_LOGGER = logging.getLogger(__name__)


def _safe_time(hour: int, minute: int) -> dt_time | None:
    """Build a time, tolerating out-of-range values from an unset register."""
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return dt_time(hour, minute)
    _LOGGER.debug("Invalid time read from register: %s:%s", hour, minute)
    return None


def parse_block(func: ScheduleFunctionDef, raw: list[int]) -> dict[str, Any]:
    """Turn a raw register block into the cached representation."""
    parsed: dict[str, Any] = {}
    for window in func.windows:
        parsed[window.key] = _safe_time(raw[window.offset], raw[window.offset + 1])
    for key, _label, offset, _py_weekday in WEEKDAY_OFFSETS:
        parsed[key] = raw[offset]
    if func.value is not None:
        parsed["value"] = raw[func.value.offset]
    return parsed


class ScheduleCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Holds the last known state of every configured schedule function."""

    def __init__(self, hass: HomeAssistant, hub: DimplexModbusHub) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_schedules",
            update_interval=None,  # manual refresh only, see module docstring
        )
        self.hub = hub

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        # Start from what is already known so that one unreadable function
        # does not discard the other four. Each function needs its own
        # multiplexer switch, so a transient failure on one of them (the heat
        # pump display being used at that moment, for instance) is a normal
        # occurrence rather than a reason to blank the whole device.
        data: dict[str, dict[str, Any]] = (
            {key: dict(value) for key, value in self.data.items()}
            if self.data
            else {}
        )
        failed: list[str] = []

        for func in SCHEDULE_FUNCTIONS:
            try:
                raw = await self.hub.async_read_schedule_block(
                    func.mux_value, func.block_length
                )
            except DimplexModbusError as err:
                failed.append(func.name)
                _LOGGER.warning(
                    "Could not read schedule '%s'; keeping the previously known "
                    "values: %s",
                    func.name,
                    err,
                )
                continue
            data[func.key] = parse_block(func, raw)

        # Only treat it as a real failure if nothing at all could be read;
        # that points at the connection rather than at a single function.
        if len(failed) == len(SCHEDULE_FUNCTIONS):
            raise UpdateFailed(
                "None of the schedules could be read: "
                + ", ".join(failed)
            )

        return data

    def get(self, func_key: str, field: str) -> Any:
        return self.data.get(func_key, {}).get(field) if self.data else None

    def set_cached(self, func_key: str, field: str, value: Any) -> None:
        if self.data is None:
            return
        self.data.setdefault(func_key, {})[field] = value
        self.async_set_updated_data(self.data)

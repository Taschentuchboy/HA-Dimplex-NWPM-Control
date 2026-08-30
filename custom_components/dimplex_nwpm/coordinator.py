"""Coordinator for the regularly polled (non-scheduled) registers."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ADDR_HOT_WATER_MAX_TEMP,
    ADDR_HOT_WATER_TARGET_TEMP,
    ADDR_PARALLEL_DISPLACEMENT,
    DOMAIN,
    ENERGY_DIGIT_BASE_HIGH,
    ENERGY_DIGIT_BASE_MID,
    ENERGY_REGISTERS,
    ENUM_SENSORS,
    INPUT_COILS,
    OPTIONAL_SENSOR_REGISTERS,
    OUTPUT_COILS,
    RUNTIME_REGISTERS,
    SENSOR_REGISTERS,
    SETTINGS_BLOCK_COUNT,
    SETTINGS_BLOCK_START,
)
from .modbus_hub import DimplexModbusError, DimplexModbusHub

_LOGGER = logging.getLogger(__name__)


class DimplexDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the plain (non-multiplexed) holding registers and coils.

    Datapoints are grouped into "core" and "optional". A failure on a core
    register fails the whole update, because it means the connection or the
    device is broken. Optional datapoints (extra sensors, heat meters, coils
    the WPM does not implement on this model) are allowed to fail
    individually: the heat pump answers with a Modbus exception for hardware
    it does not have, and one absent accessory must not take down every other
    entity. Such datapoints are simply left out of the data dict, so their
    entities report "unavailable" rather than a wrong value.
    """

    def __init__(self, hass: HomeAssistant, hub: DimplexModbusHub, scan_interval: int) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_data",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.hub = hub
        # Remember which optional datapoints failed so the log does not repeat
        # the same warning on every poll.
        self._reported_missing: set[str] = set()

    def _note_missing(self, key: str, err: Exception) -> None:
        if key not in self._reported_missing:
            self._reported_missing.add(key)
            _LOGGER.info(
                "Datenpunkt '%s' wird von dieser Anlage nicht bereitgestellt "
                "und bleibt leer (%s)",
                key,
                err,
            )

    async def _read_scaled(self, data: dict[str, Any], reg, required: bool) -> None:
        try:
            raw = await self.hub.async_read_holding_registers(
                reg.address, 1, signed=reg.signed
            )
        except DimplexModbusError as err:
            if required:
                raise
            self._note_missing(reg.key, err)
            return
        value = raw[0]
        if reg.scale != 1.0:
            value = round(value * reg.scale, reg.precision)
        data[reg.key] = value

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            # -- core temperatures ---------------------------------------
            for reg in SENSOR_REGISTERS:
                await self._read_scaled(data, reg, required=True)

            # -- settings block 5015-5017 --------------------------------
            settings = await self.hub.async_read_holding_registers(
                SETTINGS_BLOCK_START, SETTINGS_BLOCK_COUNT
            )
            data["mode"] = settings[0]
            data["party_hours"] = settings[1]
            data["holiday_days"] = settings[2]

            parallel = await self.hub.async_read_holding_registers(
                ADDR_PARALLEL_DISPLACEMENT, 1
            )
            data["parallel_displacement_raw"] = parallel[0]

            hw_target = await self.hub.async_read_holding_registers(
                ADDR_HOT_WATER_TARGET_TEMP, 1
            )
            data["hot_water_target_temp"] = hw_target[0]

            hw_max = await self.hub.async_read_holding_registers(
                ADDR_HOT_WATER_MAX_TEMP, 1
            )
            data["hot_water_max_temp"] = hw_max[0]

        except DimplexModbusError as err:
            raise UpdateFailed(str(err)) from err

        # -- everything below is optional --------------------------------
        for reg in OPTIONAL_SENSOR_REGISTERS:
            await self._read_scaled(data, reg, required=False)

        for reg in RUNTIME_REGISTERS:
            await self._read_scaled(data, reg, required=False)

        for enum_def in ENUM_SENSORS:
            try:
                raw = await self.hub.async_read_holding_registers(enum_def.address, 1)
            except DimplexModbusError as err:
                self._note_missing(enum_def.key, err)
                continue
            data[enum_def.key] = raw[0]

        for energy in ENERGY_REGISTERS:
            try:
                # The three digit groups are consecutive, so one read is enough.
                raw = await self.hub.async_read_holding_registers(energy.addr_low, 3)
            except DimplexModbusError as err:
                self._note_missing(energy.key, err)
                continue
            low, mid, high = raw[0], raw[1], raw[2]
            data[energy.key] = (
                high * ENERGY_DIGIT_BASE_HIGH + mid * ENERGY_DIGIT_BASE_MID + low
            )

        for coil in (*INPUT_COILS, *OUTPUT_COILS):
            try:
                bits = await self.hub.async_read_coils(coil.address, 1)
            except DimplexModbusError as err:
                self._note_missing(coil.key, err)
                continue
            data[coil.key] = bool(bits[0])

        return data

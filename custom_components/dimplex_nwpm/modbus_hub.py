"""Locked Modbus TCP client wrapper for the Dimplex NWPM.

Every single Modbus request (plain sensor reads as well as the multiplexed
schedule reads/writes) goes through the same asyncio.Lock. This guarantees
that nothing can slip a write to the time-function multiplexer (register
5065) in between a "select function -> wait -> read/write data block"
sequence, and that the shared TCP connection is never used concurrently.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Callable

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .const import BLOCK_START, MUX_SELECT_ADDRESS, MUX_SETTLE_DELAY

_LOGGER = logging.getLogger(__name__)


class DimplexModbusError(Exception):
    """Raised when a Modbus operation against the heat pump fails."""


def _unit_kwarg_name(func: Callable[..., Any]) -> str:
    """Return the keyword this pymodbus build uses for the unit/slave id.

    pymodbus 4.0 renamed `slave=` to `device_id=` (and `slaves=` to
    `device_ids=`). Home Assistant ships its own pinned pymodbus, which
    overrides whatever this integration declares in manifest.json, so the
    correct keyword has to be determined at runtime rather than assumed.
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):  # builtins / C-extensions without a sig
        return "device_id"
    for candidate in ("device_id", "slave", "unit"):
        if candidate in params:
            return candidate
    # Signature exposes only **kwargs - prefer the modern name.
    return "device_id"


def _to_signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def _to_unsigned16(value: int) -> int:
    return value & 0xFFFF


class DimplexModbusHub:
    """Wraps an AsyncModbusTcpClient with a serialising lock and helpers."""

    def __init__(self, host: str, port: int, slave: int) -> None:
        self._host = host
        self._port = port
        self._slave = slave
        self._client = AsyncModbusTcpClient(host=host, port=port)
        self._lock = asyncio.Lock()
        # Remembers the mux value we last selected, so back-to-back
        # operations on the *same* schedule function don't need to pay the
        # settle delay again. Any operation on a different mux value
        # invalidates this.
        self._last_mux_value: int | None = None
        # None = not yet known, True = register 5065 mirrors the selection
        # back and can be verified, False = write-only on this device.
        self._mux_readback_supported: bool | None = None

        # Resolve the unit-id keyword once per method (see _unit_kwarg_name).
        self._kw_read = _unit_kwarg_name(self._client.read_holding_registers)
        self._kw_read_coils = _unit_kwarg_name(self._client.read_coils)
        self._kw_write_one = _unit_kwarg_name(self._client.write_register)
        self._kw_write_many = _unit_kwarg_name(self._client.write_registers)
        _LOGGER.debug(
            "pymodbus unit-id keyword erkannt: read=%s, read_coils=%s, "
            "write_register=%s, write_registers=%s",
            self._kw_read,
            self._kw_read_coils,
            self._kw_write_one,
            self._kw_write_many,
        )

    async def async_connect(self) -> None:
        async with self._lock:
            # A fresh connection says nothing about the device's current
            # time-function selection - and a reconnect may well have been
            # caused by the WPM restarting, which resets it.
            self._last_mux_value = None
            await self._client.connect()
            if not self._client.connected:
                raise DimplexModbusError(
                    f"Verbindung zu {self._host}:{self._port} fehlgeschlagen"
                )

    async def async_close(self) -> None:
        async with self._lock:
            self._client.close()

    @property
    def connected(self) -> bool:
        return self._client.connected

    # -- low level -----------------------------------------------------

    async def _read_holding_raw(self, address: int, count: int) -> list[int]:
        try:
            result = await self._client.read_holding_registers(
                address, count=count, **{self._kw_read: self._slave}
            )
        except ModbusException as err:
            raise DimplexModbusError(
                f"Read error at register {address} ({count}): {err}"
            ) from err
        if result.isError():
            raise DimplexModbusError(f"Modbus error response for register {address}: {result}")
        return list(result.registers)

    async def _read_coils_raw(self, address: int, count: int) -> list[bool]:
        try:
            result = await self._client.read_coils(
                address, count=count, **{self._kw_read_coils: self._slave}
            )
        except ModbusException as err:
            raise DimplexModbusError(
                f"Read error at coil {address} ({count}): {err}"
            ) from err
        if result.isError():
            raise DimplexModbusError(f"Modbus error response for coil {address}: {result}")
        return list(result.bits)[:count]

    async def _write_register_raw(self, address: int, value: int) -> None:
        try:
            result = await self._client.write_register(
                address, _to_unsigned16(value), **{self._kw_write_one: self._slave}
            )
        except ModbusException as err:
            raise DimplexModbusError(
                f"Write error at register {address}: {err}"
            ) from err
        if result.isError():
            raise DimplexModbusError(f"Modbus error response writing register {address}: {result}")

    async def _write_registers_raw(self, address: int, values: list[int]) -> None:
        try:
            result = await self._client.write_registers(
                address,
                [_to_unsigned16(v) for v in values],
                **{self._kw_write_many: self._slave},
            )
        except ModbusException as err:
            raise DimplexModbusError(
                f"Write error at register block starting {address}: {err}"
            ) from err
        if result.isError():
            raise DimplexModbusError(
                f"Modbus error response writing block at {address}: {result}"
            )

    # -- public: plain (non-multiplexed) registers ----------------------

    async def async_read_holding_registers(
        self, address: int, count: int, signed: bool = False
    ) -> list[int]:
        """Read `count` holding registers starting at `address`."""
        async with self._lock:
            raw = await self._read_holding_raw(address, count)
        return [_to_signed16(v) for v in raw] if signed else raw

    async def async_read_coils(self, address: int, count: int) -> list[bool]:
        """Read `count` coils that are *not* behind the multiplexer.

        The input (5.6) and output (5.7) coils are global device I/O, so they
        must not touch the time-function selector - unlike coil 125, which is
        multiplexed and has to go through async_read_schedule_coil.
        """
        async with self._lock:
            return await self._read_coils_raw(address, count)

    async def async_write_register(self, address: int, value: int) -> None:
        """Write a single holding register that is *not* behind the mux."""
        async with self._lock:
            await self._write_register_raw(address, value)

    # -- public: multiplexed schedule block -----------------------------

    async def _read_mux_selection(self) -> int | None:
        """Read back the time-function selector, or None if not readable.

        Must be called while holding self._lock.
        """
        try:
            return (await self._read_holding_raw(MUX_SELECT_ADDRESS, 1))[0]
        except DimplexModbusError as err:
            _LOGGER.debug(
                "Time-function selector (register %s) is not readable: %s",
                MUX_SELECT_ADDRESS,
                err,
            )
            return None

    async def _ensure_mux_selected(self, mux_value: int) -> None:
        """Point the multiplexer at `mux_value` and confirm it took effect.

        Must be called while holding self._lock.

        Register 5065 is shared device state, not ours: the heat pump's own
        display uses the same selector when someone browses the time
        programs, and the WPM may reset it by itself or after a reconnect.
        An earlier version of this code remembered the last value it had
        written and skipped re-selecting when it matched. Whenever that
        assumption broke, the following write landed in whichever function
        the device happened to have selected - silently corrupting a
        different schedule with no visible pattern.

        Not every WPM mirrors the selection back on a read, though, so
        whether read-back is meaningful is learned from the device on the
        first selection:

        * readable -> the selection is verified before every access, and a
          write is refused outright if the device reports a different
          function (someone editing on the display, for instance).
        * not readable at all (the read itself fails) -> the selector is
          simply rewritten before every single access. That cannot detect
          interference, but it removes the stale-cache failure mode, which
          was the actual cause of corruption.

        Note the distinction: only a *failing* read downgrades to the
        unverified mode. A read that succeeds but returns a different
        function is not a quirk to work around - it means something really
        did change the selection, so the operation is refused.

        Either way the selector is never assumed to have survived from an
        earlier operation.
        """
        selected = (
            await self._read_mux_selection()
            if self._mux_readback_supported is not False
            else None
        )

        # Only skip re-selecting when read-back is known to be meaningful,
        # the device confirms the value, and we are the ones who set it -
        # otherwise it may not have settled yet.
        if (
            self._mux_readback_supported
            and selected == mux_value == self._last_mux_value
        ):
            return

        # Assume nothing until the device confirms the new selection.
        self._last_mux_value = None
        await self._write_register_raw(MUX_SELECT_ADDRESS, mux_value)
        await asyncio.sleep(MUX_SETTLE_DELAY)

        if self._mux_readback_supported is not False:
            confirmed = await self._read_mux_selection()
            if confirmed is None:
                # The register cannot be read on this device. Fall back to
                # rewriting the selector before every access.
                self._mux_readback_supported = False
                _LOGGER.info(
                    "Register %s is not readable. The time function will be re-selected "
                    "before every access; verification is not possible on this "
                    "installation.",
                    MUX_SELECT_ADDRESS,
                )
            elif confirmed == mux_value:
                self._mux_readback_supported = True
            else:
                self._mux_readback_supported = True
                raise DimplexModbusError(
                    f"Could not select time function {mux_value} - register "
                    f"{MUX_SELECT_ADDRESS} reports {confirmed}. Aborted so the "
                    "wrong schedule is not modified. Is someone using the heat "
                    "pump display right now?"
                )

        self._last_mux_value = mux_value

    async def async_read_schedule_block(self, mux_value: int, length: int) -> list[int]:
        """Select `mux_value` and read the `length`-register data block."""
        async with self._lock:
            await self._ensure_mux_selected(mux_value)
            return await self._read_holding_raw(BLOCK_START, length)

    async def async_read_schedule_coil(self, mux_value: int, address: int) -> bool:
        """Select `mux_value` and read one of the status coils behind it.

        Coils 125/126 ("Aktiv Zeit 1/2", and plain "Aktiv" for thermal
        disinfection) are multiplexed just like the data registers, so they
        only mean anything once the matching function has been selected.
        """
        async with self._lock:
            await self._ensure_mux_selected(mux_value)
            bits = await self._read_coils_raw(address, 1)
            return bool(bits[0])

    async def async_write_schedule_register(
        self, mux_value: int, offset: int, value: int
    ) -> None:
        """Select `mux_value` and write a single register at BLOCK_START+offset."""
        async with self._lock:
            await self._ensure_mux_selected(mux_value)
            await self._write_register_raw(BLOCK_START + offset, value)

    async def async_write_schedule_registers(
        self, mux_value: int, offset: int, values: list[int]
    ) -> None:
        """Select `mux_value` and write consecutive registers at BLOCK_START+offset."""
        async with self._lock:
            await self._ensure_mux_selected(mux_value)
            await self._write_registers_raw(BLOCK_START + offset, values)

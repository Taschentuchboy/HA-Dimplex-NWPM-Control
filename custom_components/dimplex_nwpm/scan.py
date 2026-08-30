"""Scan a range of holding registers to find undocumented datapoints.

The Dimplex datapoint list documents only a subset of the low address range:
1, 2, 3, 5, 6, 7, 9-14, 19-21, 23 and a few more. The gaps in between are not
empty - they simply were not published. A sensor wired to a WPM input whose
function is not the documented one (a stove buffer probe on the "3. Heizkreis"
terminal, for example) can therefore still be readable, just at an address
that has to be found by trial.

Guessing from a single snapshot is unreliable, because many registers hold
plausible-looking numbers. So the scanner keeps the previous scan and reports
what *changed* between two runs. Warming a probe by hand for a minute and
scanning again pinpoints its address with no ambiguity.

The scan is strictly read-only and goes through the same lock as everything
else, so it cannot interleave with a schedule read/write sequence.
"""

from __future__ import annotations

import logging
from typing import Any

from .const import (
    SCAN_MAX_SPAN,
    SCAN_MUX_GUARD_END,
    SCAN_MUX_GUARD_START,
    SCAN_PLAUSIBLE_MAX_C,
    SCAN_PLAUSIBLE_MIN_C,
)
from .modbus_hub import DimplexModbusError, DimplexModbusHub

_LOGGER = logging.getLogger(__name__)


def _to_signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


class RegisterScanner:
    """Reads an address range and diffs it against the previous run."""

    def __init__(self, hub: DimplexModbusHub) -> None:
        self._hub = hub
        self._last: dict[int, int] = {}

    async def async_scan(self, start: int, end: int) -> dict[str, Any]:
        if end < start:
            raise ValueError("End address is before the start address")
        if end - start + 1 > SCAN_MAX_SPAN:
            raise ValueError(
                f"Range too large (max. {SCAN_MAX_SPAN} addresses per scan)"
            )
        if start <= SCAN_MUX_GUARD_END and end >= SCAN_MUX_GUARD_START:
            # Those registers only make sense relative to whichever time
            # function the multiplexer currently points at, so scanning them
            # produces values that look real but mean nothing.
            raise ValueError(
                f"Range {SCAN_MUX_GUARD_START}-{SCAN_MUX_GUARD_END} sits behind the "
                "time-function multiplexer and is not scanned"
            )

        values: dict[int, int] = {}
        for address in range(start, end + 1):
            try:
                raw = await self._hub.async_read_holding_registers(address, 1)
            except DimplexModbusError:
                # Not implemented on this device - expected for most gaps.
                continue
            values[address] = raw[0]

        changed: list[dict[str, Any]] = []
        for address, raw in values.items():
            previous = self._last.get(address)
            if previous is not None and previous != raw:
                changed.append(
                    {
                        "address": address,
                        "before": previous,
                        "now": raw,
                        "delta_divided_by_10": round(
                            (_to_signed16(raw) - _to_signed16(previous)) / 10, 1
                        ),
                        "as_temperature": round(_to_signed16(raw) / 10, 1),
                    }
                )
        changed.sort(
            key=lambda c: abs(c["delta_divided_by_10"]), reverse=True
        )

        plausible = {
            address: round(_to_signed16(raw) / 10, 1)
            for address, raw in values.items()
            if SCAN_PLAUSIBLE_MIN_C <= _to_signed16(raw) / 10 <= SCAN_PLAUSIBLE_MAX_C
            and raw != 0
        }

        had_previous = bool(self._last)
        self._last = values

        result: dict[str, Any] = {
            "range": f"{start}-{end}",
            "addresses_responding": len(values),
            "possible_temperatures": plausible,
            "raw_values": values,
        }
        if had_previous:
            result["changed_since_last_scan"] = changed
            result["note"] = (
                "Addresses with the largest delta come first. If you warmed a probe "
                "by hand, the topmost address with a plausible positive delta is "
                "the datapoint you are looking for."
            )
        else:
            result["note"] = (
                "First scan - nothing to compare against yet. Warm the probe by hand "
                "for a minute or two and call the service again to see what "
                "changed."
            )
        return result

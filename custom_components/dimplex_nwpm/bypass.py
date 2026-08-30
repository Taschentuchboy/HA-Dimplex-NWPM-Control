"""Temporarily suspend the hot-water lock, then restore the normal schedule.

The heat pump has no "lock off for N minutes" register. What it does have is
one weekday register per day (0=Ja, 1=Nein, 2=Zeit 1, 3=Zeit 2). So a
temporary bypass works like this:

  1. Read the *current* value of today's weekday register straight from the
     device (not from cache - the user may have changed the schedule on the
     heat pump's own display in the meantime).
  2. Remember that original value, then write 1 ("Nein") so the lock does
     not apply today.
  3. After the requested duration, write the original value back.

Two details matter for correctness:

  * The weekday register that gets restored is the one that was *modified*,
    not "today's". A bypass started at 23:30 finishes after midnight, when
    "today" is a different day - restoring the wrong register would corrupt
    two days of the schedule at once.
  * The pending restore is persisted to disk. If Home Assistant restarts
    during the bypass window the lock would otherwise stay disabled
    indefinitely. On startup an expired bypass is restored immediately and
    a still-running one is rescheduled for its remaining time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    SCHEDULE_FUNCTIONS_BY_KEY,
    STORAGE_VERSION,
    WEEKDAY_MODE_OFF,
    WEEKDAY_OFFSETS,
    WW_SPERRE_KEY,
)
from .modbus_hub import DimplexModbusError, DimplexModbusHub
from .schedule import ScheduleCoordinator, parse_block

_LOGGER = logging.getLogger(__name__)

# How long to wait before retrying a restore that failed (e.g. heat pump
# temporarily unreachable when the bypass expired).
_RESTORE_RETRY_INTERVAL = timedelta(minutes=5)

# key -> (label, register offset, python weekday index)
_BY_PY_WEEKDAY = {py: (key, offset) for key, _l, offset, py in WEEKDAY_OFFSETS}
_OFFSET_BY_KEY = {key: offset for key, _l, offset, _py in WEEKDAY_OFFSETS}


class HotWaterBypassManager:
    """Owns the state of the temporary hot-water-lock bypass."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        hub: DimplexModbusHub,
        schedule_coordinator: ScheduleCoordinator,
    ) -> None:
        self._hass = hass
        self._entry = entry
        self._hub = hub
        self._schedules = schedule_coordinator
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.bypass"
        )
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

        self._weekday_key: str | None = None
        self._original_value: int | None = None
        self._ends_at: datetime | None = None

    # -- observable state ------------------------------------------------

    @property
    def active(self) -> bool:
        return self._ends_at is not None

    @property
    def ends_at(self) -> datetime | None:
        return self._ends_at

    @property
    def weekday_key(self) -> str | None:
        return self._weekday_key

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(update_callback)

        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify(self) -> None:
        for update_callback in self._listeners:
            update_callback()

    # -- persistence -----------------------------------------------------

    async def _async_save(self) -> None:
        if self._ends_at is None:
            await self._store.async_remove()
            return
        await self._store.async_save(
            {
                "weekday_key": self._weekday_key,
                "original_value": self._original_value,
                "ends_at": self._ends_at.isoformat(),
            }
        )

    async def async_load(self) -> None:
        """Restore a bypass that was pending when Home Assistant stopped."""
        stored = await self._store.async_load()
        if not stored:
            return

        weekday_key = stored.get("weekday_key")
        original_value = stored.get("original_value")
        ends_at_raw = stored.get("ends_at")
        ends_at = dt_util.parse_datetime(ends_at_raw) if ends_at_raw else None

        if weekday_key not in _OFFSET_BY_KEY or original_value is None or ends_at is None:
            _LOGGER.warning("Stored bypass state is invalid and will be discarded")
            await self._store.async_remove()
            return

        self._weekday_key = weekday_key
        self._original_value = int(original_value)
        self._ends_at = ends_at

        if ends_at <= dt_util.utcnow():
            _LOGGER.info(
                "Hot water bypass expired while Home Assistant was down; restoring "
                "the schedule now"
            )
            await self._handle_expiry(dt_util.utcnow())
        else:
            _LOGGER.info(
                "Resumed a running hot water bypass, ends at %s", ends_at
            )
            self._schedule_restore()
            self._notify()

    # -- timer -----------------------------------------------------------

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _schedule_restore(self) -> None:
        self._cancel_timer()
        if self._ends_at is None:
            return
        self._unsub_timer = async_track_point_in_utc_time(
            self._hass, self._handle_expiry, self._ends_at
        )

    async def _handle_expiry(self, _now: datetime) -> None:
        self._unsub_timer = None
        _LOGGER.info("Hot water bypass expired, restoring the schedule")
        try:
            await self.async_cancel()
        except HomeAssistantError:
            # Never let a timer callback raise into the event loop, and never
            # give up on the restore: leaving the lock disabled indefinitely
            # is the one outcome we must avoid. Retry shortly.
            self._ends_at = dt_util.utcnow() + _RESTORE_RETRY_INTERVAL
            self._schedule_restore()
            await self._async_save()
            _LOGGER.warning(
                "Restore failed, retrying in %s",
                _RESTORE_RETRY_INTERVAL,
            )
            self._notify()

    # -- actions ---------------------------------------------------------

    async def async_start(self, duration_minutes: int) -> None:
        """Suspend the hot-water lock for `duration_minutes`."""
        func = SCHEDULE_FUNCTIONS_BY_KEY[WW_SPERRE_KEY]

        if not self.active:
            # Read the live block so the value we memorise is the real one,
            # even if the schedule was edited on the heat pump's display.
            try:
                raw = await self._hub.async_read_schedule_block(
                    func.mux_value, func.block_length
                )
            except DimplexModbusError as err:
                raise HomeAssistantError(
                    f"Could not read the hot water block schedule: {err}"
                ) from err

            parsed = parse_block(func, raw)
            weekday_key, offset = _BY_PY_WEEKDAY[dt_util.now().weekday()]
            original = parsed.get(weekday_key)

            self._weekday_key = weekday_key
            self._original_value = int(original) if original is not None else 0
            _LOGGER.info(
                "Suspending the hot water block for %s minutes "
                "(weekday %s, original value %s)",
                duration_minutes,
                weekday_key,
                self._original_value,
            )
        else:
            # Already running: keep the memorised original, only extend the
            # runtime and re-apply the register below.
            weekday_key = self._weekday_key
            offset = _OFFSET_BY_KEY[weekday_key]
            _LOGGER.info(
                "A hot water bypass is already running; extending it to %s minutes",
                duration_minutes,
            )

        # Write unconditionally rather than only on the first call. This makes
        # the operation idempotent and self-healing: if the weekday register
        # drifted (edited on the heat pump display, or via the weekday select
        # entity) while a bypass was running, re-triggering restores the
        # intended "lock suspended" state instead of silently doing nothing.
        try:
            await self._hub.async_write_schedule_register(
                func.mux_value, offset, WEEKDAY_MODE_OFF
            )
        except DimplexModbusError as err:
            raise HomeAssistantError(
                f"Could not suspend the hot water block: {err}"
            ) from err
        self._schedules.set_cached(func.key, weekday_key, WEEKDAY_MODE_OFF)

        self._ends_at = dt_util.utcnow() + timedelta(minutes=duration_minutes)
        self._schedule_restore()
        await self._async_save()
        self._notify()

    async def async_cancel(self) -> None:
        """Restore the original weekday value and end the bypass."""
        self._cancel_timer()

        if self._weekday_key is not None and self._original_value is not None:
            func = SCHEDULE_FUNCTIONS_BY_KEY[WW_SPERRE_KEY]
            offset = _OFFSET_BY_KEY[self._weekday_key]
            try:
                await self._hub.async_write_schedule_register(
                    func.mux_value, offset, self._original_value
                )
            except DimplexModbusError as err:
                # Keep the stored state so the next startup retries the
                # restore rather than silently leaving the lock disabled.
                _LOGGER.error(
                    "Failed to restore the hot water block: %s", err
                )
                raise HomeAssistantError(
                    f"Could not restore the hot water block: {err}"
                ) from err

            self._schedules.set_cached(func.key, self._weekday_key, self._original_value)
            _LOGGER.info(
                "Hot water block restored (weekday %s, value %s)",
                self._weekday_key,
                self._original_value,
            )

        self._weekday_key = None
        self._original_value = None
        self._ends_at = None
        await self._async_save()
        self._notify()

    @callback
    def async_shutdown(self) -> None:
        """Stop the timer on unload; the stored state survives for restart."""
        self._cancel_timer()

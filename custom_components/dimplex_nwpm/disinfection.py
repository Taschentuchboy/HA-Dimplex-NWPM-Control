"""Trigger a thermal disinfection run now, then restore the normal schedule.

There is no "start now" register. What the heat pump offers is a weekly
schedule with one start time, so a manual run works by rewriting that
schedule to fire in a couple of minutes:

  1. Read the live thermal-disinfection block and memorise the original
     start time plus the weekday value of the day the run will fall on.
  2. Set that weekday to 0 ("Ja") and the start time to now + a short lead.
  3. Poll status coil 125 ("Aktiv") until the heat pump reports it has
     started and then finished - the heat pump decides the duration, there
     is no settable end time.
  4. Restore the original start time and weekday value.

The failure modes all point the same way - a schedule left permanently
rewritten - so the design is deliberately defensive:

  * The restore is driven by a **hard deadline**, not by the coil. The coil
    only ever makes the restore happen *earlier*. If coil reads fail
    entirely (wrong address, unsupported function code, device quirk) the
    run still ends cleanly at DISINFECTION_MAX_MINUTES.
  * If the coil never reports "aktiv" within DISINFECTION_START_TIMEOUT_
    MINUTES of the scheduled start, the heat pump evidently declined to run
    (tank already hot enough, for instance) and the schedule is restored.
  * The schedule is never restored *while* the coil reports a run in
    progress - writing "Nein" to the weekday mid-run could abort a
    disinfection cycle half way through, which would defeat its purpose.
  * State is persisted, so a restart during the 1-2 h run resumes
    monitoring instead of abandoning the modified schedule.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    COIL_DISINFECTION_ACTIVE,
    DISINFECTION_LEAD_MINUTES,
    DISINFECTION_MAX_MINUTES,
    DISINFECTION_POLL_SECONDS,
    DISINFECTION_START_TIMEOUT_MINUTES,
    DOMAIN,
    EVENT_DISINFECTION_FINISHED,
    EVENT_DISINFECTION_RUNNING,
    EVENT_DISINFECTION_TRIGGERED,
    OFF_START_HOUR_1,
    SCHEDULE_FUNCTIONS_BY_KEY,
    STORAGE_VERSION,
    THERMAL_DISINFECTION_KEY,
    WEEKDAY_MODE_ON,
    WEEKDAY_OFFSETS,
)
from .modbus_hub import DimplexModbusError, DimplexModbusHub
from .schedule import ScheduleCoordinator, parse_block

_LOGGER = logging.getLogger(__name__)

_BY_PY_WEEKDAY = {py: (key, offset) for key, _l, offset, py in WEEKDAY_OFFSETS}
_OFFSET_BY_KEY = {key: offset for key, _l, offset, _py in WEEKDAY_OFFSETS}

PHASE_IDLE = "idle"
PHASE_PENDING = "pending"  # schedule rewritten, waiting for the pump to start
PHASE_RUNNING = "running"  # coil reports a run in progress


class ThermalDisinfectionManager:
    """Owns the state of a manually triggered disinfection run."""

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
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.disinfection"
        )
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._listeners: list[Callable[[], None]] = []

        self._phase: str = PHASE_IDLE
        self._weekday_key: str | None = None
        self._original_weekday: int | None = None
        self._original_time: dt_time | None = None
        self._scheduled_for: datetime | None = None
        self._deadline: datetime | None = None
        self._started_at: datetime | None = None
        self._coil_ok: bool = True

    # -- observable state ------------------------------------------------

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def active(self) -> bool:
        return self._phase != PHASE_IDLE

    @property
    def attributes(self) -> dict[str, Any]:
        if not self.active:
            return {}
        attrs: dict[str, Any] = {"phase": self._phase}
        if self._scheduled_for:
            attrs["scheduled_for"] = self._scheduled_for.isoformat()
        if self._started_at:
            attrs["started_at"] = self._started_at.isoformat()
        if self._deadline:
            attrs["deadline"] = self._deadline.isoformat()
        if self._original_time:
            attrs["restores_start_time"] = self._original_time.strftime("%H:%M")
        attrs["status_coil_readable"] = self._coil_ok
        return attrs

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
        if not self.active:
            await self._store.async_remove()
            return
        await self._store.async_save(
            {
                "phase": self._phase,
                "weekday_key": self._weekday_key,
                "original_weekday": self._original_weekday,
                "original_time": self._original_time.strftime("%H:%M")
                if self._original_time
                else None,
                "scheduled_for": self._scheduled_for.isoformat()
                if self._scheduled_for
                else None,
                "deadline": self._deadline.isoformat() if self._deadline else None,
                "started_at": self._started_at.isoformat() if self._started_at else None,
            }
        )

    async def async_load(self) -> None:
        """Resume monitoring a run that was in progress before a restart."""
        stored = await self._store.async_load()
        if not stored:
            return

        try:
            self._phase = stored["phase"]
            self._weekday_key = stored["weekday_key"]
            self._original_weekday = int(stored["original_weekday"])
            hh, mm = stored["original_time"].split(":")
            self._original_time = dt_time(int(hh), int(mm))
            self._scheduled_for = dt_util.parse_datetime(stored["scheduled_for"])
            self._deadline = dt_util.parse_datetime(stored["deadline"])
            started = stored.get("started_at")
            self._started_at = dt_util.parse_datetime(started) if started else None
        except (KeyError, TypeError, ValueError, AttributeError):
            _LOGGER.warning(
                "Stored disinfection state is invalid and will be discarded"
            )
            await self._store.async_remove()
            self._reset_state()
            return

        if self._weekday_key not in _OFFSET_BY_KEY or self._deadline is None:
            await self._store.async_remove()
            self._reset_state()
            return

        _LOGGER.info(
            "Resumed a running thermal disinfection (phase %s, schedule will be "
            "restored by %s at the latest)",
            self._phase,
            self._deadline,
        )
        await self._async_poll(dt_util.utcnow())

    # -- timer -----------------------------------------------------------

    @callback
    def _cancel_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    @callback
    def _schedule_next_poll(self) -> None:
        self._cancel_timer()
        if not self.active or self._deadline is None:
            return
        # Never poll past the hard deadline: land exactly on it instead, so
        # the restore happens on time even if the coil is unreadable.
        next_poll = dt_util.utcnow() + timedelta(seconds=DISINFECTION_POLL_SECONDS)
        if next_poll > self._deadline:
            next_poll = self._deadline
        self._unsub_timer = async_track_point_in_utc_time(
            self._hass, self._async_poll, next_poll
        )

    @callback
    def _reset_state(self) -> None:
        self._phase = PHASE_IDLE
        self._weekday_key = None
        self._original_weekday = None
        self._original_time = None
        self._scheduled_for = None
        self._deadline = None
        self._started_at = None

    # -- main state machine ----------------------------------------------

    async def _async_poll(self, _now: datetime) -> None:
        self._unsub_timer = None
        if not self.active:
            return

        func = SCHEDULE_FUNCTIONS_BY_KEY[THERMAL_DISINFECTION_KEY]
        now = dt_util.utcnow()

        coil: bool | None
        try:
            coil = await self._hub.async_read_schedule_coil(
                func.mux_value, COIL_DISINFECTION_ACTIVE
            )
            self._coil_ok = True
        except DimplexModbusError as err:
            # Best effort only - the hard deadline still governs the restore.
            coil = None
            if self._coil_ok:
                _LOGGER.warning(
                    "Thermal disinfection status coil %s is not readable (%s). The "
                    "schedule will be restored on a timer after at most %s "
                    "minutes instead.",
                    COIL_DISINFECTION_ACTIVE,
                    err,
                    DISINFECTION_MAX_MINUTES,
                )
            self._coil_ok = False

        if self._phase == PHASE_PENDING:
            if coil is True:
                self._phase = PHASE_RUNNING
                self._started_at = now
                _LOGGER.info("Thermal disinfection is now running")
                self._hass.bus.async_fire(
                    EVENT_DISINFECTION_RUNNING,
                    {"entry_id": self._entry.entry_id},
                )
                await self._async_save()
                self._notify()
            elif (
                self._scheduled_for is not None
                and now
                > self._scheduled_for
                + timedelta(minutes=DISINFECTION_START_TIMEOUT_MINUTES)
            ):
                await self._async_finish("not_started")
                return

        elif self._phase == PHASE_RUNNING:
            if coil is False:
                await self._async_finish("completed")
                return

        if now >= (self._deadline or now):
            # Hard cap reached. If the coil still says "running" the heat
            # pump is taking longer than expected; restore anyway, since a
            # permanently rewritten schedule is the worse outcome.
            await self._async_finish("timeout")
            return

        self._schedule_next_poll()

    # -- actions ---------------------------------------------------------

    async def async_start(self) -> None:
        """Rewrite the schedule so a disinfection run starts in a minute."""
        if self.active:
            raise HomeAssistantError(
                "A manually started thermal disinfection is already in progress"
            )

        func = SCHEDULE_FUNCTIONS_BY_KEY[THERMAL_DISINFECTION_KEY]

        try:
            raw = await self._hub.async_read_schedule_block(
                func.mux_value, func.block_length
            )
        except DimplexModbusError as err:
            raise HomeAssistantError(
                f"Could not read the thermal disinfection schedule: {err}"
            ) from err

        parsed = parse_block(func, raw)
        original_time = parsed.get("start") or dt_time(0, 0)

        # Work in local time: the start-time registers hold the heat pump's
        # own wall-clock time.
        target_local = dt_util.now() + timedelta(minutes=DISINFECTION_LEAD_MINUTES)
        # Pick the weekday the run will actually fall on - a run triggered at
        # 23:59 starts tomorrow, and enabling today's register would do
        # nothing while leaving the wrong day modified.
        weekday_key, offset = _BY_PY_WEEKDAY[target_local.weekday()]
        original_weekday = parsed.get(weekday_key)

        self._weekday_key = weekday_key
        self._original_weekday = (
            int(original_weekday) if original_weekday is not None else 1
        )
        self._original_time = original_time
        self._scheduled_for = dt_util.utcnow() + timedelta(
            minutes=DISINFECTION_LEAD_MINUTES
        )
        self._deadline = self._scheduled_for + timedelta(minutes=DISINFECTION_MAX_MINUTES)
        self._started_at = None
        self._phase = PHASE_PENDING

        new_time = dt_time(target_local.hour, target_local.minute)
        try:
            await self._hub.async_write_schedule_registers(
                func.mux_value, OFF_START_HOUR_1, [new_time.hour, new_time.minute]
            )
            await self._hub.async_write_schedule_register(
                func.mux_value, offset, WEEKDAY_MODE_ON
            )
        except DimplexModbusError as err:
            # Undo whatever landed, so a half-applied trigger cannot leave the
            # schedule modified with nothing monitoring it.
            self._reset_state()
            await self._async_restore(func, weekday_key, self._original_weekday, original_time)
            raise HomeAssistantError(
                f"Could not start the thermal disinfection: {err}"
            ) from err

        self._schedules.set_cached(func.key, "start", new_time)
        self._schedules.set_cached(func.key, weekday_key, WEEKDAY_MODE_ON)

        _LOGGER.info(
            "Thermal disinfection scheduled for %s (weekday %s). Original start "
            "time %s and weekday value %s will be restored after the run.",
            new_time.strftime("%H:%M"),
            weekday_key,
            original_time.strftime("%H:%M"),
            self._original_weekday,
        )
        self._hass.bus.async_fire(
            EVENT_DISINFECTION_TRIGGERED,
            {
                "entry_id": self._entry.entry_id,
                "scheduled_for": new_time.strftime("%H:%M"),
                "weekday": weekday_key,
            },
        )

        await self._async_save()
        self._schedule_next_poll()
        self._notify()

    async def _async_restore(
        self,
        func,
        weekday_key: str | None,
        original_weekday: int | None,
        original_time: dt_time | None,
    ) -> None:
        """Best-effort write-back of the original schedule values."""
        if weekday_key is None:
            return
        try:
            if original_time is not None:
                await self._hub.async_write_schedule_registers(
                    func.mux_value,
                    OFF_START_HOUR_1,
                    [original_time.hour, original_time.minute],
                )
                self._schedules.set_cached(func.key, "start", original_time)
            if original_weekday is not None:
                await self._hub.async_write_schedule_register(
                    func.mux_value, _OFFSET_BY_KEY[weekday_key], original_weekday
                )
                self._schedules.set_cached(func.key, weekday_key, original_weekday)
        except DimplexModbusError as err:
            raise HomeAssistantError(
                f"Zeitplan der Thermischen Desinfektion konnte nicht "
                f"wiederhergestellt werden: {err}"
            ) from err

    async def _async_finish(self, reason: str) -> None:
        """Restore the schedule and end the run."""
        func = SCHEDULE_FUNCTIONS_BY_KEY[THERMAL_DISINFECTION_KEY]
        weekday_key = self._weekday_key
        original_weekday = self._original_weekday
        original_time = self._original_time
        started_at = self._started_at

        try:
            await self._async_restore(func, weekday_key, original_weekday, original_time)
        except HomeAssistantError as err:
            # Keep the state and retry rather than abandoning a modified
            # schedule; the next poll lands on the (already passed) deadline.
            _LOGGER.error("%s - erneuter Versuch in %s s", err, DISINFECTION_POLL_SECONDS)
            self._deadline = dt_util.utcnow() + timedelta(
                seconds=DISINFECTION_POLL_SECONDS
            )
            await self._async_save()
            self._schedule_next_poll()
            self._notify()
            return

        duration = None
        if started_at is not None:
            duration = round((dt_util.utcnow() - started_at).total_seconds() / 60)

        _LOGGER.info(
            "Thermal disinfection finished (%s). Restored start time %s and "
            "weekday value %s.",
            reason,
            original_time.strftime("%H:%M") if original_time else "?",
            original_weekday,
        )

        self._cancel_timer()
        self._reset_state()
        await self._async_save()

        self._hass.bus.async_fire(
            EVENT_DISINFECTION_FINISHED,
            {
                "entry_id": self._entry.entry_id,
                "reason": reason,
                "duration_minutes": duration,
            },
        )
        self._notify()

    async def async_cancel(self) -> None:
        """Abort a manual run early and restore the schedule immediately."""
        if not self.active:
            return
        await self._async_finish("cancelled")

    @callback
    def async_shutdown(self) -> None:
        """Stop the poll timer on unload; stored state survives a restart."""
        self._cancel_timer()

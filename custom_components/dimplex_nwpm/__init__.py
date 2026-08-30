"""The Dimplex NWPM (Modbus TCP) integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .bypass import HotWaterBypassManager
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DURATION,
    ATTR_END,
    ATTR_START,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    DEFAULT_BYPASS_MINUTES,
    DOMAIN,
    SCAN_DEFAULT_END,
    SCAN_DEFAULT_START,
    SERVICE_BYPASS_HOT_WATER_LOCK,
    SERVICE_CANCEL_HOT_WATER_BYPASS,
    SERVICE_CANCEL_THERMAL_DISINFECTION,
    SERVICE_SCAN_REGISTERS,
    SERVICE_START_THERMAL_DISINFECTION,
)
from .disinfection import ThermalDisinfectionManager
from .coordinator import DimplexDataCoordinator
from .modbus_hub import DimplexModbusError, DimplexModbusHub
from .scan import RegisterScanner
from .schedule import ScheduleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
    Platform.BUTTON,
]

BYPASS_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DURATION, default=DEFAULT_BYPASS_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=1440)
        ),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)

ENTRY_ONLY_SERVICE_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}
)

SCAN_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_START, default=SCAN_DEFAULT_START): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Optional(ATTR_END, default=SCAN_DEFAULT_END): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=65535)
        ),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


@dataclass
class DimplexRuntimeData:
    hub: DimplexModbusHub
    data_coordinator: DimplexDataCoordinator
    schedule_coordinator: ScheduleCoordinator
    bypass: HotWaterBypassManager
    disinfection: ThermalDisinfectionManager
    scanner: RegisterScanner


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = DimplexModbusHub(
        entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SLAVE]
    )
    try:
        await hub.async_connect()
    except DimplexModbusError as err:
        raise ConfigEntryNotReady(str(err)) from err

    data_coordinator = DimplexDataCoordinator(hass, hub, entry.data[CONF_SCAN_INTERVAL])
    await data_coordinator.async_config_entry_first_refresh()

    schedule_coordinator = ScheduleCoordinator(hass, hub)
    await schedule_coordinator.async_refresh()

    bypass = HotWaterBypassManager(hass, entry, hub, schedule_coordinator)
    # Picks up (and, if needed, immediately finishes) a bypass that was still
    # running when Home Assistant was last stopped.
    await bypass.async_load()

    disinfection = ThermalDisinfectionManager(hass, entry, hub, schedule_coordinator)
    # Likewise resumes supervising a manual disinfection run across restarts.
    await disinfection.async_load()

    entry.runtime_data = DimplexRuntimeData(
        hub=hub,
        data_coordinator=data_coordinator,
        schedule_coordinator=schedule_coordinator,
        bypass=bypass,
        disinfection=disinfection,
        scanner=RegisterScanner(hub),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry.runtime_data.bypass.async_shutdown()
        entry.runtime_data.disinfection.async_shutdown()
        await entry.runtime_data.hub.async_close()

    if not hass.config_entries.async_loaded_entries(DOMAIN):
        for service in (
            SERVICE_BYPASS_HOT_WATER_LOCK,
            SERVICE_CANCEL_HOT_WATER_BYPASS,
            SERVICE_START_THERMAL_DISINFECTION,
            SERVICE_CANCEL_THERMAL_DISINFECTION,
        ):
            hass.services.async_remove(DOMAIN, service)

    return unload_ok


def _target_entries(hass: HomeAssistant, call: ServiceCall) -> list[ConfigEntry]:
    """Resolve which config entries a service call applies to."""
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if entry_id is None:
        if not entries:
            raise HomeAssistantError("No Dimplex heat pump configured")
        return entries

    matching = [e for e in entries if e.entry_id == entry_id]
    if not matching:
        raise HomeAssistantError(f"Unknown config entry: {entry_id}")
    return matching


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_BYPASS_HOT_WATER_LOCK):
        return

    async def _handle_bypass(call: ServiceCall) -> None:
        duration = call.data[ATTR_DURATION]
        for entry in _target_entries(hass, call):
            await entry.runtime_data.bypass.async_start(duration)

    async def _handle_cancel(call: ServiceCall) -> None:
        for entry in _target_entries(hass, call):
            await entry.runtime_data.bypass.async_cancel()

    hass.services.async_register(
        DOMAIN, SERVICE_BYPASS_HOT_WATER_LOCK, _handle_bypass, BYPASS_SERVICE_SCHEMA
    )
    async def _handle_start_disinfection(call: ServiceCall) -> None:
        for entry in _target_entries(hass, call):
            await entry.runtime_data.disinfection.async_start()

    async def _handle_cancel_disinfection(call: ServiceCall) -> None:
        for entry in _target_entries(hass, call):
            await entry.runtime_data.disinfection.async_cancel()

    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_HOT_WATER_BYPASS,
        _handle_cancel,
        ENTRY_ONLY_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_START_THERMAL_DISINFECTION,
        _handle_start_disinfection,
        ENTRY_ONLY_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CANCEL_THERMAL_DISINFECTION,
        _handle_cancel_disinfection,
        ENTRY_ONLY_SERVICE_SCHEMA,
    )

    async def _handle_scan(call: ServiceCall) -> ServiceResponse:
        entries = _target_entries(hass, call)
        if len(entries) > 1:
            raise HomeAssistantError(
                "A single heat pump must be specified for a register scan"
            )
        try:
            return await entries[0].runtime_data.scanner.async_scan(
                call.data[ATTR_START], call.data[ATTR_END]
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SCAN_REGISTERS,
        _handle_scan,
        SCAN_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

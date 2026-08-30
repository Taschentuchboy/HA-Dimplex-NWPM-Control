"""Shared helpers for building entity metadata."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.helpers.entity import DeviceInfo

from .const import DEVICE_NAME, DOMAIN, MANUFACTURER, MODEL


def build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """One device per config entry, with a fixed, slug-friendly name."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=DEVICE_NAME,
        manufacturer=MANUFACTURER,
        model=MODEL,
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )

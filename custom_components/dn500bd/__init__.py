"""Denon DN-500BD MKII Blu-ray player integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import DN500BDClient, DN500BDError
from .const import (
    CONF_ACK_NOTIFICATIONS,
    CONF_ACK_TIMEOUT,
    CONF_ANSWER_TIMEOUT,
    CONF_REVERSE_CODE,
    DEFAULT_ACK_NOTIFICATIONS,
    DEFAULT_ACK_TIMEOUT,
    DEFAULT_ANSWER_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_REVERSE_CODE,
)
from .coordinator import DN500BDCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER, Platform.SENSOR, Platform.BUTTON]


@dataclass
class DN500BDRuntime:
    """Runtime data stored on the config entry."""

    client: DN500BDClient
    coordinator: DN500BDCoordinator


type DN500BDConfigEntry = ConfigEntry[DN500BDRuntime]


def build_client(host: str, port: int, options: dict) -> DN500BDClient:
    """Create a client from entry data/options."""
    return DN500BDClient(
        host,
        port,
        ack_timeout=float(options.get(CONF_ACK_TIMEOUT, DEFAULT_ACK_TIMEOUT)) / 1000,
        answer_timeout=float(options.get(CONF_ANSWER_TIMEOUT, DEFAULT_ANSWER_TIMEOUT)) / 1000,
        ack_notifications=bool(options.get(CONF_ACK_NOTIFICATIONS, DEFAULT_ACK_NOTIFICATIONS)),
        reverse_code=str(options.get(CONF_REVERSE_CODE, DEFAULT_REVERSE_CODE)),
    )


async def async_setup_entry(hass: HomeAssistant, entry: DN500BDConfigEntry) -> bool:
    """Set up a DN-500BD from a config entry."""
    client = build_client(
        entry.data[CONF_HOST], entry.data.get(CONF_PORT, DEFAULT_PORT), dict(entry.options)
    )
    try:
        await client.connect()
    except DN500BDError as err:
        raise ConfigEntryNotReady(str(err)) from err

    # Device info (firmware/model) once; tolerate failures (device may be off)
    try:
        if await client.query_power():
            await client.poll(["version", "mac"])
    except DN500BDError as err:
        _LOGGER.debug("Could not read device info: %s", err)

    coordinator = DN500BDCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise

    entry.runtime_data = DN500BDRuntime(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: DN500BDConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: DN500BDConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.client.close()
    return unloaded

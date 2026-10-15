"""Polling coordinator for the DN-500BD MKII."""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    PLAYING_TRANSPORTS,
    DN500BDClient,
    DN500BDConnectionError,
    DN500BDError,
    DN500BDState,
)
from .const import (
    CONF_POLL_IDLE,
    CONF_POLL_PLAYING,
    DEFAULT_POLL_IDLE,
    DEFAULT_POLL_PLAYING,
    DOMAIN,
    MEDIA_INFO_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

FAST_KEYS = ["transport", "elapsed", "remaining", "chapter"]
DISC_KEYS = ["chapters_total", "title", "titles_total"]
MEDIA_KEYS = ["media_type", "audio_format", "audio_channels"]


class DN500BDCoordinator(DataUpdateCoordinator[DN500BDState]):
    """Polls the player; push notifications are merged in between polls."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: DN500BDClient
    ) -> None:
        """Init."""
        self.client = client
        self.poll_playing = float(entry.options.get(CONF_POLL_PLAYING, DEFAULT_POLL_PLAYING))
        self.poll_idle = float(entry.options.get(CONF_POLL_IDLE, DEFAULT_POLL_IDLE))
        self._last_media_poll = 0.0
        self._last_title: int | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=timedelta(seconds=self.poll_idle),
        )
        client.on_packet = self._on_packet

    @callback
    def _on_packet(self, body: str, unsolicited: bool) -> None:
        """Merge unsolicited notifications immediately."""
        if unsolicited and self.data is not None:
            self.data = self.client.state.copy()
            self.async_update_listeners()

    async def _async_update_data(self) -> DN500BDState:
        client = self.client
        try:
            await client.connect()
            if not await client.query_power():
                client.state.clear_media()
                client.state.disc = None
                self.update_interval = timedelta(seconds=self.poll_idle)
                return client.state.copy()

            await client.poll(["disc"])
            if client.state.disc in ("no_disc", "tray_open", "unformatted", "tray_error"):
                client.state.clear_media()
                self.update_interval = timedelta(seconds=self.poll_idle)
                return client.state.copy()

            await client.poll(FAST_KEYS + DISC_KEYS)

            now = time.monotonic()
            if (
                now - self._last_media_poll > MEDIA_INFO_INTERVAL
                or client.state.title != self._last_title
            ):
                await client.poll(MEDIA_KEYS)
                self._last_media_poll = now
                self._last_title = client.state.title
        except DN500BDConnectionError as err:
            raise UpdateFailed(f"DN-500BD {client.host} not reachable: {err}") from err
        except DN500BDError as err:
            raise UpdateFailed(f"DN-500BD {client.host}: {err}") from err

        playing = client.state.transport in PLAYING_TRANSPORTS
        self.update_interval = timedelta(
            seconds=self.poll_playing if playing else self.poll_idle
        )
        return client.state.copy()

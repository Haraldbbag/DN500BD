"""Media player entity for the DN-500BD MKII."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DN500BDConfigEntry
from .client import PAUSED_TRANSPORTS, PLAYING_TRANSPORTS, REMOTE_KEYS, DN500BDError
from .const import (
    SERVICE_GOTO_CHAPTER,
    SERVICE_GOTO_TITLE,
    SERVICE_SCAN,
    SERVICE_SEND_KEY,
    SERVICE_SEND_RAW,
    SERVICE_TRAY,
)
from .entity import DN500BDEntity

SUPPORTED = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DN500BDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the media player."""
    async_add_entities([DN500BDMediaPlayer(entry.runtime_data.coordinator, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_GOTO_CHAPTER,
        {vol.Required("chapter"): vol.All(vol.Coerce(int), vol.Range(min=1, max=2000))},
        "async_goto_chapter",
    )
    platform.async_register_entity_service(
        SERVICE_GOTO_TITLE,
        {vol.Required("title"): vol.All(vol.Coerce(int), vol.Range(min=1, max=2000))},
        "async_goto_title",
    )
    platform.async_register_entity_service(
        SERVICE_SCAN,
        {
            vol.Required("direction"): vol.In(["forward", "reverse"]),
            vol.Optional("speed", default="fast"): vol.In(["fast", "slow"]),
        },
        "async_scan",
    )
    platform.async_register_entity_service(
        SERVICE_TRAY,
        {vol.Required("action"): vol.In(["open", "close"])},
        "async_tray",
    )
    platform.async_register_entity_service(
        SERVICE_SEND_KEY,
        {vol.Required("key"): vol.In(list(REMOTE_KEYS))},
        "async_send_key",
    )
    platform.async_register_entity_service(
        SERVICE_SEND_RAW,
        {
            vol.Required("command"): cv.string,
            vol.Optional("window", default=0.6): vol.All(
                vol.Coerce(float), vol.Range(min=0.05, max=5)
            ),
        },
        "async_send_raw",
        supports_response=SupportsResponse.ONLY,
    )


class DN500BDMediaPlayer(DN500BDEntity, MediaPlayerEntity):
    """DN-500BD MKII as media player."""

    _attr_name = None
    _attr_supported_features = SUPPORTED
    _attr_media_content_type = MediaType.VIDEO
    _unrecorded_attributes = frozenset({"elapsed", "remaining"})

    def __init__(self, coordinator, entry) -> None:
        """Init."""
        super().__init__(coordinator, entry, "player")

    # ---------------------------------------------------------------- state

    @property
    def state(self) -> MediaPlayerState | None:
        """Player state."""
        data = self.coordinator.data
        if data is None or data.power is None:
            return None
        if not data.power:
            return MediaPlayerState.OFF
        if data.transport in PLAYING_TRANSPORTS:
            return MediaPlayerState.PLAYING
        if data.transport in PAUSED_TRANSPORTS:
            return MediaPlayerState.PAUSED
        return MediaPlayerState.IDLE

    @property
    def media_position(self) -> int | None:
        """Elapsed seconds."""
        return self.coordinator.data.elapsed if self.coordinator.data else None

    @property
    def media_duration(self) -> int | None:
        """Elapsed + remaining."""
        return self.coordinator.data.duration if self.coordinator.data else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When elapsed was read."""
        data = self.coordinator.data
        if data is None or data.elapsed_at is None:
            return None
        return dt_util.utc_from_timestamp(data.elapsed_at)

    @property
    def media_track(self) -> int | None:
        """Current chapter."""
        return self.coordinator.data.chapter if self.coordinator.data else None

    @property
    def media_title(self) -> str | None:
        """Readable 'Title x · Chapter y/z'."""
        data = self.coordinator.data
        if data is None or (data.title is None and data.chapter is None):
            return None
        parts = []
        if data.title is not None:
            total = f"/{data.titles_total}" if data.titles_total else ""
            parts.append(f"Titel {data.title}{total}")
        if data.chapter is not None:
            total = f"/{data.chapters_total}" if data.chapters_total else ""
            parts.append(f"Kapitel {data.chapter}{total}")
        return " · ".join(parts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extra info."""
        d = self.coordinator.data
        if d is None:
            return {}
        return {
            "chapter": d.chapter,
            "chapters_total": d.chapters_total,
            "title": d.title,
            "titles_total": d.titles_total,
            "transport": d.transport,
            "disc": d.disc,
            "disc_type": d.media_type,
            "audio_format": d.audio_format,
            "audio_channels": d.audio_channels,
        }

    # -------------------------------------------------------------- actions

    async def _run(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except (DN500BDError, ValueError) as err:
            raise HomeAssistantError(f"DN-500BD: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        """Power on."""
        await self._run(self.client.power_on())

    async def async_turn_off(self) -> None:
        """Power off."""
        await self._run(self.client.power_off())

    async def async_media_play(self) -> None:
        """Play."""
        await self._run(self.client.play())

    async def async_media_pause(self) -> None:
        """Pause."""
        await self._run(self.client.pause())

    async def async_media_stop(self) -> None:
        """Stop."""
        await self._run(self.client.stop())

    async def async_media_next_track(self) -> None:
        """Next chapter."""
        await self._run(self.client.next_chapter())

    async def async_media_previous_track(self) -> None:
        """Previous chapter."""
        await self._run(self.client.previous_chapter())

    async def async_goto_chapter(self, chapter: int) -> None:
        """Jump to chapter."""
        await self._run(self.client.goto_chapter(chapter))

    async def async_goto_title(self, title: int) -> None:
        """Jump to title."""
        await self._run(self.client.goto_title(title))

    async def async_scan(self, direction: str, speed: str = "fast") -> None:
        """Search forward/reverse."""
        await self._run(self.client.scan(direction, speed))

    async def async_tray(self, action: str) -> None:
        """Open/close tray."""
        await self._run(self.client.tray(action == "open"))

    async def async_send_key(self, key: str) -> None:
        """Send a remote key for menu navigation."""
        await self._run(self.client.send_key(key))

    async def async_send_raw(self, command: str, window: float = 0.6) -> ServiceResponse:
        """Send any command and return what came back (for testing)."""
        try:
            result = await self.client.raw(command, window)
        except (DN500BDError, ValueError) as err:
            raise HomeAssistantError(f"DN-500BD: {err}") from err
        await self.coordinator.async_request_refresh()
        return result

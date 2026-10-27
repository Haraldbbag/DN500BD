"""Buttons for the DN-500BD MKII (scan, tray, title skip, menu keys)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DN500BDConfigEntry
from .client import DN500BDClient, DN500BDError
from .entity import DN500BDEntity


@dataclass(frozen=True, kw_only=True)
class DN500BDButtonDescription(ButtonEntityDescription):
    """Button description with action."""

    press_fn: Callable[[DN500BDClient], Awaitable[None]]


BUTTONS: tuple[DN500BDButtonDescription, ...] = (
    DN500BDButtonDescription(
        key="scan_forward",
        translation_key="scan_forward",
        icon="mdi:fast-forward",
        press_fn=lambda c: c.scan("forward", "fast"),
    ),
    DN500BDButtonDescription(
        key="scan_reverse",
        translation_key="scan_reverse",
        icon="mdi:rewind",
        press_fn=lambda c: c.scan("reverse", "fast"),
    ),
    DN500BDButtonDescription(
        key="next_title",
        translation_key="next_title",
        icon="mdi:skip-next-circle-outline",
        press_fn=lambda c: c.next_title(),
    ),
    DN500BDButtonDescription(
        key="previous_title",
        translation_key="previous_title",
        icon="mdi:skip-previous-circle-outline",
        press_fn=lambda c: c.previous_title(),
    ),
    DN500BDButtonDescription(
        key="tray_open",
        translation_key="tray_open",
        icon="mdi:eject",
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.tray(True),
    ),
    DN500BDButtonDescription(
        key="tray_close",
        translation_key="tray_close",
        icon="mdi:eject-outline",
        entity_registry_enabled_default=False,
        press_fn=lambda c: c.tray(False),
    ),
    DN500BDButtonDescription(
        key="key_up",
        translation_key="key_up",
        icon="mdi:chevron-up",
        press_fn=lambda c: c.send_key("up"),
    ),
    DN500BDButtonDescription(
        key="key_down",
        translation_key="key_down",
        icon="mdi:chevron-down",
        press_fn=lambda c: c.send_key("down"),
    ),
    DN500BDButtonDescription(
        key="key_left",
        translation_key="key_left",
        icon="mdi:chevron-left",
        press_fn=lambda c: c.send_key("left"),
    ),
    DN500BDButtonDescription(
        key="key_right",
        translation_key="key_right",
        icon="mdi:chevron-right",
        press_fn=lambda c: c.send_key("right"),
    ),
    DN500BDButtonDescription(
        key="key_enter",
        translation_key="key_enter",
        icon="mdi:checkbox-blank-circle",
        press_fn=lambda c: c.send_key("enter"),
    ),
    DN500BDButtonDescription(
        key="key_return",
        translation_key="key_return",
        icon="mdi:arrow-u-left-top",
        press_fn=lambda c: c.send_key("return"),
    ),
    DN500BDButtonDescription(
        key="key_top_menu",
        translation_key="key_top_menu",
        icon="mdi:menu",
        press_fn=lambda c: c.send_key("top_menu"),
    ),
    DN500BDButtonDescription(
        key="key_popup_menu",
        translation_key="key_popup_menu",
        icon="mdi:dots-horizontal-circle-outline",
        press_fn=lambda c: c.send_key("popup_menu"),
    ),
    DN500BDButtonDescription(
        key="key_home",
        translation_key="key_home",
        icon="mdi:home",
        press_fn=lambda c: c.send_key("home"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DN500BDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(DN500BDButton(coordinator, entry, desc) for desc in BUTTONS)


class DN500BDButton(DN500BDEntity, ButtonEntity):
    """Generic DN-500BD button."""

    entity_description: DN500BDButtonDescription

    def __init__(self, coordinator, entry, description: DN500BDButtonDescription) -> None:
        """Init."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Run the action."""
        try:
            await self.entity_description.press_fn(self.client)
        except DN500BDError as err:
            raise HomeAssistantError(f"DN-500BD: {err}") from err
        await self.coordinator.async_request_refresh()

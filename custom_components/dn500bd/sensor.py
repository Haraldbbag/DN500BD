"""Sensors for the DN-500BD MKII (timecode, chapter, title, status)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DN500BDConfigEntry
from .client import DN500BDState, format_time
from .entity import DN500BDEntity


@dataclass(frozen=True, kw_only=True)
class DN500BDSensorDescription(SensorEntityDescription):
    """Sensor description with value getter."""

    value_fn: Callable[[DN500BDState], str | int | None]


SENSORS: tuple[DN500BDSensorDescription, ...] = (
    DN500BDSensorDescription(
        key="timecode",
        translation_key="timecode",
        icon="mdi:timer-outline",
        value_fn=lambda s: format_time(s.elapsed),
    ),
    DN500BDSensorDescription(
        key="remaining",
        translation_key="remaining",
        icon="mdi:timer-sand",
        value_fn=lambda s: format_time(s.remaining),
    ),
    DN500BDSensorDescription(
        key="elapsed_seconds",
        translation_key="elapsed_seconds",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.elapsed,
    ),
    DN500BDSensorDescription(
        key="chapter",
        translation_key="chapter",
        icon="mdi:bookmark-outline",
        value_fn=lambda s: s.chapter,
    ),
    DN500BDSensorDescription(
        key="chapters_total",
        translation_key="chapters_total",
        icon="mdi:bookmark-multiple-outline",
        value_fn=lambda s: s.chapters_total,
    ),
    DN500BDSensorDescription(
        key="title",
        translation_key="title",
        icon="mdi:filmstrip",
        value_fn=lambda s: s.title,
    ),
    DN500BDSensorDescription(
        key="titles_total",
        translation_key="titles_total",
        icon="mdi:filmstrip-box-multiple",
        value_fn=lambda s: s.titles_total,
    ),
    DN500BDSensorDescription(
        key="transport",
        translation_key="transport",
        icon="mdi:play-pause",
        value_fn=lambda s: s.transport,
    ),
    DN500BDSensorDescription(
        key="disc",
        translation_key="disc",
        icon="mdi:disc",
        value_fn=lambda s: s.disc,
    ),
    DN500BDSensorDescription(
        key="disc_type",
        translation_key="disc_type",
        icon="mdi:disc-player",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.media_type,
    ),
    DN500BDSensorDescription(
        key="audio_format",
        translation_key="audio_format",
        icon="mdi:surround-sound",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.audio_format,
    ),
    DN500BDSensorDescription(
        key="audio_channels",
        translation_key="audio_channels",
        icon="mdi:speaker-multiple",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.audio_channels,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DN500BDConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(DN500BDSensor(coordinator, entry, desc) for desc in SENSORS)


class DN500BDSensor(DN500BDEntity, SensorEntity):
    """Generic DN-500BD sensor."""

    entity_description: DN500BDSensorDescription

    def __init__(self, coordinator, entry, description: DN500BDSensorDescription) -> None:
        """Init."""
        super().__init__(coordinator, entry, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        """Current value."""
        data = self.coordinator.data
        if data is None or not data.power:
            return None
        return self.entity_description.value_fn(data)

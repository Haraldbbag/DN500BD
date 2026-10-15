"""Base entity for the DN-500BD MKII."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import DN500BDCoordinator


class DN500BDEntity(CoordinatorEntity[DN500BDCoordinator]):
    """Common device info and naming."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DN500BDCoordinator, entry: ConfigEntry, key: str) -> None:
        """Init."""
        super().__init__(coordinator)
        self._entry = entry
        base = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{base}_{key}"
        state = coordinator.client.state
        connections = {(CONNECTION_NETWORK_MAC, state.mac)} if state.mac else set()
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base)},
            connections=connections,
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=entry.title,
            sw_version=state.firmware,
            configuration_url=None,
        )

    @property
    def client(self):
        """Protocol client."""
        return self.coordinator.client

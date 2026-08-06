"""Shared entity base class for the Locking Cover integration.

Provides the common DeviceInfo (one virtual device per config entry) and
unique_id pattern so cover.py / lock.py / sensor.py stay free of
boilerplate.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import LockingCoverCoordinator


class LockingCoverEntity(CoordinatorEntity[LockingCoverCoordinator]):
    """Base class wiring up unique_id, device_info and the controller."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LockingCoverCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self.controller = coordinator.controller
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        via_device_identifier: tuple[str, str] | None = None
        ent_reg = er.async_get(coordinator.hass)
        source_registry_entry = ent_reg.async_get(self.controller.config.source_cover)
        if source_registry_entry and source_registry_entry.device_id:
            dev_reg = dr.async_get(coordinator.hass)
            device = dev_reg.async_get(source_registry_entry.device_id)
            if device and device.identifiers:
                via_device_identifier = next(iter(device.identifiers))

        device_info: DeviceInfo = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=self.controller.config.name,
            manufacturer="Custom Integration",
            model="Locking Cover Wrapper",
        )
        if via_device_identifier is not None:
            device_info["via_device"] = via_device_identifier
        self._attr_device_info = device_info

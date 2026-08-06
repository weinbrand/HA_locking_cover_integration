"""Sensor platform for the Locking Cover integration.

Exposes the three independent state machines (bolt, tension, overall status)
plus the last error as separate enum sensors, matching the requirement that
these are independent states, not folded into cover/lock attributes.
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LockingCoverConfigEntry
from .const import BoltState, ErrorState, TensionState
from .entity import LockingCoverEntity

_LOGGER = logging.getLogger(__name__)

_STATUS_READY = "ready"
_STATUS_SEQUENCE_RUNNING = "sequence_running"
_STATUS_WAITING_FOR_UNLOCK = "waiting_for_unlock"
_STATUS_ERROR = "error"
_STATUS_OPTIONS = [
    _STATUS_READY,
    _STATUS_SEQUENCE_RUNNING,
    _STATUS_WAITING_FOR_UNLOCK,
    _STATUS_ERROR,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LockingCoverConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    async_add_entities(
        [
            BoltStateSensor(coordinator, entry),
            TensionStateSensor(coordinator, entry),
            StatusSensor(coordinator, entry),
            LastErrorSensor(coordinator, entry),
        ]
    )


class BoltStateSensor(LockingCoverEntity, SensorEntity):
    """sensor.<name>_bolzen"""

    _attr_translation_key = "bolt_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in BoltState]

    def __init__(self, coordinator, entry: LockingCoverConfigEntry) -> None:
        super().__init__(coordinator, entry, "bolzen")

    @property
    def native_value(self) -> str:
        return self.controller.state.bolt_state.value

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None]:
        state = self.controller.state
        return {
            "bolt_left_entity_id": self.controller.config.bolt_left,
            "bolt_right_entity_id": self.controller.config.bolt_right,
            "bolt_left_raw": state.bolt_left_raw,
            "bolt_right_raw": state.bolt_right_raw,
            "identical_sensors": state.identical_sensors,
        }


class TensionStateSensor(LockingCoverEntity, SensorEntity):
    """sensor.<name>_spannung"""

    _attr_translation_key = "tension_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in TensionState]

    def __init__(self, coordinator, entry: LockingCoverConfigEntry) -> None:
        super().__init__(coordinator, entry, "spannung")

    @property
    def native_value(self) -> str:
        return self.controller.state.tension_state.value


class StatusSensor(LockingCoverEntity, SensorEntity):
    """sensor.<name>_status - human-facing summary of what the integration
    is currently doing."""

    _attr_translation_key = "status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = _STATUS_OPTIONS

    def __init__(self, coordinator, entry: LockingCoverConfigEntry) -> None:
        super().__init__(coordinator, entry, "status")

    @property
    def native_value(self) -> str:
        state = self.controller.state
        if state.last_error != ErrorState.NONE:
            return _STATUS_ERROR
        if state.pending_target_position is not None:
            return _STATUS_WAITING_FOR_UNLOCK
        if state.moving:
            return _STATUS_SEQUENCE_RUNNING
        return _STATUS_READY

    @property
    def extra_state_attributes(self) -> dict[str, int | None]:
        return {"pending_target_position": self.controller.state.pending_target_position}


class LastErrorSensor(LockingCoverEntity, SensorEntity):
    """sensor.<name>_letzter_fehler"""

    _attr_translation_key = "last_error"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in ErrorState]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: LockingCoverConfigEntry) -> None:
        super().__init__(coordinator, entry, "letzter_fehler")

    @property
    def native_value(self) -> str:
        return self.controller.state.last_error.value

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"detail": self.controller.state.last_error_detail}

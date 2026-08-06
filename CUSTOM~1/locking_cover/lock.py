"""Lock platform for the Locking Cover integration.

The lock entity represents the *tensioning mechanism*, not the raw
mechanical bolts: locked == tensioned, unlocked == relaxed. The bolts
themselves are exposed separately via sensor.<name>_bolzen.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LockingCoverConfigEntry
from .const import TensionState
from .entity import LockingCoverEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LockingCoverConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities([LockingCoverLockEntity(runtime.coordinator, entry)])


class LockingCoverLockEntity(LockingCoverEntity, LockEntity):
    """Virtual lock entity, e.g. lock.wetterschutzrollo_ost."""

    _attr_name = None

    def __init__(self, coordinator, entry: LockingCoverConfigEntry) -> None:
        super().__init__(coordinator, entry, "lock")

    @property
    def is_locked(self) -> bool | None:
        state = self.controller.state.tension_state
        if state == TensionState.UNKNOWN:
            return None
        return state == TensionState.TENSIONED

    @property
    def is_locking(self) -> bool:
        return self.controller.state.tension_state == TensionState.TENSIONING

    @property
    def is_unlocking(self) -> bool:
        return self.controller.state.tension_state == TensionState.RELAXING

    async def async_lock(self, **kwargs: Any) -> None:
        await self.controller.async_request_lock()

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.controller.async_request_unlock()

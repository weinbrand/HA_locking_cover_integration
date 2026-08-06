"""Cover platform for the Locking Cover integration.

This entity is a thin wrapper around the source cover entity. It forwards
all user commands to LockingCoverController and, while the tensioning
mechanism is engaged or transitioning (see POSITION_OVERRIDE_STATES),
overrides the reported position/state to "closed / 0%" even if the source
cover briefly reports a slightly open position because of the tension
pulse. The real source position is never modified or lost - it is simply
not surfaced during that window.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_CURRENT_POSITION,
    ATTR_POSITION,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.const import STATE_CLOSED, STATE_CLOSING, STATE_OPENING, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import LockingCoverConfigEntry
from .const import POSITION_OVERRIDE_STATES
from .entity import LockingCoverEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LockingCoverConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    async_add_entities([LockingCoverCoverEntity(runtime.coordinator, entry)])


class LockingCoverCoverEntity(LockingCoverEntity, CoverEntity):
    """Wrapper cover entity, e.g. cover.wetterschutzrollo_ost."""

    _attr_name = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(self, coordinator, entry: LockingCoverConfigEntry) -> None:
        super().__init__(coordinator, entry, "cover")

    @property
    def _source_state(self):
        return self.hass.states.get(self.controller.config.source_cover)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        source = self._source_state
        return source is not None and source.state != STATE_UNAVAILABLE

    @property
    def current_cover_position(self) -> int | None:
        if self.controller.state.tension_state in POSITION_OVERRIDE_STATES:
            return 0
        source = self._source_state
        if source is None:
            return None
        # NOTE: ATTR_POSITION ("position") is the *service call* argument
        # used to command a target position. The entity's own reported
        # current position is a state *attribute* under ATTR_CURRENT_POSITION
        # ("current_position") - reading ATTR_POSITION here would always be
        # None. See PROGRESS.md for the bugfix history.
        return source.attributes.get(ATTR_CURRENT_POSITION)

    @property
    def is_closed(self) -> bool | None:
        if self.controller.state.tension_state in POSITION_OVERRIDE_STATES:
            return True
        source = self._source_state
        if source is None or source.state == STATE_UNAVAILABLE:
            return None
        return source.state == STATE_CLOSED

    @property
    def is_opening(self) -> bool:
        if self.controller.state.tension_state in POSITION_OVERRIDE_STATES:
            return False
        source = self._source_state
        return source is not None and source.state == STATE_OPENING

    @property
    def is_closing(self) -> bool:
        if self.controller.state.tension_state in POSITION_OVERRIDE_STATES:
            return False
        source = self._source_state
        return source is not None and source.state == STATE_CLOSING

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.controller.async_request_open(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.controller.async_request_close()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        position = int(kwargs[ATTR_POSITION])
        if position <= 0:
            await self.controller.async_request_close()
        else:
            await self.controller.async_request_open(position)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.controller.async_request_stop()

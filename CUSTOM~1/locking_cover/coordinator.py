"""Push-based coordinator for the Locking Cover integration.

DataUpdateCoordinator is normally associated with polling. Here it is reused
purely for its well-established "shared state + entity refcounting" role:
`update_interval` is left at None so no polling loop is ever scheduled.
Every time `LockingCoverController` changes its state it sends a dispatcher
signal, which this coordinator turns into `async_set_updated_data()` calls so
that all `CoordinatorEntity` platforms update consistently in one step.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, SIGNAL_UPDATE
from .controller import ControllerRuntimeState, LockingCoverController

_LOGGER = logging.getLogger(__name__)


class LockingCoverCoordinator(DataUpdateCoordinator[ControllerRuntimeState]):
    """Bridges push updates from LockingCoverController to entities."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, controller: LockingCoverController
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=None,
        )
        self.controller = controller
        self.data = controller.state
        self._unsub_signal = async_dispatcher_connect(
            hass, SIGNAL_UPDATE.format(entry_id=entry.entry_id), self._handle_controller_update
        )

    @callback
    def _handle_controller_update(self) -> None:
        self.async_set_updated_data(self.controller.state)

    @callback
    def async_unload(self) -> None:
        self._unsub_signal()

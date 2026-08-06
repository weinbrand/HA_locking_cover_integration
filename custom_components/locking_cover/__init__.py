"""The Locking Cover integration.

Wraps an existing cover entity (e.g. a Shelly Plus 2PM operating a roller
shutter) and adds a mechanical bolt-locking layer on top of it: a lock
entity representing the tensioning mechanism, plus diagnostic sensors. No
changes are made to the wrapped cover's own integration; this component only
calls its public cover.* services and reads its state.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ATTR_CONFIG_ENTRY_ID,
    DOMAIN,
    PLATFORMS,
    SERVICE_FORCE_RELAX,
    SERVICE_RESET_ERROR,
)
from .controller import LockingCoverConfig, LockingCoverController
from .coordinator import LockingCoverCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class LockingCoverRuntimeData:
    """Data stored on the ConfigEntry at runtime (no global state)."""

    controller: LockingCoverController
    coordinator: LockingCoverCoordinator


LockingCoverConfigEntry = ConfigEntry[LockingCoverRuntimeData]  # type alias (kept as plain assignment for broad Python-version compatibility)


async def async_setup_entry(hass: HomeAssistant, entry: LockingCoverConfigEntry) -> bool:
    """Set up Locking Cover from a config entry."""
    config = LockingCoverConfig.from_entry(entry)

    if hass.states.get(config.source_cover) is None:
        raise ConfigEntryNotReady(
            f"Quell-Cover-Entity {config.source_cover} ist (noch) nicht verfügbar"
        )

    controller = LockingCoverController(hass, entry, config)
    await controller.async_setup()
    coordinator = LockingCoverCoordinator(hass, entry, controller)

    entry.runtime_data = LockingCoverRuntimeData(controller=controller, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LockingCoverConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        runtime = entry.runtime_data
        runtime.coordinator.async_unload()
        await runtime.controller.async_unload()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: LockingCoverConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the two maintenance services once per hass instance."""
    if hass.services.has_service(DOMAIN, SERVICE_RESET_ERROR):
        return

    service_schema = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string})

    def _get_entry(call: ServiceCall) -> LockingCoverConfigEntry:
        entry_id = call.data[ATTR_CONFIG_ENTRY_ID]
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                f"Keine geladene Locking-Cover Instanz mit config_entry_id={entry_id} gefunden"
            )
        return entry  # type: ignore[return-value]

    async def _async_handle_reset_error(call: ServiceCall) -> None:
        await _get_entry(call).runtime_data.controller.async_reset_error()

    async def _async_handle_force_relax(call: ServiceCall) -> None:
        await _get_entry(call).runtime_data.controller.async_force_relax()

    hass.services.async_register(
        DOMAIN, SERVICE_RESET_ERROR, _async_handle_reset_error, schema=service_schema
    )
    hass.services.async_register(
        DOMAIN, SERVICE_FORCE_RELAX, _async_handle_force_relax, schema=service_schema
    )

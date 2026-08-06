"""Diagnostics support for the Locking Cover integration."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import LockingCoverConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LockingCoverConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return entry.runtime_data.controller.as_diagnostics()

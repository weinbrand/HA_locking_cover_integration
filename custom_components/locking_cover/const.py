"""Constants for the Locking Cover integration."""

from __future__ import annotations

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "locking_cover"

PLATFORMS: list[Platform] = [Platform.COVER, Platform.LOCK, Platform.SENSOR]

# --- Config / Options keys --------------------------------------------------

CONF_NAME = "name"
CONF_SOURCE_COVER = "source_cover_entity_id"
CONF_BOLT_LEFT = "bolt_left_entity_id"
CONF_BOLT_RIGHT = "bolt_right_entity_id"
CONF_TENSION_TIME_MS = "tension_time_ms"
CONF_OPEN_TIMEOUT_S = "open_timeout_s"

DEFAULT_NAME = "Locking Cover"
DEFAULT_TENSION_TIME_MS = 1000
DEFAULT_OPEN_TIMEOUT_S = 120

MIN_TENSION_TIME_MS = 100
MAX_TENSION_TIME_MS = 30000
MIN_OPEN_TIMEOUT_S = 5
MAX_OPEN_TIMEOUT_S = 3600

# Debounce time for bolt sensor changes. Only a state that remains stable for
# this long is evaluated by the state machine.
BOLT_DEBOUNCE_S = 0.5

# The relax (untensioning) time is derived exclusively from the tension time,
# see LockingCoverConfig.relax_time_ms.
RELAX_TIME_OFFSET_MS = 1000
MIN_RELAX_TIME_MS = 2000

# --- Storage -----------------------------------------------------------------

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = f"{DOMAIN}_state"

# --- Issue registry ----------------------------------------------------------

ISSUE_IDENTICAL_SENSORS = "identical_bolt_sensors"


class BoltState(StrEnum):
    """State of the two mechanical bolt sensors combined."""

    LOCKED = "locked"
    UNLOCKED = "unlocked"
    PARTIALLY_LOCKED = "partially_locked"
    UNKNOWN = "unknown"


class TensionState(StrEnum):
    """State of the tensioning mechanism."""

    TENSIONED = "tensioned"
    RELAXED = "relaxed"
    TENSIONING = "tensioning"
    RELAXING = "relaxing"
    UNKNOWN = "unknown"


class ErrorState(StrEnum):
    """Last known error of the integration."""

    NONE = "none"
    TIMEOUT_WAITING_FOR_UNLOCK = "timeout_waiting_for_unlock"
    SOURCE_COVER_UNAVAILABLE = "source_cover_unavailable"
    INTERRUPTED_SEQUENCE = "interrupted_sequence"
    INVALID_SENSOR_CONFIGURATION = "invalid_sensor_configuration"


# Tension states during which the wrapper cover entity must present itself as
# fully closed (position 0%), regardless of what the source cover reports.
#
# Architecture decision (see IMPLEMENTATION_PLAN.md, "Architekturentscheidungen"):
# the spec text only names TENSIONED explicitly, but TENSIONING and RELAXING
# are short engage/disengage pulses executed while the shutter physically
# rests at the bottom end-stop. Surfacing the brief position blip these
# pulses cause on the source cover would be confusing (the shutter would
# appear to "flicker open" for about a second). All three states are
# therefore treated identically for display purposes. RELAXED is
# intentionally excluded: once relaxed, the source cover's own position is
# authoritative again.
POSITION_OVERRIDE_STATES = frozenset(
    {TensionState.TENSIONING, TensionState.TENSIONED, TensionState.RELAXING}
)

SERVICE_RESET_ERROR = "reset_error"
SERVICE_FORCE_RELAX = "force_relax"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

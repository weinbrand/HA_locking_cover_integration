"""Core state machine and business logic for the Locking Cover integration.

This module contains no Home Assistant entity code. It is a plain,
independently testable controller that owns:

* the debounced evaluation of the two bolt sensors,
* the tensioning / relaxing sequences (pulsing the source cover),
* the "wait for unlock, then move to target position" opening logic,
* persistence of the last stable tension state across restarts,
* error tracking.

Entities (cover.py, lock.py, sensor.py) are thin, stateless views on top of
`LockingCoverController.state` and call its public `async_request_*` methods
to translate Home Assistant service calls into state-machine transitions.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, dataclass, field
import logging
from typing import Any

from homeassistant.components.cover import ATTR_POSITION
from homeassistant.components.cover import DOMAIN as COVER_DOMAIN
from homeassistant.components.persistent_notification import (
    async_create as pn_async_create,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_CLOSED,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.storage import Store

from .const import (
    BOLT_DEBOUNCE_S,
    CONF_BOLT_LEFT,
    CONF_BOLT_RIGHT,
    CONF_NAME,
    CONF_OPEN_TIMEOUT_S,
    CONF_SOURCE_COVER,
    CONF_TENSION_TIME_MS,
    DEFAULT_OPEN_TIMEOUT_S,
    DEFAULT_TENSION_TIME_MS,
    DOMAIN,
    ISSUE_IDENTICAL_SENSORS,
    MIN_RELAX_TIME_MS,
    RELAX_TIME_OFFSET_MS,
    SIGNAL_UPDATE,
    STORAGE_VERSION,
    BoltState,
    ErrorState,
    TensionState,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class LockingCoverConfig:
    """Immutable, resolved configuration for one Locking Cover device."""

    name: str
    source_cover: str
    bolt_left: str
    bolt_right: str
    tension_time_ms: int
    open_timeout_s: int

    @property
    def relax_time_ms(self) -> int:
        """Relax time is derived exclusively from the tension time.

        relax_time = max(tension_time + 1000 ms, 2000 ms)
        There is no separate configuration option for this on purpose.
        """
        return max(self.tension_time_ms + RELAX_TIME_OFFSET_MS, MIN_RELAX_TIME_MS)

    @property
    def identical_sensors(self) -> bool:
        """True if the left and right bolt sensor are the same entity."""
        return self.bolt_left == self.bolt_right

    @classmethod
    def from_entry(cls, entry: ConfigEntry) -> LockingCoverConfig:
        data: dict[str, Any] = {**entry.data, **entry.options}
        return cls(
            name=data.get(CONF_NAME, entry.title),
            source_cover=data[CONF_SOURCE_COVER],
            bolt_left=data[CONF_BOLT_LEFT],
            bolt_right=data[CONF_BOLT_RIGHT],
            tension_time_ms=int(data.get(CONF_TENSION_TIME_MS, DEFAULT_TENSION_TIME_MS)),
            open_timeout_s=int(data.get(CONF_OPEN_TIMEOUT_S, DEFAULT_OPEN_TIMEOUT_S)),
        )


@dataclass
class ControllerRuntimeState:
    """Mutable, observable runtime state. Read by entities, written only by
    LockingCoverController."""

    bolt_left_raw: str | None = None
    bolt_right_raw: str | None = None
    bolt_state: BoltState = BoltState.UNKNOWN
    tension_state: TensionState = TensionState.UNKNOWN
    pending_target_position: int | None = None
    moving: bool = False
    last_error: ErrorState = ErrorState.NONE
    last_error_detail: str | None = None
    identical_sensors: bool = False


class LockingCoverController:
    """Owns the state machine for a single Locking Cover device."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, config: LockingCoverConfig
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.config = config
        self.state = ControllerRuntimeState(identical_sensors=config.identical_sensors)

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}"
        )
        self._unsub_listeners: list[Any] = []
        self._debounce_cancel: dict[str, Any] = {}
        self._sequence_task: asyncio.Task[None] | None = None
        self._sequence_lock = asyncio.Lock()
        self._open_timeout_cancel: Any | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Load persisted state, register listeners and reconcile once."""
        stored = await self._store.async_load() or {}
        try:
            self.state.tension_state = TensionState(stored.get("tension_state"))
        except ValueError:
            self.state.tension_state = TensionState.UNKNOWN

        _LOGGER.info(
            "[%s] Wiederherstellung nach Neustart: gespeicherter Spannzustand=%s. "
            "Sensorsignale haben Vorrang, keine Bewegung wird automatisch fortgesetzt.",
            self.config.name,
            self.state.tension_state,
        )

        if self.config.identical_sensors:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_IDENTICAL_SENSORS}_{self.entry.entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_IDENTICAL_SENSORS,
                translation_placeholders={"name": self.config.name},
            )
            _LOGGER.warning(
                "[%s] Diagnose: linker und rechter Bolzensensor sind identisch (%s). "
                "Nur für Testzwecke zulässig, siehe Repairs.",
                self.config.name,
                self.config.bolt_left,
            )

        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass,
                [self.config.bolt_left, self.config.bolt_right],
                self._async_bolt_changed,
            )
        )
        self._unsub_listeners.append(
            async_track_state_change_event(
                self.hass, [self.config.source_cover], self._async_source_cover_changed
            )
        )

        # Initial reconciliation from already-stable sensor states (no debounce
        # needed for values that are not currently changing).
        self._recompute_bolt_state(log_prefix="Wiederherstellung nach Neustart")
        await self._async_evaluate(reason="startup")

    async def async_unload(self) -> None:
        """Tear down listeners, timers and running sequences."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

        for cancel in self._debounce_cancel.values():
            cancel()
        self._debounce_cancel.clear()

        if self._open_timeout_cancel:
            self._open_timeout_cancel()
            self._open_timeout_cancel = None

        await self._async_cancel_current_sequence()

        if self.config.identical_sensors:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_IDENTICAL_SENSORS}_{self.entry.entry_id}"
            )

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id))

    def as_diagnostics(self) -> dict[str, Any]:
        return {
            "config": {
                "name": self.config.name,
                "source_cover": self.config.source_cover,
                "bolt_left": self.config.bolt_left,
                "bolt_right": self.config.bolt_right,
                "tension_time_ms": self.config.tension_time_ms,
                "relax_time_ms": self.config.relax_time_ms,
                "open_timeout_s": self.config.open_timeout_s,
                "identical_sensors": self.config.identical_sensors,
            },
            "state": asdict(self.state),
            "sequence_running": bool(self._sequence_task and not self._sequence_task.done()),
        }

    # ------------------------------------------------------------------
    # Bolt sensor handling (debounced)
    # ------------------------------------------------------------------

    def _read_bolt_raw(self, entity_id: str) -> str | None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, "unknown"):
            return None
        return state.state  # "on" or "off"

    def _recompute_bolt_state(self, *, log_prefix: str = "Sensorwechsel") -> None:
        left = self._read_bolt_raw(self.config.bolt_left)
        right = self._read_bolt_raw(self.config.bolt_right)
        self.state.bolt_left_raw = left
        self.state.bolt_right_raw = right

        # unknown/unavailable never counts as "open" (STATE_ON).
        if left == STATE_OFF and right == STATE_OFF:
            new_state = BoltState.LOCKED
        elif left == STATE_ON and right == STATE_ON:
            new_state = BoltState.UNLOCKED
        elif left is None and right is None:
            new_state = BoltState.UNKNOWN
        else:
            # Any other combination (one open + one closed, or one
            # known + one unknown) is a safe intermediate state: upward
            # movement stays blocked, downward movement stays allowed.
            new_state = BoltState.PARTIALLY_LOCKED

        if new_state != self.state.bolt_state:
            _LOGGER.info(
                "[%s] %s: Bolzenstatus %s -> %s (links=%s, rechts=%s)",
                self.config.name,
                log_prefix,
                self.state.bolt_state,
                new_state,
                left,
                right,
            )
            self.state.bolt_state = new_state

    @callback
    def _async_bolt_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new_state = event.data["new_state"]
        _LOGGER.debug(
            "[%s] Rohes Sensorsignal %s -> %s (500ms Entprellung gestartet)",
            self.config.name,
            entity_id,
            new_state.state if new_state else None,
        )

        cancel = self._debounce_cancel.pop(entity_id, None)
        if cancel is not None:
            cancel()

        @callback
        def _debounced(_now: Any) -> None:
            self._debounce_cancel.pop(entity_id, None)
            self._recompute_bolt_state()
            self.hass.async_create_task(self._async_evaluate(reason=f"bolt_change:{entity_id}"))

        self._debounce_cancel[entity_id] = async_call_later(
            self.hass, BOLT_DEBOUNCE_S, _debounced
        )

    @callback
    def _async_source_cover_changed(self, event: Event[EventStateChangedData]) -> None:
        self.hass.async_create_task(self._async_evaluate(reason="source_cover_change"))

    # ------------------------------------------------------------------
    # Central background evaluation (auto tension / auto relax / resume open)
    # ------------------------------------------------------------------

    async def _async_evaluate(self, *, reason: str) -> None:
        source = self.hass.states.get(self.config.source_cover)
        if source is None or source.state == STATE_UNAVAILABLE:
            if self.state.last_error != ErrorState.SOURCE_COVER_UNAVAILABLE:
                self.state.last_error = ErrorState.SOURCE_COVER_UNAVAILABLE
                self.state.last_error_detail = "Quell-Cover-Entity ist nicht verfügbar."
                _LOGGER.warning(
                    "[%s] Quell-Cover %s ist nicht verfügbar",
                    self.config.name,
                    self.config.source_cover,
                )
                self._notify()
            return

        if self.state.last_error == ErrorState.SOURCE_COVER_UNAVAILABLE:
            self.state.last_error = ErrorState.NONE
            self.state.last_error_detail = None

        cover_closed = source.state == STATE_CLOSED

        # Fall 2: at least one bolt sensor reports (stable) open while
        # tensioned -> relax.
        if self.state.tension_state == TensionState.TENSIONED and (
            self.state.bolt_left_raw == STATE_ON or self.state.bolt_right_raw == STATE_ON
        ):
            _LOGGER.info(
                "[%s] Bolzen hat sich während tensioned geöffnet -> automatisches Entspannen",
                self.config.name,
            )
            await self._async_start_relax(reason="bolt_opened")
            return

        # Automatic tensioning: cover closed, both bolts locked, not already
        # tensioned, no movement in progress.
        if (
            cover_closed
            and self.state.bolt_state == BoltState.LOCKED
            and self.state.tension_state != TensionState.TENSIONED
            and not self.state.moving
            and (self._sequence_task is None or self._sequence_task.done())
        ):
            await self._async_start_tension(reason=reason)
            return

        # Deferred open request: both bolts finally unlocked.
        if (
            self.state.pending_target_position is not None
            and self.state.bolt_state == BoltState.UNLOCKED
        ):
            await self._async_resume_pending_open()
            return

        self._notify()

    # ------------------------------------------------------------------
    # Tension / relax sequences
    # ------------------------------------------------------------------

    async def _async_cancel_current_sequence(self) -> None:
        task = self._sequence_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._sequence_task = None

    async def _async_start_tension(self, *, reason: str) -> None:
        if self.state.tension_state == TensionState.TENSIONING and self._sequence_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._sequence_task
            return
        await self._async_cancel_current_sequence()
        self._sequence_task = self.entry.async_create_background_task(
            self.hass,
            self._async_run_tension(),
            name=f"{DOMAIN}_tension_{self.entry.entry_id}",
        )
        with contextlib.suppress(asyncio.CancelledError):
            await self._sequence_task

    async def _async_run_tension(self) -> None:
        async with self._sequence_lock:
            self.state.moving = True
            self.state.tension_state = TensionState.TENSIONING
            _LOGGER.info(
                "[%s] Spannbeginn: Aufwärtsfahrt für %d ms",
                self.config.name,
                self.config.tension_time_ms,
            )
            self._notify()
            try:
                await self.hass.services.async_call(
                    COVER_DOMAIN,
                    "open_cover",
                    {ATTR_ENTITY_ID: self.config.source_cover},
                    blocking=True,
                )
                await asyncio.sleep(self.config.tension_time_ms / 1000)
                await self.hass.services.async_call(
                    COVER_DOMAIN,
                    "stop_cover",
                    {ATTR_ENTITY_ID: self.config.source_cover},
                    blocking=True,
                )
            except asyncio.CancelledError:
                self.state.last_error = ErrorState.INTERRUPTED_SEQUENCE
                self.state.last_error_detail = "Spannvorgang wurde unterbrochen."
                _LOGGER.warning("[%s] Spannvorgang unterbrochen", self.config.name)
                with contextlib.suppress(Exception):
                    await self.hass.services.async_call(
                        COVER_DOMAIN,
                        "stop_cover",
                        {ATTR_ENTITY_ID: self.config.source_cover},
                        blocking=True,
                    )
                raise
            else:
                self.state.tension_state = TensionState.TENSIONED
                await self._async_persist_tension_state()
                _LOGGER.info("[%s] Spannende: Spannzustand=tensioned", self.config.name)
            finally:
                self.state.moving = False
                self._notify()

    async def _async_start_relax(self, *, reason: str) -> None:
        if self.state.tension_state == TensionState.RELAXING and self._sequence_task:
            with contextlib.suppress(asyncio.CancelledError):
                await self._sequence_task
            return
        await self._async_cancel_current_sequence()
        self._sequence_task = self.entry.async_create_background_task(
            self.hass,
            self._async_run_relax(),
            name=f"{DOMAIN}_relax_{self.entry.entry_id}",
        )
        with contextlib.suppress(asyncio.CancelledError):
            await self._sequence_task

    async def _async_run_relax(self) -> None:
        async with self._sequence_lock:
            self.state.moving = True
            self.state.tension_state = TensionState.RELAXING
            _LOGGER.info(
                "[%s] Entspannbeginn: Abwärtsfahrt für %d ms",
                self.config.name,
                self.config.relax_time_ms,
            )
            self._notify()
            try:
                await self.hass.services.async_call(
                    COVER_DOMAIN,
                    "close_cover",
                    {ATTR_ENTITY_ID: self.config.source_cover},
                    blocking=True,
                )
                await asyncio.sleep(self.config.relax_time_ms / 1000)
                await self.hass.services.async_call(
                    COVER_DOMAIN,
                    "stop_cover",
                    {ATTR_ENTITY_ID: self.config.source_cover},
                    blocking=True,
                )
            except asyncio.CancelledError:
                self.state.last_error = ErrorState.INTERRUPTED_SEQUENCE
                self.state.last_error_detail = "Entspannvorgang wurde unterbrochen."
                _LOGGER.warning("[%s] Entspannvorgang unterbrochen", self.config.name)
                with contextlib.suppress(Exception):
                    await self.hass.services.async_call(
                        COVER_DOMAIN,
                        "stop_cover",
                        {ATTR_ENTITY_ID: self.config.source_cover},
                        blocking=True,
                    )
                raise
            else:
                self.state.tension_state = TensionState.RELAXED
                await self._async_persist_tension_state()
                _LOGGER.info("[%s] Entspannende: Spannzustand=relaxed", self.config.name)
            finally:
                self.state.moving = False
                self._notify()

    async def _async_persist_tension_state(self) -> None:
        await self._store.async_save({"tension_state": self.state.tension_state.value})

    # ------------------------------------------------------------------
    # Public API used by the lock entity
    # ------------------------------------------------------------------

    async def async_request_lock(self) -> None:
        """Handle a lock.lock() call on the virtual lock entity.

        If the cover is not yet fully closed with both bolts locked, this
        now drives the cover down to its lower end-stop instead of
        rejecting the call. Tensioning itself still only ever engages
        reactively - once _async_evaluate() observes cover=closed and
        bolt_state=LOCKED (e.g. after the close finishes and the bolts
        mechanically engage) - so "Lock" effectively becomes "close and
        lock" for a cover that is not yet down.
        """
        if self.state.tension_state in (TensionState.TENSIONED, TensionState.TENSIONING):
            return

        source = self.hass.states.get(self.config.source_cover)
        cover_closed = source is not None and source.state == STATE_CLOSED

        if cover_closed and self.state.bolt_state == BoltState.LOCKED:
            await self._async_start_tension(reason="manual_lock")
            return

        _LOGGER.info(
            "[%s] Verriegeln angefordert: Cover ist noch nicht in unterer Endlage, "
            "fahre zunächst herunter (Spannen erfolgt automatisch, sobald geschlossen "
            "und beide Bolzen verriegelt melden)",
            self.config.name,
        )
        await self.async_request_close()

    async def async_request_unlock(self) -> None:
        """Handle a lock.unlock() call on the virtual lock entity (Fall 1)."""
        if self.state.tension_state in (TensionState.RELAXED, TensionState.RELAXING):
            return
        await self._async_start_relax(reason="manual_unlock")

    # ------------------------------------------------------------------
    # Public API used by the cover entity
    # ------------------------------------------------------------------

    async def async_request_open(self, target_position: int) -> None:
        _LOGGER.info(
            "[%s] Öffnungsanfrage: Zielposition=%d%%", self.config.name, target_position
        )
        self._cancel_open_timeout()

        if self.state.tension_state in (TensionState.TENSIONED, TensionState.TENSIONING):
            await self._async_start_relax(reason="open_request")

        if self.state.bolt_state == BoltState.UNLOCKED:
            self.state.pending_target_position = None
            await self._async_move_source(target_position)
            return

        self.state.pending_target_position = target_position
        self.state.last_error = ErrorState.NONE
        self.state.last_error_detail = None
        _LOGGER.info(
            "[%s] Zielposition %d%% gespeichert, warte auf Entriegelung beider Bolzen (Timeout=%ds)",
            self.config.name,
            target_position,
            self.config.open_timeout_s,
        )
        self._notify()

        @callback
        def _on_timeout(_now: Any) -> None:
            self._open_timeout_cancel = None
            self.hass.async_create_task(self._async_on_open_timeout())

        self._open_timeout_cancel = async_call_later(
            self.hass, self.config.open_timeout_s, _on_timeout
        )

    async def async_request_close(self) -> None:
        _LOGGER.info("[%s] Schließanfrage: fahre bis untere Endlage", self.config.name)
        self._discard_pending_open(log=False)
        await self.hass.services.async_call(
            COVER_DOMAIN, "close_cover", {ATTR_ENTITY_ID: self.config.source_cover}, blocking=False
        )
        self._notify()

    async def async_request_stop(self) -> None:
        _LOGGER.info("[%s] Stopp angefordert", self.config.name)
        self._discard_pending_open(log=True)
        await self.hass.services.async_call(
            COVER_DOMAIN, "stop_cover", {ATTR_ENTITY_ID: self.config.source_cover}, blocking=False
        )
        self._notify()

    def _discard_pending_open(self, *, log: bool) -> None:
        if self.state.pending_target_position is not None:
            if log:
                _LOGGER.info(
                    "[%s] Wartende Öffnung durch Stopp verworfen", self.config.name
                )
            self.state.pending_target_position = None
        self._cancel_open_timeout()

    def _cancel_open_timeout(self) -> None:
        if self._open_timeout_cancel:
            self._open_timeout_cancel()
            self._open_timeout_cancel = None

    async def _async_resume_pending_open(self) -> None:
        target = self.state.pending_target_position
        self.state.pending_target_position = None
        self._cancel_open_timeout()
        _LOGGER.info(
            "[%s] Beide Bolzen entriegelt, fahre auf gespeicherte Zielposition %d%%",
            self.config.name,
            target,
        )
        await self._async_move_source(target)  # type: ignore[arg-type]

    async def _async_move_source(self, target_position: int) -> None:
        if target_position >= 100:
            await self.hass.services.async_call(
                COVER_DOMAIN,
                "open_cover",
                {ATTR_ENTITY_ID: self.config.source_cover},
                blocking=False,
            )
        else:
            await self.hass.services.async_call(
                COVER_DOMAIN,
                "set_cover_position",
                {ATTR_ENTITY_ID: self.config.source_cover, ATTR_POSITION: target_position},
                blocking=False,
            )
        self._notify()

    async def _async_on_open_timeout(self) -> None:
        if self.state.pending_target_position is None:
            return
        _LOGGER.warning(
            "[%s] Timeout (%ds) beim Warten auf Entriegelung erreicht, Zielposition verworfen",
            self.config.name,
            self.config.open_timeout_s,
        )
        self.state.pending_target_position = None
        self.state.last_error = ErrorState.TIMEOUT_WAITING_FOR_UNLOCK
        self.state.last_error_detail = (
            f"Timeout nach {self.config.open_timeout_s}s beim Warten auf Entriegelung "
            "beider Bolzensensoren."
        )
        await pn_async_create(
            self.hass,
            message=(
                f"'{self.config.name}': Timeout beim Warten auf Entriegelung der "
                "Bolzensensoren. Die Öffnungsanfrage wurde verworfen, es wurde keine "
                "automatische Bewegung ausgelöst. Bitte Bolzenverriegelung prüfen."
            ),
            title=f"{self.config.name}: Timeout beim Öffnen",
            notification_id=f"{DOMAIN}_{self.entry.entry_id}_open_timeout",
        )
        self._notify()

    # ------------------------------------------------------------------
    # Maintenance services
    # ------------------------------------------------------------------

    async def async_reset_error(self) -> None:
        self.state.last_error = ErrorState.NONE
        self.state.last_error_detail = None
        _LOGGER.info("[%s] Fehlerzustand manuell zurückgesetzt", self.config.name)
        self._notify()

    async def async_force_relax(self) -> None:
        _LOGGER.info("[%s] Manuelles Entspannen angefordert (Service)", self.config.name)
        await self._async_start_relax(reason="service_force_relax")

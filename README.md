# Locking Cover — Home Assistant Custom Integration

Wraps an existing `cover` entity (e.g. a **Shelly Plus 2PM** in cover mode)
and adds a mechanical bolt-locking layer on top of it, without modifying the
wrapped cover's own integration.

Creates one virtual device with:

| Entity | Purpose |
|---|---|
| `cover.<name>` | Wrapper cover (position/state overridden while tensioned) |
| `lock.<name>` | Represents the **tensioning mechanism** (locked = tensioned, unlocked = relaxed) |
| `sensor.<name>_bolzen` | Combined state of both mechanical bolt sensors |
| `sensor.<name>_spannung` | Tension state (tensioned / relaxed / tensioning / relaxing) |
| `sensor.<name>_status` | Human-facing summary (ready / sequence_running / waiting_for_unlock / error) |
| `sensor.<name>_letzter_fehler` | Last error (diagnostic entity) |

## Installation (HACS custom repository)

1. HACS → the three-dot menu → **Custom repositories**.
2. Add this repository's URL, category **Integration**.
3. Install **Locking Cover**, restart Home Assistant.
4. Settings → Devices & services → **Add integration** → search for
   *Locking Cover*.

## Configuration

All configuration happens through the UI config flow:

* **Source cover** — the existing cover entity to wrap (e.g. the Shelly).
* **Left / right bolt sensor** — two `binary_sensor` entities. `off` = bolt
  closed/locked, `on` = bolt open/unlocked (standard `door`/`opening`
  device-class convention). The same sensor may be selected twice for
  testing; this is flagged as a Repair issue afterwards, not blocked.
* **Tension time** (default 1000 ms) — how long the source cover is pulsed
  upward to tension the lock once it is closed and both bolts are locked.
* **Open timeout** (default 120 s) — how long to wait for both bolts to
  report unlocked before an open request is abandoned.

The relax time is *always* derived from the tension time
(`max(tension_time + 1000 ms, 2000 ms)`) and cannot be configured
separately — this is intentional, see the architecture notes.

Bolt sensors, tension time and open timeout can be changed later via the
integration's **Configure** (options) dialog. The source cover cannot be
changed after setup; remove and re-add the integration if it needs to
point at a different cover.

## Behaviour summary

* Cover, bolt and tension state are three independent state machines.
* Bolt sensor changes are debounced by 500 ms; only a stable reading is
  evaluated.
* Whenever the cover is closed and both bolts become (stably) locked, the
  integration automatically tensions the lock (short upward pulse).
* Opening (`cover.open_cover` / `set_cover_position` > 0) relaxes a tensioned
  lock first, then waits for both bolts to unlock before moving to the
  requested position. If this doesn't happen within the configured timeout,
  the request is dropped, `sensor.<name>_letzter_fehler` is set, and a
  persistent notification is created — no automatic movement is triggered.
* `cover.close_cover` simply drives the source cover to its lower end-stop;
  tensioning is handled reactively once the bolts report locked.
* While tensioning/tensioned/relaxing, `cover.<name>` always reports
  `closed` / position `0 %`, even if the source cover briefly reports a
  slightly open position because of the tensioning pulse.

See `IMPLEMENTATION_PLAN.md` and `PROGRESS.md` (delivered alongside this
repository, not part of the repository itself) for the full specification
mapping, architecture decisions and implementation log.

## Services

* `locking_cover.reset_error` — clears the last error of an instance.
* `locking_cover.force_relax` — manually triggers a relax sequence
  (maintenance/recovery).

## License

MIT, see `LICENSE`.

# Power workbench: the console and the current on one time axis

Peter, 2026-11-21: "now is the time to define the ultimate power profiling workbench / dashboard so that we can not only get debugging logs but also match that with the real power being consumed at any point in time."
Also: "the recording period might be time based, it may be event based i.e wake/sleep", "we may also want to record 2 or more wake events and not just one, and maybe to just run continuous", and "having fixed periods of 1 minute, 10 minutes etc etc is impractical".
The firmware session (`device`, xsession `device--nrf-workbench`, #52 to #64) has answered what a firmware developer needs; its facts are cited where used.
Extends `meter-page.md`, `ppk2-stages.md`, `console.md` and `project-power-profile.md`; where they differ this one is later. Mockup: layout A of the Meter mockups (console lane, cursor, Capture card).

## The one idea

**Pick any instant of a capture and get everything that was true then:** what the supply was doing, the current, which phase of the wake it was, and the lines the firmware printed. The same answer for a person pointing at the scope and for an agent calling a command.

## What is wrong today (each read in the code this session)

1. **The console is in memory only.** Nothing in `console/` writes to disk, so a restart empties it; the firmware session asked for it twice (#57, #58).
2. **There is no way to ask "what was happening at time T".** The recording's `wall` column is `time.time()` at the moment a 0.2 s block is appended (`sampler.py`), and each console line carries `wall` from the broker's `time.time()`: one host clock, but nothing joins them.
3. **A wake's length is not the awake period.** A capture is the lead-in block (0.2 s) + the awake blocks + one quiet block as its tail (0.2 s) (`capture.py`), so `duration_s` is the awake period plus about 0.4 s. The page's cycle table uses a different rule (a block whose highest sample is over half the peak) and gave 21.6 s where the capture said 22.6 s: two rules, two answers, for one thing.
4. **No per-wake table, no outlier flag, no phases by level.** `meter.captures` gives `n, t0, wake_at_s, duration_s, samples, lost, truncated, peak, mean` and nothing else.
5. **Battery life exists only in the browser** (`cycle.js`); the server returns `charge_mah` for a session.
6. **The capture card offers five fixed lengths** and cannot stop on a wake.

## The model

**One clock: the recording clock** `t_s`, seconds on the PPK2's sample clock from the recording's start (10 µs resolution). Everything else is a **lane** on it:

| lane | from | clock and how good |
|---|---|---|
| Supply | the unit's mode, source voltage and output state, changes stamped when this service makes them | host wall, mapped (below) |
| Current | the PPK2 envelope, and full-rate wakes where kept | the sample clock itself |
| Phases | the current, cut by level (below) | derived from the current |
| Events | named stretches (`meter-page.md`, "Events"), from a person, from the project profile, or from a console pattern | the recording clock |
| Console | persisted lines, each `{wall, link, text}` | host wall, mapped; a line that states its own offset (below) is placed more exactly |
| Logic | the PPK2's 8 digital inputs, already in each raw word (`ppk2-stages.md`, stage 3) | the sample clock, exact; needs a firmware pin and a wire (Peter's hands), later |

**Mapping host wall to the recording clock, and saying how good it is.** `started` (host wall at the session's start) gives `t_s = wall - started` to the accuracy of the arrival time of a block, which is up to the 0.2 s block plus the kernel's buffering (about 160 ms): **±0.3 s, not yet measured here**. That is enough to **pair** a console wake with a PPK2 wake: wakes are one beat apart (900 s on the bench RAK), so a pairing is right while the error is under half a beat, 450 s against 0.3 s. Inside a wake it is not enough, so:
- a line that carries its own offset from the wake's start on the device clock (the RAK prints `LC <phase> +<ms>`, and `SLEEPNOW ... awake=<ms>`) is placed at **the PPK2's wake start plus that offset**, plus one constant `c`, the difference between the device's idea of the wake start and the PPK2's rising edge. `c` is **measured, not assumed**: the median over the wakes of a recording, shown with its spread. The firmware session expects tens of ms and has not measured it;
- a line with no offset is placed by host wall and drawn with its ±0.3 s;
- a logic pin, when there is one, is exact and replaces `c` for that wake.
The cursor always says which of these placed a line and how far to trust it. **A line is never drawn more precisely than its source allows.**

**What a capture keeps** (one recording, one folder): the 0.2 s rows (all of them), the full-rate wakes (newest 20), the logic bits, the console lines of the recording's span, the supply facts, the events and phases, the clock anchors it measured, and **where its profile and its firmware came from** (`profile: {project, role, source}` from `project-power-profile.md`, and the stored package id of the role's firmware from the project's manifest), so two recordings can be compared by firmware without anyone typing a note.

## Phases: cut by current level

A phase is a stretch above or below a level. The profile (`project-power-profile.md`, extended) names the level and the phases:

```yaml
power:
  alarm:
    phases:                         # optional; cut the wake at this level, named by order
      level_ma: 50
      names: [open_tx, receive, close_tx]      # above, between, above; any stretch above beyond these is extra_tx
```

Derived from the bench RAK (the firmware session, #64): first and last stretch of a wake are TX at 118 to 121 mA, receive between at about 6.5 mA with spikes to 25 mA, so 50 mA separates them. **An order rule can mislabel a wake with a retry**, so every wake reports its **count of stretches above the level** and is flagged when the count is not `len(names) - 1` stretches above (3 phases: 2 above). A device with no sleep needs no phases at all; events from the recording start do the same job (`project-power-profile.md`, decision 2).

## Commands

| command | effect | what it does |
|---|---|---|
| `meter.at` `recording`, `wall`? or `t_s`?, `span_s`? | read | everything true at that instant: supply state, the current (lowest, mean, highest), the phase and event it falls in, the console lines within `span_s` (default 1 s) with how each was placed and its uncertainty, and the wake it belongs to. The same call the page's cursor makes |
| `meter.wake_table` `recording`, `level_ma`? | read | one row per wake: number, wall start, `awake_s` (first block over the awake level to the last block over it), `capture_s`, charge, mean, peak, time above the level, count of stretches above it, `resolution` (`full` or `0.2s`), `sleepnow_gap_s` (the PPK2's `awake_s` minus the `awake=` the firmware printed in its `SLEEPNOW` line for the same wake: near zero on a normal wake, and several seconds means current was drawn after the firmware said it slept, so it also gives `c` a second way; `null` with no console line), `marks` (the names of any `meter.mark` stamps that fall inside the wake, so a wake where a command was sent shows as one without reading the clock), and `outlier` (distance from the recording's median in robust units; the flag is the rule in the spec's checks, not a typed number). Both new fields are the firmware session's (#67) |
| `meter.life` `recording`, `capacity_mah`, `volts`? | read | the beat period, charge per beat, average current, and days at that capacity; current-based (a battery's voltage curve is not modelled, said in the answer). The page shows this number and computes none of its own |
| `meter.mark` `name` | write | stamps NOW on the running recording's clock as an event (or the start of one, closed by a second call), so a command sent to the board is marked with no typed time |
| `meter.compare` `a`, `b` | read | per phase: charge in each recording and the difference, and the firmware package each ran |
| `console.history` `link`, `from_wall`, `to_wall` | read | persisted lines of a link between two host times; survives a restart |
| `meter.record` gains `stop_after_wakes` | write | see "Capture" |

`meter.wake_table`, `meter.life` and `meter.compare` read the 0.2 s rows, and the full-rate wakes where held, so **every wake of a long recording gets a row**; only the newest 20 have `resolution: full`.

## Console persistence

Lines are appended to `<paths.console>/<link>/<date>.jsonl`, `{wall, offset, text}` per line, as they arrive, so a restart loses nothing already received. **The bound has no default** (the rule for every stream here, `core.md`): `streams.console_log.max_mb` per link is required; the oldest file goes first; without it the feature is absent and the page says why. Lines are kept whole (the `SLEEPNOW` line is long and is wanted in full). It only exists while the debug wires are attached; a recording with and without the wire is how to see whether the wire changes the sleep floor (the firmware session has not measured that).

## Capture: stop on the clock, or on the board

**Stop after** is one of: **Wakes** (N whole wakes, each from its start to its end), **Time** (a typed length in seconds, minutes or hours), **Never** (until Stop). A capture ends at the first of its condition and Stop. *Constraint: Peter, "might be time based, it may be event based".*
- **Wakes, N.** `meter.record` with `stop_after_wakes` starts recording at once (the sleep before the first wake is part of the recording: the sleep floor is wanted too), and ends the session when the Nth **counted** wake has **closed** (its tail included). The count is event-driven off the capture's own close, which the session already publishes (`facts.captures`); nothing polls. With no sleep level yet, the first 4 s find it (`ppk2-stages.md`) unless the project profile gives it.
- **A wake already in progress when the recording starts does not count** (the firmware session, #68). Only a wake whose **rising edge the recording saw** is counted, so every counted wake is whole: a capture that opens with no sleep block before it is marked `partial` and is kept but not counted.
- **No wake period is assumed, anywhere** (Peter, 2026-11-21, on a `beat_s: 900` the service had put in the bench RAK's manifest: "you have assumed that 15 minutes is a fixed window size. that is NOT how a wake window is detected"). A wake is found from the current (the trigger's levels, hold and longest wake), never from how often a board wakes: boards wake on a button, a sensor, a radio frame, at irregular times, or never sleep. An earlier draft of this section derived the give-up from a beat, `(N + 1) x B`, with B from a profile field or learned from two edges; that is withdrawn, and `beat_s` is not a manifest field (register: `a-profile-assumes-wakes-come-on-a-fixed-period`).
- **The give-up is a ceiling the person gives, or 12 h.** With `stop_after_wakes` the session ends at the Nth counted wake or at `bound_s`, whichever comes first; `bound_s` is optional there and defaults to the 12 h maximum (43,200 s, `core.md`), and the page says "gives up after 12 h" rather than hiding it. It is a ceiling for the failure case (no wake ever comes), never a wait.
- **A counted wake is whole at both ends** (amended 2026-11-21, task `gap-stop-after-wakes`, from the code: `Capturer`). A capture is `partial` when the block before its first awake block was not judged asleep: the recording opened on it, no level was known yet, or the capture continues a wake the longest-wake cut ended. A partial capture is kept and not counted; a `truncated` one (cut at `max_s`) is kept and not counted either, since it has no end. `Capturer.whole` counts only closed captures that are neither. The save to disk carries `partial` so a replay says it.
- **N has a derived limit**: `stop_after_wakes` is a whole number from 1 to `MAX_BOUND_S / BLOCK_S` (a wake is at least one 0.2 s block, so 12 h cannot hold more than 216,000); outside that the call is refused naming `stop_after_wakes`. A `bound_s` reached before N wakes is `gave_up` (with `wakes` of `stop_after_wakes` seen), not `time_reached`.
- **How a recording ended is always said** (`ended_by`, in the session, the sidecar and on the card; `ended` is already the sidecar's wall time): `wakes_done`, `time_reached`, `gave_up` (with `wakes` of `stop_after_wakes` seen), `stopped` (by hand), or `error` with its reason in `reason`. Yesterday's `device-wholewake-1` ended "gone silent" and nobody could tell why; a session that ends must name the cause. `ended_by` is `null` while it runs.
- **Never** (continuous) is available now: it runs until Stop or the 12 h file limit (`bound_s` 43,200; reaching it is `time_reached`). A rolling window that keeps only the newest length, so it could run longer, is a later improvement and not needed for this.
- Watch (no file) takes the same choices.

## The page

Layout A of the mockup: the supply strip always on screen; under it the scope with its readouts; **a Console lane on the same time axis**; the overview; the controls; on the right the Capture card (Stop after: Wakes, Time, Never) and **Events and console** (a list of either, paged, never scrolled). **Nothing scrolls.** The cursor follows the pointer over the scope or the lane; its readout is one line: time, current, phase, the nearest earlier console line and how long before. Clicking pins it; clicking a console line in the list pins the cursor at that line. A wake-table view and a compare view are the next tabs of the same page, not separate pages (they come after the one above).

## Build order (each is its own task, derived from what needs what)

1. **Console persisted** (`console.history`): nothing downstream works without the lines.
2. **`stop_after_wakes`** and the Capture card.
3. **`meter.wake_table`** with `awake_s` and `capture_s` apart (this also ends the 21.6 / 22.6 disagreement and brings the cycle table onto the wake profile).
4. **Phases** in the project profile (`project-power-profile.md`, the Task 1 build), then phases in the table.
5. **`meter.at`**, the clock mapping with its measured `c`, and the page's console lane and cursor.
6. **`meter.life`**, **`meter.mark`**, **`meter.compare`**.
7. The logic lane, when a firmware pin and a wire exist (Peter's hands).

## Not known yet, and measured before it is trusted

- **The host arrival offset** of a block (assumed under 0.3 s): measure with a known edge.
- **`c`**: the device's wake start against the PPK2's edge.
- **Whether the debug wires change the sleep floor.**
Each is a MEASURE step in the task that depends on it; none is assumed in the code.

## Checks

Unit: `meter.at` returns the right lines for a synthetic recording with a known console (placed by offset and by wall, each labelled); the pairing survives a wake with no console line; `c` is the median of per-wake offsets and the spread is reported; `awake_s` and `capture_s` differ by the lead-in and tail and `awake_s` agrees with the cycle table on a synthetic cycle; the stretch count flags a wake with a mid-window burst; `stop_after_wakes=2` ends the session after the second wake closes and not on the first (and a third is not recorded); console lines survive a restart and the bound drops the oldest file first. Mutants (each caught): the lead-in counted in `awake_s`; the order rule with no count flag; pairing by index instead of by time; `c` taken as zero; a session that ends on wake open instead of wake close.
Browser (both themes, 1280×720, 1440×900, 390×800): no page scrolls; the cursor readout names the phase and the nearest line; pinning from the list moves the cursor; the Capture card changes its fields with Stop after.
Live, measure-only (no mode or supply change): a one-wake recording ends by itself; the console of that recording is on disk after a service restart.

## Register

`console-lines-are-not-kept-past-a-restart`, `the-capture-card-cannot-stop-on-wakes`, `a-wakes-length-includes-its-lead-in-and-tail`, `the-cycle-table-uses-its-own-wake-rule`, `no-command-gives-battery-life`, `no-per-wake-table`, `nothing-says-what-was-happening-at-a-moment`.

source_hash: pending

# Wake stages: capture a wake at full rate, mark and name its stages

Peter, 2026-11-20: "when measuring this there are several things we are interested in, as well as the battery life, we are also interested in the sleep and wake period durations and power consumed during those periods".
Then: "often has stages, such as a CPU wake and then a radio burst - exactly, we are also interested in examining the wake cycle and see what power was being consumed against time and how that associates with what the
firmware would be doing at that stage. so it would probably need the ability to set markers and name them ?" and "go".
Extends `ppk2.md` and `meter-page.md`; where they differ this one is later.

## Why the page cannot do it today

A session's envelope is 0.2 s per point (the profiler's block, the multimeter's period). A 30 ms CPU wake followed by a 60 ms radio burst is one point, and a linear axis cannot show 10 µA asleep, mA for the CPU and tens
of mA for the radio together. So the single wake must be kept **at the unit's full 100,000 samples a second**, on the Pi, and drawn on a log axis. The multimeter cannot do this: stages are a profiler feature only.

## The plan, in the order it is built

| stage | what | needs |
|---|---|---|
| 0 | the Sleep and Wake table from the envelope (`meter-page.md`, "The cycle table") | done |
| 1 | **capture each wake at full rate on the Pi, and show one as a scope trace (this spec, below)** | the profiler |
| 2 | named markers on a wake and a stage table across the captured wakes | stage 1 |
| 3 | markers from the profiler's 8 logic inputs: the firmware toggles a spare GPIO at each stage and the markers place themselves to 10 µs | stage 1, a wire from the board to the unit (Peter's hands) |
| 4 | console lines from the debug UART overlaid at their arrival time (good to tens of ms, so for long phases, not radio edges) | stage 2 |

Stages 2 to 4 are designed here only to the point that stage 1 does not close them off: a capture keeps the whole raw word (the logic bits are in it) and times are on the unit's sample clock, relative to the wake's start.

## Stage 1: capture

**What a capture is:** the raw 4-byte words of one wake, from the block before it started to the first quiet block after it, with where it began on the unit's sample clock. Raw words keep the range, the counter and the logic bits;
the current is decoded from them on request with the unit's calibration, exactly as the session's blocks are.

**When one is taken.** On the Pi, in the profiler's source, as the session runs (a watch or a recording):
- **The level.** A wake is a block whose *highest* sample is over `level = sleep + (peak - sleep) / 2`, the rule the page uses (`meter-page.md`, "Battery life"), kept as a running figure: `sleep` is the median of the block means so far and `peak` the highest sample so far.
  The median is held as a histogram of 16 bins a decade from 1 nA to 10 A (160 bins, constant memory however long a session runs); a bin is 10^(1/16) = 1.155 wide, so the sleep level is known to ±7.5 %, which moves the level by under 4 % of the gap from sleep to peak.
  There is no wake until `peak` is at least **twice** `sleep` (the page's rule: noise on a steady load is a few per cent, a radio burst is a thousand times).
- **The window.** The last 5 blocks are always held. When a block goes over the level, the capture starts with the blocks before it that were already awake (a mean over twice the sleep level: a CPU up before its radio) back to and including the first quiet one, at most 5 (1 s of lead-in, 400 KB of ring),
  so a wake starts from sleep and not part-way through; it takes every block while the level is exceeded, and ends with the first quiet block after (0.2 s of tail). A stretch that is awake but under the level, such as the 20 s a RAK spends at 6.6 mA between its two radio bursts, is not in
  a capture: the session's chart has it, at 0.2 s a point.
- **The ceiling:** a capture is at most **25 blocks (5 s)**: 25 × 20,000 words × 4 bytes = 2 MB. A wake still going at 5 s is closed as `truncated` and the next one is looked for once it falls quiet.
- **How many are kept:** the newest **8**, at most 16 MB in memory: enough to compare wake with wake and see whether a stage drifts, about 1 % of the bench Pi's memory. They live in memory for the session and until the next session on that unit starts;
  a recording also keeps its wakes on disk (stage 2, below).
- A wake closer than a block to the one before it simply extends that capture.
- Lost samples (the counter) are counted per capture and said; the time axis counts samples received.

**The range glitch.** The unit switches range when the current steps, and the first samples after a switch are a glitch. Measured on the bench RAK (2026-11-20, a wake that steps from 4 mA to 120 mA): two samples of 221 mA and 139 mA either side of the edge,
then the 120 mA plateau. The session's own reduction holds 3 samples out from the one where the range changes (`ppk2.md`). A decoded wake does the same: those samples take the value before them, so a wake keeps its length and its **peak, average and charge are the
session's, not the glitch's**; `held` says how many were held (3 for each range change in the wake). This is also the first real-load look at the hold-out ppk2.md called unproven: the glitch was 2 samples, inside the 3 held.

**Time.** A capture's `t0` is the sample clock (seconds since the session began) of its first sample. Its **wake start** is the first sample over **half of its own highest sample** (after the glitch hold), found when it is decoded, and every time shown to a person is
relative to that (negative before it), so the same stage is at the same time in every wake: it is the edge of the biggest burst. It is not the level the capture was triggered at, which rises as the session's peak does: on the RAK the first wake was triggered by its CPU phase (9 mA),
so its trigger level was 4.5 mA and its origin 0.2 s before the radio edge, while the second wake's was on the edge. A device whose bursts are of different heights is lined up on each wake's own tallest.

**Commands** (read; need the readings stream like the other session commands):

| command | args | answer |
|---|---|---|
| `meter.captures` | none | `{session, captures: [{n, t0, wake_at_s, duration_s, samples, lost, truncated, peak, mean}]}`, oldest first; empty for a multimeter session or none yet |
| `meter.capture` | `n`, `t0`? `t1`? (seconds from the wake start, default the whole capture), `points`? (default 1000, 2 to 2000) | `{n, wake_at_s, t0, t1, points, buckets: [{t0, t1, min, max, mean, n}], charge_c, mean, peak, samples, lost, truncated}` |

`meter.capture` is an **exact** envelope of the window: each bucket's lowest, highest, mean and count are from the decoded samples (a zoom asks again for a finer one; nothing is thinned). Times in the answer are from the wake start.
`points` defaults to 1000 (a wide chart's pixels) and is at most 2000, the envelope's own bound. A window outside the capture is clipped; an empty one is a usage error. `n` not held is the refusal `capture_unknown`.

The sample feed carries `facts.captures` (how many wakes have been **finished and kept** so far, saved to disk first when the session is a recording), so a page knows when there is a new wake it can read, without polling.

## Stage 1: the scope view

Under the session chart, when wakes were captured (profiler only):
- a **choice of wake** (the held captures: "wake 5, 0.4 s, peak 21 mA");
- the wake as the same band chart (lowest to highest per point, the mean as a line), **time from the wake start** along the bottom, in ms or s;
- a **log axis** switch for the current: asleep at µA and awake at mA need it; it is off by default, as the linear trace shows the radio's shape. On the log axis zero cannot be drawn, so the floor is the lowest sample, said under the chart;
- **drag across the chart to zoom** to that stretch (a new `meter.capture` for the window, so it is full resolution), and a Reset;
- the figures of the window: length, average, peak, charge.
With no capture it says why: no wake found yet (a steady load, or fewer than twice its sleep), or the multimeter.

## Stage 2: named stages, kept with the recording

Peter, 2026-11-20: "the ability to set markers and name them", and then, going away: "you can actually profile the connected RAK and analyse and tag the wake cycle".

**Wakes are kept with a recording.** A recording (not a watch) writes each wake to `<recordings>/<name>.wakes/` as it closes: `<n>.bin` (the raw words, zlib level 1: about 3.5 times smaller, measured: the 6-bit sample counter still changes every word) and `<n>.json` (its t0, level,
lost, truncated, peak), plus `cal.json` (the calibration the unit gave, so a wake decodes without the unit). The **newest 20** are kept per recording: the stage table needs the spread across wakes and 20 shows it; worst case 20 × 2 MB = 40 MB raw
before compression, and the recordings folder keeps the newest 10 recordings (deleting a recording deletes its `.wakes/` and `.stages.json`). So `meter.captures` and `meter.capture` take an optional `recording`: the wakes of a finished recording,
read from disk; without it, the last session's, from memory as before. A wake that closes while the disk write fails is still held in memory and the failure is said in the session's facts (`wakes_unsaved`).

**A stage** is `{name, start_s, end_s}`: a stretch of a wake in seconds from the wake's start (negative is before it), so it means the same thing in every wake. They are stored per recording in `<name>.stages.json`.
Rules: a name is 1 to 40 characters and unique ignoring case; `start_s` < `end_s`; stages may not overlap (the shares would not add up: `bad_stage`); at most **12** (a wake has a handful of stages: more would not fit a table a person reads).
Times are kept as given. Stages are saved only against a recording (the page says to record to keep them).

| command | args | effect |
|---|---|---|
| `meter.stage_set` | `recording`, `name`, `start_s`, `end_s`, `every`?, `offset`? | write: add the stage, or change the one of that name |
| `meter.stage_delete` | `recording`, `name` | write: remove it; `stage_unknown` if there is none |
| `meter.stages` | `recording`, `volts`? | read: the stage table over that recording's saved wakes |

**Wakes of different kinds.** The bench RAK alternates two shapes of wake (found 2026-11-20): a beacon wake (CPU ramp, setup, TX 1, then the RX window) and a second-send wake (the end of the RX window, TX 2, shutdown). One set of stage
boundaries cannot describe both, and averaging them gives a 6.5 mA wake and a 0.02 mA one as one stage. So a stage may apply to only some wakes: `every` (1 to 8, default 1) and `offset` (0 to every - 1, default 0) select the wakes whose capture number `n`
has `n % every == offset`. `every` 1 is every wake (what a stage was before); for the RAK, `every` 2 with `offset` 1 is the beacon wakes (1, 3, 5 ...) and `offset` 0 the second-send wakes (2, 4, 6 ...). Capture numbers only rise (a recording keeps the newest 20 but
their numbers stay), so the pattern does not shift as old wakes are dropped; a recording that happened to start on the other kind has the offsets the other way round, and the table says which wake numbers each stage used so that is visible. This is by number, which suits a
device that alternates and is an accident of its firmware; grouping wakes by their shape is not built. The overlap rule applies only between stages that can apply to the same wake: two selectors `(e1, o1)` and `(e2, o2)` can both hold for some `n` exactly when
`(o1 - o2)` is a multiple of `gcd(e1, e2)`, and only then are overlapping times refused.

**The stage table** (`meter.stages`): for each stage, over every wake it applies to, among the held ones (up to 20): in each wake the samples in `[start_s, end_s)` give a mean current, a peak and a charge; the answer gives, per stage, `mean` (of the wakes' means),
`lowest_mean` and `highest_mean` (the spread across wakes: a stage that costs the same each time has them close), `peak` (the highest sample in any wake), `charge_c` (mean over the wakes) and `share` (of the charge of the stages that apply to the same wakes), with `length_s`
(`end_s - start_s`), `every`, `offset`, `wakes` (how many contributed: a stage outside a short capture contributes nothing) and `numbers` (which wakes). `groups` lists each set of wakes that stages share: `{every, offset, numbers, stages, tagged_charge_c,
untagged_charge_c}`, the untagged charge being the rest of those wakes' capture windows (lead-in, tail, anything not named). `energy_j` per stage is charge × `volts` when volts is given. Exact: every figure is from the raw samples, not from the envelope.

**On the scope:** drag across the trace to zoom, then name the stretch as a stage (a name box, an "Applies to" choice of all wakes, odd wakes or even wakes, and an "Add as a stage" button; the stretch is rounded to 0.1 ms; the choice sets `every` and `offset`, the API takes any);
the table is in groups, each headed by the wakes it used; saved stages are shaded on the trace with their names above the shading, listed with a delete,
and the table is shown under the trace. Without a recording behind the wakes the page says stages are kept with a recording.

## Result codes (core.md rows)

| code | class | meaning |
|---|---|---|
| `capture_unknown` | refused | no capture with that number is held (the evidence lists the numbers held) |
| `stage_unknown` | refused | the recording has no stage of that name |

A stage that breaks a rule above (an empty, too long or duplicate name, a start not before its end, an overlap, a thirteenth) is a usage error naming the argument, as any bad argument is. A recording that is not there is the existing
`recording_unknown`.

## Checks

Unit (`tests/instruments/test_capture.py`): the level rule (median to ±7.5 %, nothing under twice the sleep, a steady load never triggers, noise never triggers); the window starts one block before and ends one quiet block after; a wake longer than
25 blocks is truncated and the next is found; two wakes closer than a block are one capture; only the newest 8 are kept and numbers keep rising; raw words are stored whole (compared byte for byte with what the unit sent); the decoded window equals a per-sample decode,
exactly, in every bucket, at any zoom, with the lowest and highest sample kept; the wake start is the first sample over half the wake's own peak, so a wake begun by its CPU and one that was not share the radio edge as time zero; the lead-in goes back over blocks already awake, to the first quiet one, never more than 5; the 3 samples after a range change are held out of a wake, as the session's are; the whole path over the fake unit with a wake profile gives the captures and the right peak; a multimeter session has none;
`meter.captures` and `meter.capture` over the registry, `capture_unknown`, a bad window; mutants on each rule. Browser: the scope appears under the chart, the wake is chosen, the log switch changes the axis note, a drag zooms and Reset returns, both themes, phone and desktop.
Chart maths (`tests/dashboard/test_meter_scope.py`, Node): linear puts zero at the bottom and the peak at the top; log lifts a 3 mA stage well off the floor where linear leaves a sliver, and falls back to linear with nothing above zero;
the band's top edge is the highest sample and its bottom edge the lowest; the wake start is marked only when in view; time reads in ms under a second; ticks are 1, 2 or 5 times a power of ten inside the range; a drag position maps back to a time and is held inside the chart; 10 mutants, the one
survivor being equivalent (no value exceeds the top). Browser (`scripts/verify_ppk2_live.py`): the steady load says no wake was kept; with the 2 s wake, the list, the chart's range, the dashed start, the figures, the linear and log axes, a drag to about 15 % (90 ms) and Show all of it.
**Live, on the bench RAK powered by the PPK2 (source mode, 4.0 V, Peter's word, 2026-11-20)**, two 1 h recordings (`rak-profile-1`, `rak-profile-2`; 359.9 M samples each, 0 lost, the service at 5 to 8 % of a core, 39 MB):
- **The cycle** (the page's own cycle finder on the envelope, and the CSV's 0.2 s blocks): period **900.00 s** (gaps 14:59 to 15:02 on the envelope), 4 wakes and 3 whole cycles per hour, sleep median **33.8 µA**, awake **21.6 s** a cycle, **85.1 µAh and 340.6 µA a cycle**, 341 µWh at 4.0 V,
  so **1000 mAh lasts 122 days by current** (2000 mAh 245). The awake 21.6 s is 276.6 mC, **90 %** of the charge; the 878 s asleep is 29.9 mC, 10 %. (Current at 4.0 V from the unit: not a battery's voltage, and not modelled.)
- **The stages** (8 wakes saved, odd and even alternate; `meter.stages`, 4 wakes each): beacon wakes: CPU wake and setup 0.9 mC (a 1.4 mA ramp for 25 ms, then ~8.8 mA, 115 to 285 ms from wake to wake), **TX 1 120.5 mA for 683 ms = 82.3 mC (22.9 µAh)**, then the RX window at 6.4 mA;
  second-send wakes: the end of the RX window at 6.54 mA, a 9 ms switch at 4.9 mA, **TX 2 118.5 mA for 518 ms = 61.4 mC (17 µAh)**, shutdown over 42 ms and then 23.7 µA asleep. The ~20.8 s between the two TXs at 6.56 mA (the RX window) is about **43 %** of the cycle's charge, TX 1 27 %, TX 2 20 %, sleep 10 %, the rest under 1 %.
  This matches the firmware docs (`beat` 900 s, `rx.win` 20 s on this unit per its MILESTONES); that TX 1 is the beacon is an inference from them, **what TX 2 is is not known**.
- **What the real unit taught** (each is fixed and has a test): the range switch puts a glitch of 221 mA and 139 mA in two samples at a 4 mA to 120 mA edge, which the capture now holds out; the first wake's trigger level (4.5 mA, from its CPU phase) put its time origin 0.2 s before its radio edge while the second's was on it, so the origin is
  now each wake's own half-peak; the lead-in of one block cut a long RX run-in, so it now goes back over awake blocks (at most 5); the stage table needs to know wakes of two kinds (`every`/`offset`).
- **Not proven:** that every cycle looks like these four (4 were seen in each hour, 8 in all); the glitch hold-out of 3 samples is checked on one kind of edge only; stages by number rather than by shape; nothing here is a battery measurement.

## Files this spec allows

`nrf_workbench/instruments/{capture,ppk2,sampler,meter}.py`, `nrf_workbench/core/status.py`, `docs/specs/{ppk2-stages,ppk2,meter-page,core}.md`, `ui/src/MeterPage.jsx`, `ui/src/meter/*`, `ui/src/help.js`,
`tests/instruments/`, `tests/dashboard/`, `tests/devices/fakes.py`, `scripts/verify_ppk2_live.py`.

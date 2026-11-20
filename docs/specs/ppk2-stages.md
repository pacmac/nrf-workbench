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
- **The window.** The last block is always held. When a block goes over the level, the capture starts with the block before it (0.2 s of lead-in, since the wake began somewhere inside the block that revealed it), takes every block while the level is exceeded,
  and ends with the first quiet block after (0.2 s of tail).
- **The ceiling:** a capture is at most **25 blocks (5 s)**: 25 × 20,000 words × 4 bytes = 2 MB. A wake still going at 5 s is closed as `truncated` and the next one is looked for once it falls quiet.
- **How many are kept:** the newest **8**, at most 16 MB in memory: enough to compare wake with wake and see whether a stage drifts, about 1 % of the bench Pi's memory. They live in memory for the session and until the next session on that unit starts;
  saving them with a recording, so they survive a restart, is later (stage 2 needs it).
- A wake closer than a block to the one before it simply extends that capture.
- Lost samples (the counter) are counted per capture and said; the time axis counts samples received.

**Time.** A capture's `t0` is the sample clock (seconds since the session began) of its first sample. Its **wake start** is the first sample over the level, found when it is decoded, and every time shown to a person is
relative to that (negative before it), so the same stage is at the same time in every wake.

**Commands** (read; need the readings stream like the other session commands):

| command | args | answer |
|---|---|---|
| `meter.captures` | none | `{session, captures: [{n, t0, wake_at_s, duration_s, samples, lost, truncated, peak, mean}]}`, oldest first; empty for a multimeter session or none yet |
| `meter.capture` | `n`, `t0`? `t1`? (seconds from the wake start, default the whole capture), `points`? (default 1000, 2 to 2000) | `{n, wake_at_s, t0, t1, points, buckets: [{t0, t1, min, max, mean, n}], charge_c, mean, peak, samples, lost, truncated}` |

`meter.capture` is an **exact** envelope of the window: each bucket's lowest, highest, mean and count are from the decoded samples (a zoom asks again for a finer one; nothing is thinned). Times in the answer are from the wake start.
`points` defaults to 1000 (a wide chart's pixels) and is at most 2000, the envelope's own bound. A window outside the capture is clipped; an empty one is a usage error. `n` not held is the refusal `capture_unknown`.

The sample feed carries `facts.captures` (how many were taken so far), so a page knows when to ask again without polling.

## Stage 1: the scope view

Under the session chart, when wakes were captured (profiler only):
- a **choice of wake** (the held captures: "wake 5, 0.4 s, peak 21 mA");
- the wake as the same band chart (lowest to highest per point, the mean as a line), **time from the wake start** along the bottom, in ms or s;
- a **log axis** switch for the current: asleep at µA and awake at mA need it; it is off by default, as the linear trace shows the radio's shape. On the log axis zero cannot be drawn, so the floor is the lowest sample, said under the chart;
- **drag across the chart to zoom** to that stretch (a new `meter.capture` for the window, so it is full resolution), and a Reset;
- the figures of the window: length, average, peak, charge.
With no capture it says why: no wake found yet (a steady load, or fewer than twice its sleep), or the multimeter.

## Result codes (core.md row)

| code | class | meaning |
|---|---|---|
| `capture_unknown` | refused | no capture with that number is held (the evidence lists the numbers held) |

## Checks

Unit (`tests/instruments/test_capture.py`): the level rule (median to ±7.5 %, nothing under twice the sleep, a steady load never triggers, noise never triggers); the window starts one block before and ends one quiet block after; a wake longer than
25 blocks is truncated and the next is found; two wakes closer than a block are one capture; only the newest 8 are kept and numbers keep rising; raw words are stored whole (compared byte for byte with what the unit sent); the decoded window equals a per-sample decode,
exactly, in every bucket, at any zoom, with the lowest and highest sample kept; the wake start is the first sample over the level; the whole path over the fake unit with a wake profile gives the captures and the right peak; a multimeter session has none;
`meter.captures` and `meter.capture` over the registry, `capture_unknown`, a bad window; mutants on each rule. Browser: the scope appears under the chart, the wake is chosen, the log switch changes the axis note, a drag zooms and Reset returns, both themes, phone and desktop.
Live, when a board with a repeating wake is wired: a capture of a real wake and its shape (not possible with the LED alone).

## Files this spec allows

`nrf_workbench/instruments/{capture,ppk2,sampler,meter}.py`, `nrf_workbench/core/status.py`, `docs/specs/{ppk2-stages,ppk2,meter-page,core}.md`, `ui/src/MeterPage.jsx`, `ui/src/meter/*`, `ui/src/help.js`,
`tests/instruments/`, `tests/dashboard/`, `tests/devices/fakes.py`, `scripts/verify_ppk2_live.py`.

# A Meter page: power over hours, live readings, a chart, recordings

Peter, 2026-11-19: "why does the dashboard not have anything for the meter control / readings / charts etc etc ?", "I was expecting a new Menu, not just a card !", and then:
"full control of the meter would be nice, but at this stage I am primarily interested in power consumption. now it is limited to 30 seconds I believe, but it would be nice if that could
be longer so that we can potentially monitor sleep and awake cycles of a device. Although that said, the NRF power profiler might be a more accurate tool for that, but I dont have that yet."
Extends `meter.md`; where they differ this one is later. Sessions can also read the Nordic Power Profiler (`ppk2.md`, later still): `meter.watch` and `meter.record` take a `source`, the CSV gained `min,max,n`, and the page has a switch.

The first build is a card that asks only on a button press: at most 150 readings (30 s). Peter's own use shows the need: Activity, 09:44 and 09:45, `meter.sample` read 7.17 mA, then 0.102 mA:
a board going to sleep. A node that beats every 900 s needs minutes to hours.

## Decisions (made on Peter's words, stated so they can be undone)

1. **Power over time first, control later.** The page shows the meter's setting and says to set function, range and integration time at the front panel. `meter.configure` (changing them from
   software, which can cut a board's supply if the meter is in series) is **deferred**: Peter called it "nice", and its write syntax could not be tried on the live meter. Its design is kept at the end.
2. **A menu page, not a card.** Menu item **Meter**, present only when the service has the meter. The Home and Devices cards stay for a quick check and link to the page.
3. **The meter is the stop-gap.** The Nordic Power Profiler (PPK2, `park-ppk2-power-profiler`) samples at about 100 ksps; this meter takes one reading per integration period (0.2 s at NPLC 10) and
   **averages** what happens inside it. So it shows sleep and awake *levels* and cycles of seconds or more, and cannot see a radio burst of a few milliseconds. The page says so.

## Sessions: watch and record (one at a time)

A **session** is one sampler thread on the Pi that takes a reading every period and publishes it. The browser only listens: no polling anywhere.

| command | effect | what it does |
|---|---|---|
| `meter.watch` `bound_s` | write | a live session for at most `bound_s` seconds; nothing is written to disk |
| `meter.record` `bound_s`, `name`? | write | the same, and every reading is appended to `<paths.meter>/<name>.csv` (name defaults to the start time), flushed per reading so a killed service loses nothing already taken |
| `meter.stop` | write | ends the session now; already stopped is a success |
| `meter.session` | read | `{state: idle/watching/recording/error, mode, session, name, started, elapsed_s, bound_s, reason, stats}` |
| `meter.profile` | read | the session so far as an **envelope** (below) plus the live feed cursor to resume from: `{session, cursor, epoch, unit, function, buckets, stats}` |
| `meter.recordings` | read | the files, newest first: `{name, bytes, readings, started}` |
| `meter.wake_set` `wake_*`? | write | changes the running power-profiler session's trigger (what counts as a wake) from its next block; `meter_idle` when nothing runs (`ppk2-stages.md`, "The wake profile") |
| `meter.replay` `recording`, `t0`?, `t1`?, `points`? | read | any stretch of a recording as an exact envelope on its own clock, with the stretch's figures (see "Live and replay") |
| `meter.recording_delete` `name` | write | deletes a recording with its sidecar, saved wakes, stages and events; `meter_busy` while it is being recorded |
| `meter.event_set` `recording`, `name`, `start_s`, `end_s`, `was`? / `meter.event_delete` / `meter.events` `recording`, `volts`? | write / write / read | named stretches of a recording (see "Events") |
| `meter.recording` `name` | read | the file's text (`csv`), for download; `recording_unknown` for a name that is not a file here |

`bound_s` is **required** (nothing runs unbounded, core.md): **1 to 43,200 s (12 h)**. A CSV line is about 60 bytes and a reading arrives every 0.2 s at NPLC 10, so 12 h is 216,000 lines, about
13 MB. The newest **10** recordings are kept (about 130 MB at most); older ones are deleted when a new recording starts. A second session while one runs is refused `meter_busy`.

**One reading** is one `READ?`; the setting (function, range, NPLC) is re-asked every 10 readings (about 2 s at NPLC 10) so a front-panel change shows within a couple of seconds, without 4 extra queries
on every reading. Each reading is opened, asked and closed on its own connection under the meter's one-caller lock, so `meter.show` can still run between two readings. An overload reading
(>= 9.9e37) is published and written with `overload` set and no value, and is left out of the statistics. A meter that goes silent or unreachable **ends the session** (`state: error`, the reason, the file
kept as far as it got): a session never carries on past a fault. The service stopping stops the session.

**Time.** Each reading carries `t_s` (seconds since the session began, the host's monotonic clock at arrival, as for `meter.sample`) and the wall time. The meter stamps nothing.

**Statistics**, kept as the session runs: `readings`, `min`, `max`, `mean`, and for a current function `charge_c` (coulombs = amp-seconds, by the trapezoid rule on the readings) and `charge_mah`
(= coulombs / 3.6). The mean over a whole sleep/wake cycle is the figure a battery life comes from; the page turns it into hours for a capacity the person types.

**The envelope** keeps a whole session in bounded memory: a list of buckets `{t0, t1, min, max, mean, n}`. It starts with one reading per bucket; when it reaches **2,000** buckets, neighbours are merged in
pairs (min of mins, max of maxes, weighted mean) and the bucket width doubles. 2,000 buckets is about twice the pixels of a wide chart, so nothing is thinned below what can be drawn; 12 h at 0.2 s (216,000
readings) ends in buckets of 128 readings, and a 5 ms spike still shows as a band edge. It is exact (a merge loses nothing it reports). The page keeps the same structure from the feed and starts from `meter.profile`.

**Live feed** `meter` (on the existing websocket; `streams.meter` with both bounds and no default): events `meter.reading` and `meter.state`, so a page resumes by cursor like the others. On the bench
`retain: 20000` readings (67 minutes at 0.2 s) and `max_age_days: 1`. Without the bound the feed is absent, sessions are absent, and the page says why.

CSV columns: `t_s,wall,value,unit,function,range,nplc,overload`.

## Result codes (core.md rows; the test reads both)

| code | class | meaning |
|---|---|---|
| `meter_busy` | refused | a session is running (what is running is in the evidence), so a second one cannot start |
| `recording_unknown` | refused | no recording has that name |

(A recording name that already exists is the existing `name_taken`.)

## The page (Meter)

Phone and desktop, both themes, no timers. From the top (**reworked 2026-11-20**, "Live and replay" below): **the Scope card**, which is either **Live** or **Replay: <recording>** (two buttons in its header);
in Live: **the live figure**, large, with its unit; the session line ("recording, 00:04:12 of 10:00, 1,260 readings"); **Watch / Record / Stop** with a duration choice (1 min, 10 min, 1 h, 4 h, 12 h) and a name for a
recording, and **Replay <name>** once a recording has ended; then **the trace** with its controls and **the figures** (lowest, highest, average, and for current the charge in mAh, of the stretch shown);
**battery life** (a capacity in mAh gives "at this average it lasts N days") and the cycle table. In Replay: the trace over the whole recording with its controls, the overview strip, the figures, and **Events**.
Below: **Stages across the wakes** (a collapsed section: each saved wake at full rate, and stages across wakes), **Recordings** (each with **Replay**, **Save CSV** and **Delete**, which asks first), and **the
instrument's settings** (the meter's setting in words, or the profiler's mode and supply), collapsed. A failure is shown as the whole result with its code. The note about averaging (decision 3) is on the page.

## Bench layout (2026-11-21): two instruments, the window, no scrolling

Peter, 2026-11-21: "dashboards dont scroll", "the power profiler is 2 bench devices: 1) a power supply and 2 a current monitor", "the old multimeter connection should be removed", and, of the Capture card, "ONLY allows fixed periods to be selected, not very practical". Layout A of the mockup (`https://claude.ai/artifact/YCj46YmdfVRabAPysba9TW`), built; supersedes "The page (Meter)" where they differ.

- **The page owns the window** from 768 px up (`navigation.md`, "Full-height pages"): the Supply strip, then the Current monitor filling the rest. Nothing scrolls, and nothing is cut off: every control lies inside the page. A phone keeps the old stacked flow until it has its own layout (not built; it scrolls).
- **Supply** (always on screen, one slim row): the unit's state in words with a lamp, the voltage large, a typed voltage with steps of 100 mV, Output Off/On, and Source/Ampere. The unit does not say whether its output is on, so On, and leaving source mode with the output on or unknown, are asked twice: the button, then a sentence saying what it will do (Switch on / Leave source mode, or Cancel); that answer is the `accept_supply` the server insists on. While a session holds the unit every control is disabled and the strip says it is held.
- **Current monitor**: a row of readouts (Now, Lowest, Average, Highest, Charge used, Battery life), the scope filling its box, the overview (a replay), one row of scope controls, the notes (lost samples, a slow stream, a negative current); in a 19 rem column beside it the **Capture** card, the **Trigger** line and the **Recordings**.
- **Capture**: Stop after is **Time** (a length typed in seconds, minutes or hours, 1 s to 12 h) or **Never** (until Stop, or the 12 h limit every capture has), and **Wakes** (N whole wakes) only when `meter.record` takes `stop_after_wakes` (`power-workbench.md`). The five fixed periods are gone. A capture has no wake period: how often a board wakes is never asked or assumed.
- **Trigger**: the line says where it comes from, "From project mt-alarm: asleep 34 µA, awake over 100 µA" (`meter.project_profile`) or "Found from the data"; Change opens the fields, which override the project's field by field.
- **Recordings** show the rows that fit the height and page (a measured list, `ResizeObserver`, no timer). Replay, Save CSV, Delete with a confirm in the row.
- **Drawers** hold what is wanted now and then and open over the monitor, closed by Escape: the trigger, battery life (capacity, whole recording or cycle, the cycle table), the stages across the wakes, the session's whole record, and a replay's events. A drawer holds a form or a table longer than the window may be and so may scroll inside itself: it is the one place a page scrolls, and it is closed unless asked for.
- **The Stages button** is offered when there is something to look at: a recording on the scope or being recorded, **or a session that has kept wakes** (`captures` above zero), a Watch included. A Watch writes nothing to disk, but the unit keeps every wake it sees at full rate for the session (`meter.captures` with no recording), and looking at one before deciding to record is what a Watch is for. With no recording the drawer lists and draws the wakes, says stages are kept with a recording, and offers no way to name one. A session that kept no wake, or none run, offers nothing. The first 4 s of a session are spent finding the sleep level (20 blocks), so a wake earlier than that is not kept. Check: `scripts/verify_watch_wakes.py`.
- **The multimeter is not on this page.** The page needs the profiler (`ppk2.show`); a bench with only a multimeter has no Meter page.
- **Not built here**: the console lane and the cursor that follows it, the wake table, `Wakes` on the card (these need `power-workbench.md` steps 1 to 5).

Checks: `scripts/verify_meter_layout.py` (a real browser over the fake PPK2 bench, both themes, 1280x720 and 1440x900, idle and with a watch running): the document and the page do not scroll, every control lies inside the page, the supply strip is one slim row, the scope keeps at least 200 px, the page uses the width beside the nav, a length can be typed, Stop after is Time and Never, no multimeter.

## Live and replay: one scope (2026-11-20)

Peter, 2026-11-20: "there is no way to delete a recording. and shouldn't Download be 'Replay'. this entire page layout is not good. the scope view should be either the live data, or should be a replay.
when a replay is loaded it should show the whole time period, you should be able to zoom in and out and forward / reverse through the whole recording, just as you would do on an oscilloscope."
Then: "the Y axis has no scale, and I cannot mouse over the trace to see what the XY values are."

**One scope, two modes.** *Live* shows the running (or last) session; *Replay* shows a recording, from its first reading to its last. Both have the same controls and the same trace. A person never has two
charts of one thing to compare: the wake at full rate is the deepest zoom of the same scope, not a second chart.

**The trace** has a **labelled vertical axis** (round values in the unit, linear from zero or logarithmic), a **labelled time axis** (m:ss or h:mm:ss for minutes and hours, seconds, then milliseconds as it
zooms), and a **cursor**: move over the trace (or touch it) and a line follows the pointer and a readout above the chart gives its time and the lowest, average and highest reading of the point under it and how long
each point is.

**Controls**: *Whole*, *Back* and *Forward* (half a screen), *Zoom out* and *Zoom in* (twice), a drag across the trace to zoom to that stretch, ctrl + wheel (or a trackpad pinch) to zoom about the pointer, and
keys with the chart focused (← → + − 0). Under the chart a strip shows the whole recording with the stretch shown lit; pressing on it moves the stretch there. A line says what is shown ("0:14:20 to 0:16:00,
1 min 40 s of 1 h"). The lowest, highest, average and charge under the chart are those of the stretch shown, not of the recording.

**What a replay can show** (bounded by what was kept): a recording's CSV has one row per 0.2 s block, so the whole recording zooms down to **0.2 s a point** (the floor is five points, 1 s). A stretch wholly
inside a **wake the recording saved at full rate** (the newest 20) zooms to the sample, **10 µs**, and is drawn from the wake's own samples. The strip marks the saved wakes. Keeping every sample is not built: 12 h
is 17 GB (7.5 GB compressed); see "Keeping every sample".

**Commands.** `meter.replay` (`recording`, `t0`?, `t1`?, `points`?): the exact lowest, highest and time-weighted mean of any stretch in at most `points` buckets of equal width (2 to 2000, default 1000), on the
recording's clock (seconds since it began), with the stretch's lowest, highest, average, charge and samples (each row counted for the part of it inside the stretch), the recording's `duration_s`, `unit`, `started` (wall
time), the spans of its saved wakes, and `source`: `csv` or `wake` (with its number, `resolution_s` 0.2 or 1e-5). `meter.recording_delete` (`name`): removes the CSV, sidecar, saved wakes, stages and events; refused
`meter_busy` while it is being recorded; `recording_unknown` for a name that is not there. The Recordings list has **Replay** (loads the scope), **Save CSV** (what Download was: anyone may want the file) and
**Delete** (asks first).

## Events (2026-11-20)

Peter, 2026-11-20: "rather than wake / sleep these should be defined as events, and events can be named ... we might not know what it is doing until the capture is done, then we can define events ... I would expect
to be able to assign vertical marker pairs, not by dragging along the trace, that is very difficult, but by clicking once would tag the event start and the second click would tag the second event end, those would
probably be vertical lines, and the space between them shaded and the ability to name them."

**An event is a named stretch of a recording**, `start_s` to `end_s` in seconds since it began. **When it is defined: after**, on any data. Nothing has to be known before the capture. (The trigger, the wake
profile of `ppk2-stages.md`, is the part that is set before or during a capture: it decides what is kept at full rate, and is not needed to define an event.)

**Making one.** On a replay choose *Mark an event*; the first click puts a line where it starts (orange), the second puts the end line and shades the space between; a box asks for the name (a default,
"event 1", is selected). *Esc* or Cancel drops it. Click order does not matter (the earlier click is the start). Each saved event is tinted, named above the trace, listed in a table with its **length, average
current (and lowest), peak and charge** (and energy when the supply voltage is known), and offers *Show* (zooms to it), *Rename*, *Move start*, *Move end* (arms the next click on the trace) and *Delete*.

**Measured exactly from the best data kept**: wholly inside a saved wake, from the wake's samples (the table says "from the samples"); otherwise from the readings, each counted for the part inside the event
(so an event that starts part-way through a 0.2 s row does not count the whole row). An event past the end of the recording is measured over what is there and says so; one with no readings measures nothing and is not an error.

**Rules** (`events.py`): a name is 1 to 40 characters, unique ignoring case; `start_s` is 0 or more and `end_s` at least one sample (10 µs) after it; events **may overlap** (a burst inside the wake it belongs to,
which stages may not); at most 200. `meter.event_set` (`recording`, `name`, `start_s`, `end_s`, `was`?: the event this replaces, so a rename or a move is one change), `meter.event_delete`, `meter.events` (`recording`,
`volts`?). Kept in `<name>.events.json` beside the CSV; deleted with it. New result code `event_unknown` (404, core.md).

## Battery life: the whole recording, or one wake/sleep cycle

Peter, 2026-11-20: "there should be a toggle switch for that, because if a device sleeps for a period, then wakes and sleeps again, you would want to know what the battery life would be based on that wake / sleep cycle".
The first build divided the capacity by the **average of the whole recording**. That is right only if the recording holds whole cycles: a 30 s window that misses a once-a-minute burst overstates the life, one that catches
two bursts understates it. The page now has a two-way choice beside the capacity, **Whole recording** (the Average figure, as before, and the default) and **Wake/sleep cycle**.

**The cycle estimate** is the mean current from the first wake to the last wake in the recording, so it holds only whole cycles. It is computed in the browser from the envelope (`ui/src/meter/cycle.js`), so it follows
a live session, and it is exact to within one bucket at each end (a bucket is 0.2 s for the profiler and doubles as a long session grows). It is derived like this:
1. **Sleep level** = the median of the buckets' means: a device asleep most of the time has its sleep level in the middle. (A device awake more than half the time has no sleep level to find: no cycle is found, and the page says so.)
2. **Wake** = a run of buckets whose *highest* sample is above the level halfway between the sleep level and the highest sample of the recording. The highest is used, not the mean, so a burst shorter than a bucket counts.
   Nothing is found unless the highest sample is at least **twice** the sleep level: noise on a steady load is a few per cent, a wake that does not double the draw changes the average by less than the noise, and a radio burst is a thousand times.
3. **One wake** = runs of wake closer together than half the longest gap are joined (a wake made of a CPU burst and a radio burst 0.4 s later is one wake, not two).
4. **The estimate** = the charge from the start of the first wake to the start of the last, divided by that time. Charge is each bucket's mean times the time to the next bucket, so it is exact for what was sampled. Wakes counted
   minus one is the number of whole cycles, shown. A wake still in progress at the end of the recording is left out.
5. **The period** shown is that time divided by the number of cycles. If the gaps between wakes differ by more than two buckets (each end of a gap is placed to one bucket) the page says the cycle is not regular and gives
   the shortest and longest, and the estimate still stands: it is the mean over complete intervals, whatever their pattern.

With fewer than two wakes the page says how many it found and that a cycle needs two, and shows no life. In cycle mode the chart marks each wake's start with a thin vertical line, so what was found can be seen.
This does not model the battery (a cell's voltage falls as it drains, and its capacity depends on the load): it is capacity divided by the current, as before.

### The cycle table

Peter, 2026-11-20: "we are also interested in the sleep and wake period durations and power consumed during those periods, so this probably needs to be displayed as a table as well as a chart".
With the cycle choice on, a table sits under the battery-life line, and the chart shades each wake it found (as well as the dashed mark at its start). Everything is over the same whole cycles as the estimate
(first wake start to last wake start), so **the rows add up to the cycle exactly**.

| row | time per cycle | share of time | average current | charge per cycle | share of charge | energy per cycle |
|---|---|---|---|---|---|---|
| Wake (×N wakes) | mean over the wakes, and the shortest to longest | wake time / period | wake charge / wake time | mean over the wakes | of the cycle's | charge × volts |
| Sleep | period − wake time | the rest | sleep charge / sleep time | cycle charge − wake charge | the rest | charge × volts |
| Cycle | period | 100 % | the estimate | cycle charge | 100 % | charge × volts |

- **A wake's span** is from the start of its first bucket to the start of the bucket after its last (a joined wake includes the sleep between its bursts). Its charge is each of those buckets' mean times its real time span, exact for what was
  sampled. So a wake's time is placed to **one bucket at each end**, and the bucket still holds some sleep: the table says so under it.
- **A duration is not claimed beyond that.** A wake that fills one bucket was shorter than two (it began somewhere in that bucket and ended in it or the next): the table says "under {2 buckets}". From two buckets it says "about {n buckets}".
  The wake's share of the cycle is then "under" the same bound (2 buckets / period), its average is "at least" what is shown (the bucket also holds sleep), and the Sleep row says "over" the rest, so the two never claim more than the whole.
  A radio burst of 30 ms therefore reads "under 0.4 s" at 0.2 s buckets, and the charge beside it is still exact. Exact short durations come from the capture of single wakes (`ppk2-stages.md`).
- **The wake's peak** is the highest sample in its buckets, shown beside the average.
- **Energy** is charge times the supply voltage, shown only when it is known: the profiler in source mode with its output on, at the voltage the service set. The multimeter does not know the voltage, so it shows charge only, and the table says why.
- **Units:** charge in µAh (mAh from 0.1 mAh), energy in µWh (mWh from 0.1 mWh), time in the words the page already uses. Charge in coulombs is mAh × 3.6.
- With no cycle found there is no table: the line that says why stands.

## Deferred: `meter.configure`

Set function (`dcv acv dci aci res fres freq cap`), range (a number or `auto`) and integration time from the page. Needs: the guard (while the meter is in a current function, leaving it or changing the range can open the
circuit and cut the board's supply, so it is refused `meter_in_series` unless an explicit tick is given), write-then-read-back verification (`meter_rejected` on a mismatch or an error-queue line), a whitelist of the
three write shapes so nothing else can be sent, and a trial of the real write syntax with Peter at the bench. Not built until Peter asks.

## Checks

Unit, over a fake SCPI meter on a socket (as meter.md): watch publishes readings and states in order; record writes the CSV (header, one line per reading, overload flagged); the bound ends a session; `stop` ends
it and is idempotent; a second start is `meter_busy`; silence or a refused connection ends it with `state: error` and keeps the file; the setting is re-asked every 10 readings; statistics and charge are exact
on known readings; the envelope keeps min, max, mean and count exact through every merge and never exceeds 2,000 buckets; the newest 10 recordings are kept; a name is a slug and cannot reach another path;
every line any meter command sends still ends in `?`. Feed: a client resumes by cursor, and `meter.profile`'s cursor joins it with no gap and no repeat. Mutants on each. Browser: both themes, phone and desktop,
the Meter menu item, a watch with a live figure and a chart, a recording and its download, the figures, a failure shown. Live: watch and record on the real meter in the function it is already in.

Battery life (`tests/dashboard/test_meter_cycle.py`, Node on envelopes the server's own `Envelope` builds): the mean is exact against the raw readings over whole cycles, through merged buckets and
with unevenly spaced readings (time-weighted); it differs from the window average when the window holds part of a cycle; a burst shorter than a bucket is found by the highest sample; a two-burst wake is one wake;
noise on a steady load, noise on the sleep level, one wake, a negative current, a device awake most of the time and a small rise over a 3 mA baseline each give no cycle or no false wakes, with the reason; irregular gaps are
said to be so and the estimate stands; a wake in progress at the end changes nothing; too few buckets and a bucket still filling do not crash; the chart marks only times it shows. 15 mutants of `cycle.js`, none survive.
Cycle table (same file): the Wake and Sleep rows add up to the cycle's time and charge exactly and match the raw readings; a one-reading wake is flagged unresolved with its charge and peak exact, also through merged buckets; wakes of
different lengths give the mean and the shortest to longest; a two-burst wake takes in the sleep between; a meter stall inside a wake counts in its time and charge (time-weighted); the spans are every wake; the text helpers; shades of
a visible minimum width. 12 more mutants, none survive. Browser (`scripts/verify_ppk2_live.py`): the choice starts on the whole recording, pressing the cycle shows "Wakes every 2 s (3 cycles counted)" with the average and life, the chart marks the four wakes, pressing the
whole recording drops them, no sideways scroll, both themes, phone and desktop.

## Later: the trigger and the named events come from the project (2026-11-21)

The five trigger fields are no longer only page state: a project's manifest `power` section gives a role's trigger and events, and `meter.project_profile` says which is in force.
The page's use of it, and the redesign of the page (two instruments, no scrolling, stop after N wakes), are `project-power-profile.md` ("Not in this task") and `power-workbench.md`.

## Files this spec allows

`nrf_workbench/instruments/{sampler,meter}.py`, `nrf_workbench/core/{config,events,status}.py`, `nrf_workbench/api/feed.py`, `nrf_workbench/service.py`, `ui/src/{App.jsx,Meter.jsx,MeterPage.jsx,help.js,Home.jsx,Devices.jsx}`,
`ui/src/meter/*`, `docs/specs/{meter-page,meter,core,api}.md`, `tests/instruments/`, `tests/api/`, `tests/dashboard/`, `scripts/verify_meter_live.py`, `scripts/verify_ppk2_live.py`.

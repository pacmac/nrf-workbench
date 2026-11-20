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

Phone and desktop, both themes, no timers. From the top: **the meter's setting in words** (function, range, NPLC, "in series"); **the live figure**, large, with its unit; **Watch / Record / Stop** with a
duration choice (1 min, 10 min, 1 h, 4 h, 12 h) and a name for a recording; the session line ("recording, 00:04:12 of 10:00, 1,260 readings"); **the chart** of the envelope (a band from lowest to highest and
the mean as a line, zero always on the axis, the time axis in minutes or hours); **the figures**: min, max, mean, and for current the charge in mAh; **battery life**: a capacity in mAh the person types gives
"at this average it lasts N days"; **Recordings** with a download for each. A failure is shown as the whole result with its code. The note about averaging (decision 3) is on the page.

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

## Files this spec allows

`nrf_workbench/instruments/{sampler,meter}.py`, `nrf_workbench/core/{config,events,status}.py`, `nrf_workbench/api/feed.py`, `nrf_workbench/service.py`, `ui/src/{App.jsx,Meter.jsx,MeterPage.jsx,help.js,Home.jsx,Devices.jsx}`,
`ui/src/meter/*`, `docs/specs/{meter-page,meter,core,api}.md`, `tests/instruments/`, `tests/api/`, `tests/dashboard/`, `scripts/verify_meter_live.py`, `scripts/verify_ppk2_live.py`.

# The Nordic Power Profiler (PPK2) as a second source for meter sessions

Peter, 2026-11-19: "I also have a NRF Power Profiler arriving, we will probably need integration with that later". 2026-11-20: "I have received the power profiler it is USB. is it possible to interact with that ?"
then "I have plugged it in". Extends `meter-page.md`; where they differ this one is later.

## What the device is (read from the real unit on the Pi, 2026-11-20)

- USB `1915:c00a`, product `PPK2`, serial `E1C5F15B15A3`, full speed. It enumerates **two** serial ports: interface **01** (`ttyACM1`) speaks the PPK2 protocol; interface **03** (`ttyACM2`) is a
  Zephyr shell (it answered a prompt `shell:~$`). The port is chosen by the USB interface number, never by the `ttyACM` number, and the shell port is never opened.
- Metadata query (byte `0x19`) answers a text block ending in `END`: `Calibrated`, `R0..R4`, `GS0..4`, `GI0..4`, `O0..4`, `S0..4`, `I0..4`, `UG0..4`, `VDD` (4000 = the source voltage in mV), `HW`, `mode` (2 = source meter),
  `IA`. Taken in 0.2 s.
- Start measuring (`0x06`) streams 4-byte little-endian words, **99,466 per second** over 1.007 s (100,160 words, 400,640 bytes, none left over). A word holds: ADC value bits 0 to 13, range bits 14 to 16
  (0 is the most sensitive; 0..4), counter bits 18 to 23, logic inputs bits 24 to 31. The counter ran 0,1,2,3... with **0 discontinuities in 100,159 steps**. Stop is `0x07`; 768 bytes were still in flight after it.
- Current in amps = `UG[r] * (x * (GS[r] * x + GI[r]) + (S[r] * VDD/1000 + I[r]))` where `x = (adc - O[r]) * (1.8/163840) / R[r]` and `adc` is the 14-bit value times 4 (the IRNAS `ppk2-api-python` formula; the
  Nordic app uses the same calibration block). With nothing attached the unit read **0.13 uA, spread 0.3 uA, all in range 0**.
- The protocol is from a third-party library and the unit; Nordic publishes none. Any claim here that the unit has not shown is marked **unproven**.

## Decisions (made on Peter's words, stated so they can be undone)

1. **Not a second page.** The Meter page gets a source: *Multimeter* or *Power profiler*. Watch, record, stop, the live feed, the envelope, statistics, charge, battery life and the CSV are the same machinery. One
   session at a time across both sources (`meter_busy`), because each owns the one `meter` feed. The multimeter stays as it is.
2. **Measuring changes nothing.** A session sends only `0x19`, `0x06`, `0x07` (and `0x07` before it starts, to end a stream a dead process left). Mode, source voltage and the supply switch are separate commands behind a gate (below).
3. **The unit is amps, function `CURR`.** The PPK2 reads current, so a session reports amps like the multimeter's current function, and every figure (charge, mAh, battery life, formatting) already works. The unit and function in a session
   are the PPK2's own.
4. **Reduce on the Pi, keep min and max.** 100 ksps cannot be stored or sent. A session reduces the stream to **blocks of 0.2 s = 20,000 samples** (the multimeter's own period at NPLC 10, so feed rate, retention and file size stay as
   meter-page.md derived: 5 events/s, 216,000 lines and about 24 MB for 12 h). Each block carries the **lowest, highest, mean and count of its samples exactly**: a radio burst of a few milliseconds is the edge of a band, never averaged away.
   What is lost is only *when inside the 0.2 s* it happened. (A raw capture of a burst is deferred, below.)
5. **Time is the device's sample clock.** `t_s` of a block is the samples taken so far divided by 100,000, not the arrival time, so USB jitter does not move a point. A session also reports `lost`: samples the counter field shows
   were dropped (a step other than +1 modulo 64, counted as the steps skipped). A burst of 64 or more lost words looks like no gap to the counter; the received rate against the host clock catches it: a session whose samples/s over the whole
   run is under 98,000 (2 % under the 100,000 the device sends; measured 99,466 over the first second) is flagged `rate_low`. Both numbers are on the session and in the sidecar.
6. **Range changes are filtered like the reference.** The PPK2 switches range automatically; the samples straight after a switch are not the current. The reference library holds the output for **3 samples** after a range change and so
   do we: those 3 words are left out of the statistics and counted in `filtered`. **Unproven**: with nothing attached the unit never changed range, so this is not yet seen working; it is checked against a real load in VERIFY.
7. **Reading cost is derived.** Reducing 20,000 words took 9.8 ms (mask and count unique values: the calibration is applied to the at most 5 x 16,384 distinct (range, ADC) values, not per sample), counter check 4.1 ms,
   logic 2.7 ms on this Pi, worst case on random data: about 17 ms per 0.2 s, **8.5 % of one core**. Reads are 0.2 s of data at a time; the kernel's tty buffer (64 KB, 160 ms of stream) is well above the 17 ms the thread is away.
8. **Source mode and the supply switch are gated.** In source mode the PPK2 powers the board (0.8 to 5 V); the multimeter's lesson applies in both directions: the service cannot see what is wired. So:
   - `ppk2.mode` to **ampere** is how a board with its own supply is measured, and to **source** powers nothing yet; the PPK2 does not report whether its output is on, so after a restart of the service the output state is **unknown**.
     Leaving source mode while the output is on or unknown can cut a board's power, so it is refused `supply_unconfirmed` unless `accept_supply` is true. Entering source mode, or leaving it when this service itself switched the output off, needs no tick.
   - `ppk2.supply` sets the voltage (800 to 5000 mV, refused `voltage_unsafe` outside) and switches the output. **Switching on** needs source mode (`ppk2_mode` refuses otherwise) and `accept_supply` true (`supply_unconfirmed`). Off needs no tick.
   - No other byte is ever sent: a whitelist of the six command shapes, and a test fails if any other can leave the module.

## Configuration

`instruments.ppk2`: `auto` (the one PPK2 on the bench; two are `two_candidates`) or its USB serial (`E1C5F15B15A3`). Unset means no PPK2 commands. `NRFWB_PPK2`. The capability `ppk2` is the setting; whether it is plugged in is `ppk2.show`'s answer.
The session commands now need the capability `meter_stream` (the bounded `meter` stream) which is present when **either** instrument is configured; `meter_store` still needs `paths.meter`. A session for a source that is not configured
is refused `capability_missing`. On the bench `streams.meter.retain` stays 20,000 events (67 minutes at 5/s) and `max_age_days` 1.

## Commands

| command | effect | what it does |
|---|---|---|
| `ppk2.show` | read | `{reachable, serial, port, mode: ampere/source/unknown, vdd_mv, calibrated, hw, supply: on/off/unknown}`; opens the port only for the metadata query, never starts a stream. While a session is using the PPK2 it is refused `meter_busy` and the session carries the facts |
| `ppk2.mode` `mode`, `accept_supply`? | write | ampere or source, by the rule above; read back from the metadata (`no_reply` if it did not take) |
| `ppk2.supply` `on`, `mv`?, `accept_supply`? | write | the voltage and the switch, by the rule above; the voltage read back from the metadata |
| `meter.watch` / `meter.record` | write | gain a `source` argument: `meter` (the default when the multimeter is configured) or `ppk2` (the default when only it is) |
| `meter.session`, `meter.profile`, `meter.recordings` | read | gain `source`, `lost`, `filtered`, `rate_low` |

Result codes added (core.md rows; the test reads both): `ppk2_unreachable` (failed: nothing at the port, or it would not open), `ppk2_mode` (refused: the PPK2 is in the other mode), `supply_unconfirmed` (refused: needs `accept_supply`).
`meter_busy` now means "an instrument session is running". `no_reply` is used when the unit does not answer the metadata query within the ceiling. `two_candidates` and `voltage_unsafe` already exist.

Ceilings, derived: the metadata answer took 0.2 s, so **0.5 s** (2.5 x); a block is 0.2 s of stream, so a block that has not completed in **0.65 s** (2 x the block + the same 0.25 s call ceiling the multimeter uses) is a silent
unit and **ends the session** (`state: error`, the file kept as far as it got), as the multimeter does. After `0x07` the stream is drained until a 50 ms read is empty (the in-flight bytes took under 10 ms), at most 1 s.

## What changes in a session and a recording

A reading event gains `min`, `max`, `n` and `source` (a multimeter reading has `min == max == value` and `n == 1`). The envelope takes `min`/`max` for a bucket's edges and `value * n` into its sum, so a merged bucket's mean stays exact;
the **page's envelope does the same** (envelope.js and envelope.py are cross-checked by a test). Statistics: `readings` counts samples (the page says "samples" for the PPK2); `charge` for a PPK2 block is the block's mean times its
duration `n / 100,000` summed, which is exact for the samples taken (the multimeter keeps its trapezoid between readings). CSV columns become `t_s,wall,value,unit,function,range,nplc,overload,min,max,n`: the old columns keep their place,
so an older file still reads. For the PPK2, `range` is the highest range index seen in the block (0 to 4, not an amp value: the full-scale of each range is not asserted) and `nplc` is empty. The sidecar and each row of `meter.recordings` carry `source`.

## The page

A source switch at the top of the Meter page (only when both are configured) and the multimeter's setting line becomes the instrument's: for the PPK2, *mode (ampere or source), supply voltage, calibrated yes/no, lost, filtered*.
Watch, Record, Stop, the figure, the chart, the figures and battery life are unchanged. The note about averaging changes with the source: for the PPK2 it says each point is a 0.2 s band from lowest to highest, so a burst shows as the
band's top edge. Mode and supply controls on the page are **deferred** (the commands exist; the page shows the mode and says to set it with `ppk2.mode`). Devices and Home get a *Power profiler* card like the multimeter's; Help gets a topic.

## Deferred (named so they are not forgotten)

- **Raw capture of a burst**: `ppk2.capture` for 1 to 10 s at the full 100 ksps, kept as a file, to look inside one wake. 10 s is 4 MB of words; not built.
- **The 8 logic inputs** (bits 24 to 31): wiring a board's GPIO to one would mark exactly when it wakes. The bytes are already read and counted in the benchmark; recording them per block is not built.
- Page controls for mode and supply; a project `expect` for a current range.

## Checks

Unit, over a **fake PPK2**: a pty pair, or a file-like serial, that answers `0x19` with a real metadata block (the one above), streams generated words for `0x06`, stops on `0x07` and keeps 768 bytes in flight: the port is picked by interface
number (the shell port is never opened, a fake tree has both); metadata parses (all 5 ranges, `Calibrated: 0`, `mode: 2`); the conversion matches the reference formula on known words, in every range; the unique-value reduction equals a
straight per-sample loop on random words (min, max, sum, count exact); block time is the sample clock; the counter check counts lost steps and wraps at 64; a stream under 98,000/s sets `rate_low`; the 3 samples after a range change are left out
and counted, across a block boundary too; a silent unit ends the session with `error`, the file kept; stop sends `0x07` and drains; a dead stream left from before is stopped before a session starts; sessions refuse `meter_busy` across
sources; `ppk2.show` is refused while a session runs; **the whitelist**: every byte written is one of the six shapes (a test fails on any other); the gate: leaving source mode with the output on or unknown is `supply_unconfirmed`, entering it
is not, switching on without source mode is `ppk2_mode`, without the tick `supply_unconfirmed`, voltage outside 800 to 5000 is `voltage_unsafe`; the CSV's old columns are where they were. Envelope: min/max/mean/count exact through every merge
with blocks of different `n`, in Python and in the page's code, cross-checked. Mutants on the reducer, the conversion, the range filter, the counter, the gate and the whitelist. Browser: both themes, phone and desktop, the switch, a PPK2
watch and recording, the lost/filtered line, the failure shown. **Live**: `ppk2.show` on the real unit; a 60 s recording with `lost` 0 and the service's CPU under 15 % of a core; a stop that leaves the unit quiet; the unit's mode and supply
are not written on the real unit without Peter's word (the gate is proved on the fake).

## Files this spec allows

`nrf_workbench/instruments/{ppk2,sampler,meter,envelope}.py`, `nrf_workbench/core/{config,status}.py`, `nrf_workbench/service.py`, `nrf_workbench/api/feed.py`, `ui/src/{MeterPage.jsx,App.jsx,Home.jsx,Devices.jsx,help.js}`,
`ui/src/meter/*`, `docs/specs/{ppk2,meter-page,meter,core,api}.md`, `tests/instruments/`, `tests/core/`, `tests/dashboard/`, `tests/api/`, `tests/devices/fakes.py`, `scripts/verify_ppk2_live.py`, `scripts/verify_meter_live.py`, `config/host.yaml` examples.

# A LAN multimeter as a bench instrument (Siglent SDM3045X)

Peter, 2026-11-19: `sdm3045x.home` is a multimeter on the LAN; integrate it so the bench can measure. Area `instruments`
(architecture.md). Tags: **[measured]** on the real meter, **[derived]**, **[unknown]** (not tested, and why).

## What is true on the meter (measured 2026-11-19, `sdm3045x.home` = 192.168.0.63)

- TCP **5025** answers raw SCPI, one text line each way. Port 80 is closed. `*IDN?` -> `Siglent Technologies,SDM3045X,SDM35EAC1L0704,5.01.01.09R2`.
- It was **already in current mode**: `FUNC?` -> `"CURR"`, range 0.6 (`CURR:DC:RANG?`), auto-range **off**, NPLC 10, sample count 1, trigger `IMM`, and three
  `READ?` gave 1.71706 mA, 1.71717 mA, 1.71728 mA. Something is wired **in series** and drawing 1.7 mA.
- A `READ?` takes the time of one conversion: 198 and 215 ms at NPLC 10 (10 cycles / 50 Hz = 0.200 s, **[derived]**; the first answered in 14 ms because a
  conversion had already finished). A query it does not know (`VOLT:DC:IMP:AUTO?`) gets **no reply at all**, not an error line; the error shows in
  `SYST:ERR?` (`-113,"Undefined header"`). A query the meter understands answers in 1 to 7 ms.

## Defects first

1. **Switching the meter's function can cut the supply to the board under test.** In current mode the leads are in the current jacks, in series with the
   board. Setting `FUNC VOLT` from software moves the measurement to the voltage path and leaves the series circuit **open**: the board loses power.
   **[derived; not tested, because testing it would switch off whatever draws that 1.7 mA]** A range change in current mode may do the same for the instant
   the shunt relays move **[unknown]**. So **the service writes no setting to the meter, ever**: no function, no range, no NPLC, no trigger. It only asks
   questions (every line it sends ends in `?`). The function is a fact about **where the leads are plugged**, which software cannot see; the person sets it
   at the front panel and the service reports it.
2. **A silent meter looks like a hang.** An unknown query gets no answer, so every read has a **ceiling** and a miss is `no_reply` with the meter's own
   `SYST:ERR?` text, never a wait.
3. **The caller must not be able to aim the service at another host.** The address is **configuration**, never an argument (an API that connects to a
   named host is a way to reach anything the Pi can reach).
4. **A reading is data, not a verdict.** The service never switches power, never flashes and never refuses a project because of a number. It reports the
   value with the function, the range and the NPLC that produced it; a caller (or a project's `expect.meter`) judges.

## Configuration

`instruments.meter` (YAML, a string), `NRFWB_METER` (env): `host` or `host:port`. **No default**: unset means the `meter` capability is absent and
`GET /api/capabilities` says why. The port is the instrument's own (`SCPI_PORT = 5025`, the Siglent raw-socket port, measured open), a constant in the module,
not a setting. The same unknown-key rule as the rest of the configuration applies.

## Commands (area `instruments`, wire names `meter.*`; capability `meter`)

| command | effect | what it does |
|---|---|---|
| `meter.show` | read | connects, asks `*IDN?`, `FUNC?`, the function's range, auto-range and NPLC, and `SYST:ERR?`. Returns `{reachable, identity, function, unit, range, autorange, nplc, period_s, in_series, error}`. `in_series` is true when the function is a current one: "if the leads are in the current jacks this is in series with the circuit" (reported, never asserted). Measures nothing. |
| `meter.read` `expect_function`? | read | one `READ?` under the present setting: `{value, unit, function, range, nplc, raw, overload}`. With `expect_function` (`dcv`, `acv`, `dci`, `aci`, `res`, `fres`, `freq`, `cap`) a different present function is **refused `meter_function`** before anything is read, saying what it is set to: a caller who thinks it is reading volts while the meter is in current is told. |
| `meter.sample` `count`, `expect_function`? | read | `count` readings, one `READ?` each, timestamped on the host at receipt (`t_s` from the first): `{points: [{t_s, value}], n, min, max, mean, unit, period_s, duration_s}`. |

All three open a connection, ask, and **close it**: nothing stays open across calls, so a service restart cannot leave a socket the front panel is
locked out by. One lock serialises calls (one caller at a time on the meter). Nothing here is `effect: write`: the bench changes nothing by calling them.

`overload`: SCPI's overload value is `9.9E37`; a reading at or above it (`>= 9.9e37`) is a `suspect` result `meter_overload` with the raw text and the range, never
a number to calculate with. **[derived from the SCPI standard; not seen on this meter]**.

Units by function: `CURR`/`CURR:AC` -> A, `VOLT`/`VOLT:AC` -> V, `RES`/`FRES` -> ohm, `FREQ` -> Hz, `CAP` -> F. `CURR` is the only one **[measured]**; for any other
function name the unit is empty and the raw function is reported, so a wrong guess about a spelling shows as an empty unit and not as a wrong number.

## Result codes (a spec change to `core.md`; the test reads both)

| code | class | meaning |
|---|---|---|
| `meter_unreachable` | failed | the meter did not accept a connection within the ceiling (address wrong, meter off, another client holds it) |
| `meter_function` | refused | `expect_function` named a function other than the one the meter is set to; nothing was read |
| `meter_overload` | suspect | the reading is the overload value: the range is too small |

A meter that accepted the connection and then did not answer is the existing `no_reply` (failed), with `meter` in the evidence and the `SYST:ERR?` text.

## What the commands ask, per function

Range, auto-range and NPLC are asked with the function's own prefix (`CURR:DC`, `VOLT:DC`, `RES`, ...). **Only `CURR:DC` and `VOLT:DC` are [measured]**; for another function a
query the meter does not know is silent, the field is `null`, the error is drained with `SYST:ERR?`, and the reading ceiling assumes NPLC 10 (the one measured value): a
slower setting then shows as `no_reply` and says so, which fails safe.

## Numbers

- **Query ceiling 0.25 s** for everything except a reading: answers take 1 to 7 ms; a busy 4-core Pi may need 50 ms to schedule us; 5 times that is where
  something is wrong (the same derivation as `pinctrl`, line-release.md). Connect ceiling the same.
- **Reading ceiling = 2 x period + 0.25 s**, with `period = max(NPLC x 0.02 s, 0.014 s)`: 0.02 s is one cycle of the **slower** mains frequency (50 Hz, so the
  period is never under-estimated at 60 Hz); 0.014 s is the measured overhead of one `READ?` on this meter. At NPLC 10: 0.65 s against 0.198 to 0.215 s measured
  (3x). It is a give-up ceiling; a reading returns when the meter answers.
- **`count` is 1 to 1000, and `count x period` is at most 30 s**: a flash takes about 30 s on this bench and a request already holds the service for that
  long, so a sample is bounded to the same scale. At NPLC 10 that is 150 readings. Over the bound is a usage error that names the limit and the period.
  The 1000 is memory: 1000 points of two floats is a small JSON body.
- Timestamps are the **host's** monotonic clock at receipt (the meter stamps nothing). The error is one period plus scheduling jitter; the result says so
  (`t_s` is "when the reading arrived").

## Elsewhere in the bench

- **`bench.overview` and Home do not show the meter** (decided while building: a meter that is off would add a connect ceiling to every Home load, and a static
  "configured" line says nothing a person needs). Live state is an action on the Devices page. Nothing is added to the overview.
- **Devices page**: a *Multimeter* card (present only when the commands are, so only with an address), with **Check the meter** (`meter.show`), **Read once**
  (`meter.read`) and **Take N** (`meter.sample`). The answer is a sentence in words with SI prefixes ("1.717 mA"), the amber note that the meter is in series
  when it is set to current, for a sample a chart (x is arrival time, spread evenly when every reading arrived inside the clock's resolution) with its range
  stated, and the whole result one click away; a failure is shown as the whole result with its code and no headline. No timers: a button asks, the answer is shown.
- **Help**: a `meter` topic (opened from Devices): what it can do, why it cannot change the function (the board loses power), what to set at the front panel.
- **A project can expect a reading** (slice 3): `expect.meter: {function, min, max}` in its manifest (base units: V, A, ohm, Hz, F; both ends inclusive; `min` not above
  `max`; `function` one of the `expect_function` words). The new read command **`project.measure`** takes one `meter.read` with `expect_function` and returns
  `{function, unit, value, min, max, met}`: **ok** whether inside or not (`met` says which, the message says "OUTSIDE"), because the number is the answer and the
  project does the judging, never the meter command. Anything that is not a reading is returned as it came: a meter set to another function is the refusal
  `meter_function`, an overload is `suspect`, an unreachable or silent meter is `failed`, a host with no meter is `capability_missing`: a project is not held to a
  number nobody measured. It is **not** a mode of `project.probe` (decided while building: probe's result is the I2C probe's `bus, address, answered`, and
  callers rely on it). `project.check` stays read-only: it lists the expectation as `not_applied` ("not measured by check") and **never touches the meter**.
  The Projects page shows a **Measure** button on a project that has the expectation.

## Checks

Unit: a fake SCPI server on a real socket (so the framing, the silence and the close are real). **The fake records every line it receives and a test asserts every
one ends in `?`** (the invariant of defect 1), plus: `show` fields, `read` value/unit/overload, `expect_function` refusal with nothing read, a silent meter ->
`no_reply` within the ceiling, a refused connect -> `meter_unreachable`, sample statistics and timestamps, the 30 s and 1000 bounds, concurrent callers
serialised, the connection closed after each call, the capability absent without an address, the address never an argument. Mutants on each. Browser: both themes,
phone and desktop, Devices page. Live: `meter.show`, `read` and `sample` on the real meter **in the function it is already in**, and the meter left exactly as
found (`FUNC?`, range, NPLC and auto-range read before and after and equal).

## Not built, on purpose

`meter.configure` (function, range, NPLC). Defect 1 is why; adding it means a refusal while the function is a current one and an explicit acknowledgement that
the circuit opens, and a decision by Peter about what the bench may do to a live board. Voltage and resistance reads therefore need the front panel set first.

## Files this spec allows

`nrf_workbench/instruments/{__init__,scpi,meter}.py` (new), `nrf_workbench/core/{config,status}.py`, `nrf_workbench/service.py`, `nrf_workbench/bench/overview.py`,
`nrf_workbench/project/{manifest,check,commands}.py` (slice 3), `docs/specs/{meter,core,projects}.md`, `ui/src/{Devices.jsx,help.js,Home.jsx,overview.js}`,
`ui/src/meter/*`, `tests/instruments/`, `tests/bench/`, `tests/project/`, `scripts/verify_redesign.py`, `scripts/verify_meter_live.py`.

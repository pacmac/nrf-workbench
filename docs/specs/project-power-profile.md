# Project power profile: the trigger and the named events follow the board under test

Peter, 2026-11-21: "the wake/sleep event trigger profile / definition needs linking to the project so that when a project is selected, it also selects the correct profile, the same way that GPIOs etc are associated with a project."
Then, answering the open questions: "a project is linked to a device that is plugged into the USB, so if no device is plugged in then there would be no wake / sleep definition / profile would there ? - this is NOT just designed for this one project it is supposed to be generic and apply to any project. maybe another project is awake all of the time and has no sleep, but we still want to tag / define events such as radios switching on and off".
Extends `projects.md` (the manifest, apply, check) and `ppk2-stages.md` (the wake profile, stages, events); where they differ this one is later. Task `gap-wake-profile-follows-project`, step 1 to 3. The Meter page itself is a separate task (see "Not in this task").

## What is wrong today

1. **The profile is stored nowhere.** The five trigger fields are `useState(EMPTY_TRIGGER)` in `MeterPage.jsx`; a reload starts blank. (Register: `the-trigger-profile-is-lost-when-the-page-reloads`.)
2. **A project cannot carry one.** The manifest has `pins`, `macros`, `wiring`, `esp32`, `firmware`, `expect`; nothing about how a board's power is read. (Register: `a-project-cannot-carry-the-trigger-profile`.)
3. **There is no "selected" project.** Projects are applied, and several may be applied at once (`projects.md`, "Open questions"). "Selecting a project selects its profile" has to mean something that exists.

## Decisions (derived, each with its constraint; each reversible)

1. **The profile belongs to a ROLE in the project, not to the project.** A project's boards are roles (`devices.<role>`), each a USB serial or an ESP32 target. *Constraint: a profile describes one board's firmware; a project may hold several boards, and a project with no board plugged in measures nothing.* So the profile hangs off a role, and it is **in force** only while (a) the project is applied and (b) that role's board is attached. **Attached, for the profile, means the board is on USB OR the console link the project wires for the role is configured and open** (amended 2026-11-21 on the firmware session's evidence, #71: the bench RAK is powered by the profiler and is deliberately off USB while measured, so USB alone leaves nothing in force for ever; and the unit is silent for 878 s of every 900 s, so "open", never "a line was seen lately"). The plan's `roles[].attached` (the USB test that flashing needs) is unchanged; `power.role_attached` adds the link, and says which made it attached. With no board attached there is no profile in force and a session behaves as today: every field found from the data.
2. **A profile is two independent parts, both optional, so it fits a device that never sleeps.**
   - **`trigger`**: what is kept at full rate (the five fields of the wake profile; a blank field is found from the data). A device that is awake all the time simply has no `trigger`, or one that sets `max_s`; nothing in it needs a sleep level.
   - **`events`**: named stretches applied to every capture, so "radio on", "radio off", "boot" mean the same thing every time. Each is anchored on **`from: wake`** (seconds from the wake's own start: a *stage*, `ppk2-stages.md`, Stage 2) or **`from: start`** (seconds from the recording's start: an *event*, `meter-page.md`, "Events"). A device with no sleep uses `from: start`.
   *Constraint: Peter, "events such as radios switching on and off" for a device with no sleep: a wake-anchored definition has no origin there.*
3. **Which board is on the profiler is not discoverable, so one is named when it is not obvious.** The unit measures current at its two terminals; nothing on its USB link says which board is wired to them. *Constraint: the profiler is a single instrument and a USB inventory lists boards, not what is wired where.* So: if **exactly one** attached role of an applied project has a profile, it is in force on its own; if **several** do, none is in force until a person names the one under test (`meter.project_use`), kept in `<paths.meter>/under-test.json` as `{project, role, key}` (the board's own key). The choice counts only while that project is applied and that role's board, with that key, is attached; a reading command writes nothing, so a choice that has stopped counting is ignored and reported as no `under_test`, and it is removed by the next `meter.project_use` and by `project.release` (so it is never carried to another board; the same board plugged back in is the same board under test). If **none** is attached with a profile, nothing is in force.
4. **A session started with no `wake_*` argument uses the profile in force; an explicit argument wins, field by field.** *Constraint: a person on the page and an agent making the same call must get the same capture, or they will disagree about what a wake was (the defect class `park-wake-kinds` and the cycle table's own rule already show).* `meter.wake_set` is unchanged (it is always explicit). The recording's sidecar (and the session summary) says where its profile came from: `profile: {project, role, source, overridden}`, where `source` is `"project"` when a profile in force was applied (`overridden` lists the `wake_*` arguments that replaced its fields), `"explicit"` when none was in force and `wake_*` arguments were given, `"found"` when neither (project and role are null; `overridden` empty); and the trigger in force at its end (`ppk2-stages.md` already records that). A multimeter session takes no profile.
5. **A recording starts with its profile's events.** `meter.record` writes the profile's `from: wake` events into the recording's stages and its `from: start` events into the recording's events, through the same rules as `meter.stage_set` and `meter.event_set` (so a bad one is refused by the code that already judges them). A watch keeps no files, so it applies only the trigger.
6. **Applying a project changes nothing in a store for this section.** The manifest is the store: the profile is read from it when it is needed. So `apply` has nothing to create, `release` nothing to remove, and **no ownership tag**. (The pins section needs ownership because two projects can claim one pin; here the conflict is two profiles for one instrument, which decision 3 resolves by naming one, not by refusing apply.)
7. **Saving a changed profile from the page back into the manifest is not built.** The manifest is hand-edited YAML with comments; rewriting it from a form loses them. *Constraint: a rewrite that destroys a person's comments is worse than a page that says "edit the project's manifest to keep this".* Parked as `park-save-profile-to-manifest` (needs a splice of just the `power:` block).
8. **Continuous (Never, on the capture card) is out of this task and is NOT unbounded.** Revised 2026-11-21 (the firmware session, #68: continuous must stay available): it runs until Stop or the 12 h file limit that `bound_s` already has (`core.md`), so no new rule is needed; see `power-workbench.md`, "Capture". A rolling window that keeps only the newest length is a later improvement.
9. **The profile carries NO wake period** (reversed 2026-11-21; Peter: "you have assumed that 15 minutes is a fixed window size. that is NOT how a wake window is detected"). Decision 9 had added `power.<role>.beat_s`, how often the board wakes, so a give-up could be derived from it. A wake is found from the current, and a board may wake at irregular times or never sleep, so a period is not a property the profile can state; the field was never used by detection, and it is removed (register: `a-profile-assumes-wakes-come-on-a-fixed-period`). A give-up is a ceiling the person gives, or 12 h (`power-workbench.md`, "Capture").

## The manifest section

```yaml
power:                               # optional; a mapping of role -> profile
  alarm:                             # must be a role in devices (an nRF or an ESP32 role)
    trigger:                         # optional; every field optional (blank = found from the data)
      awake_ua: 50                   # a 0.2 s block is awake when its mean is over this, in microamps
      sleep_ua: 25                   # the current it sleeps at, in microamps
      hold_s: 0.2                    # a wake ends after this long back at or under the awake level
      lead_s: 0.2                    # sleep kept before a wake
      max_s: 40                      # the longest wake kept whole
    events:                          # optional; at most 12 per role (a table a person reads, as for stages)
      - {name: boot,        from: wake,  start_s: 0.0, end_s: 1.2}
      - {name: radio join,  from: wake,  start_s: 4.0, end_s: 12.1, every: 2, offset: 1}
  sensor:                            # an always-awake board: no trigger, events from the recording's start
    events:
      - {name: radio on,  from: start, start_s: 2.0, end_s: 14.0}
```

Validation (`manifest.py`, same style as `pins`: every error names its field, an unknown key is an error):
- the role must be in `devices` (so the reserved names `i2c` and `meter` are refused for free; a YAML mapping cannot hold a role twice, so no rule is needed for that); a profile states at least one of `trigger`, `events` (an empty one would make a role a candidate that supplies nothing);
- `trigger` fields are numbers, the **same ranges `capture.WakeProfile` enforces** (sleep and awake above 0, awake above sleep, `max_s` 1 to 120, `hold_s` 0.2 up to `max_s`, `lead_s` 0 up to `max_s`), converted from µA to amps when handed on; the manifest does not restate the rules, it calls `WakeProfile` and reports its message under the field;
- an event: `name` 1 to 40 characters, unique ignoring case within the role; `from` is `wake` or `start`; `start_s` below `end_s`; `every` (1 to 8) and `offset` (0 to `every` - 1) only with `from: wake` (default 1 and 0), the same selector rule as stages; the two kinds are judged by the code that judges them on a recording (`capture.set_stage` for `from: wake`, `events.set_event` for `from: start`), so the stage rule holds (stages in wakes they share may not overlap; a stage may start before the wake, negative) and the event rule holds (a recording event starts at 0 or later and **may overlap or nest**, as events do); a name is unique ignoring case across both kinds in a role; the 12 limit counts both kinds;
- `beat_s` (or any period) is an unknown key and refused; at most 12 events per role; `power` itself is at most the manifest's own 64 KiB.

## Commands

| command | effect | what it does |
|---|---|---|
| `meter.project_profile` (needs `meter_stream`) | read | `{in_force, candidates, under_test}`: `in_force` is `{project, role, kind, trigger, events}` or `null`; `candidates` lists every role of an applied project that has a profile with `{project, role, attached, why}` so a page can say *"alarm, not attached"*; `under_test` is the chosen one or `null`. Opens nothing, writes nothing |
| `meter.project_use` `project`, `role` | write | name the board under test; refused `profile_not_candidate` (naming why: not applied, not attached, no profile); `role: ""` clears it |
| `project.show` | read | gains `power` (the manifest's section) and, per role, whether it is in force |
| `project.plan` / `project.check` | read | one item per role with a profile: `have` when the board is attached, `unresolved` (not blocking, the role is information) when it is not |
| `meter.watch`, `meter.record` | write | as now; with no `wake_*` the profile in force supplies the trigger (decision 4); a recording also gets its events (decision 5) |

**Where the code lives (the dependency rule, `architecture.md`).** `instruments` imports `core` only and may not import `project`. So the session default does not import the project area: `meter.watch` and `meter.record` ask the registry for `meter.project_profile` through the same call the project area itself uses for the stores. The resolver (`nrf_workbench/project/power.py`) lives in `project`, which already depends on `instruments`.

## Result codes (core.md rows)

| code | class | meaning |
|---|---|---|
| `profile_not_candidate` | refused (409) | `meter.project_use` named a role that has no profile, is not attached, or belongs to a project that is not applied; the evidence says which |

A manifest that breaks a rule above is the existing `bad_manifest` (422) with the field named.

## Files this spec allows

`nrf_workbench/project/{manifest,plan,check,commands}.py` and the new `nrf_workbench/project/power.py`; `nrf_workbench/instruments/{meter,sampler}.py` (the commands and the default, the sidecar field, the events written at the start of a recording); `docs/specs/{projects,ppk2-stages,meter-page}.md` (a pointer to this spec); `tests/project/test_power.py` (new), `tests/project/test_manifest.py`, `tests/instruments/test_session_profile.py` (new: it needs the profiler's fake unit that `test_capture.py` holds, not the multimeter's that `test_meter_sessions.py` holds); `nrf_workbench/core/status.py` and `docs/specs/core.md` (the closed list of result codes gains `profile_not_candidate`: `tests/core/test_status.py` refuses an emitted code that is not listed, found when the first green run showed it; also `nrf_workbench/project/check.py`, which gives the new `power` plan item its status). **Not changed:** `apply.py`, `store.py` (decision 6: nothing to create, no ownership), `capture.py` (the profile reuses `WakeProfile`), every UI page (the Meter page's display of the profile belongs to the Meter page rewrite).

## Not in this task

- **The Projects help topic** (`ui/src/help.js`) naming the `power` section: any UI source change makes the committed build stale (`tests/dashboard/test_ui_rules.py` compares the build's digest to the source's), so it waits for the Meter page task, which rebuilds the UI anyway. Tried and reverted 2026-11-21.
- **The Meter page** showing the profile, naming the board under test, and the layout of mockup A, the typed length and the one-shot / continuous switch: the next task, on this one's commands.
- **The full-height shell** for every page: its own task (`the-dashboard-shell-scrolls-and-is-capped-at-six-xl`).
- **Saving a changed profile back to the manifest** (decision 7) and **building Continuous** (decision 8).
- **Grouping wakes by their shape** (`park-wake-kinds`): `every` and `offset` stay by capture number, as for stages.

## Checks

Unit (`tests/project/test_power.py`, `test_manifest.py`): a manifest with `power` validates; each rule above refused with its field (unknown role, a role twice, a reserved role, awake not above sleep, `max_s` 121, `hold_s` below 0.2, an event named twice ignoring case, start not below end, `every` or `offset` with `from: start`, a thirteenth event, an overlap under the selector rule, an overlap on the clock); a profile with only `events` and one with only `trigger` both validate. Resolver: board attached and project applied gives `in_force`; not attached gives `null` and the candidate says why; project not applied gives `null`; two attached roles give `null` until `meter.project_use` names one; the choice is dropped when the project is released and when the board is unplugged (never carried to the other board); `meter.project_use` of a non-candidate is `profile_not_candidate` naming the reason. Session (`test_meter_sessions.py`): a watch with no `wake_*` uses the profile in force (the `WakeProfile` seen by the fake unit equals the manifest's, converted from µA); an explicit argument overrides that field only and leaves the others from the profile; with nothing in force the capture is exactly as today (every field found); a multimeter session with a profile in force is not refused (it keeps no wakes, so it ignores the profile); a recording's sidecar has `profile` with its source; its stages hold the `from: wake` events and its events the `from: start` ones.
Mutants (each must be caught): the attached test removed (a profile in force with no board); the applied test removed; the one-candidate rule widened to take the first of two; the explicit-wins rule inverted; µA not converted to amps (a thousandfold error); the `from: start` event written as a stage; the choice not dropped on release. Each mutant is seen red once, per the register's method, before the check is trusted. A control run on the unmutated source is green.
Live, on the bench Pi (deploy only when `meter.session` is idle): apply a project with `power` for the bench RAK with it attached, `meter.project_profile` says in force; unplug nothing (hands), instead a project whose role serial is not attached says `candidates[].attached: false`; a 1 minute `meter.record` with no `wake_*` shows the profile's trigger in its sidecar. No PPK2 output setting is changed (measuring only, as authorised).

## Register

`a-project-cannot-carry-the-trigger-profile` (the manifest section, plan, check, resolver, commands). `the-trigger-profile-is-lost-when-the-page-reloads` is closed by the Meter page task, which reads the profile from the project instead of page state, and is not touched here. Each entry is tried red before its files are edited.

source_hash: pending

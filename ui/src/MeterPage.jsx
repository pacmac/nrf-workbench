import { useCallback, useContext, useEffect, useRef, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { createFeed } from "./feed.js";
import { Button, Json, NavContext, inputClass } from "./ui.jsx";
import { Envelope, Stats } from "./meter/envelope.js";
import { findCycle } from "./meter/cycle.js";
import { CycleTable } from "./meter/Cycle.jsx";
import { CaptureCard } from "./meter/Capture.jsx";
import { LiveScope } from "./meter/LiveScope.jsx";
import { Drawer, FitList } from "./meter/Monitor.jsx";
import { ReplayScope } from "./meter/ReplayScope.jsx";
import { Scope } from "./meter/Scope.jsx";
import { Supply } from "./meter/Supply.jsx";
import { batteryLife, clock, lapse, si } from "./meter/format.js";
import { Trigger } from "./meter/Trigger.jsx";
import { EMPTY_TRIGGER, isChanged, triggerArgs } from "./meter/trigger.js";

// The Meter page (docs/specs/meter-page.md, "Bench layout"; docs/specs/ppk2.md): the Nordic Power Profiler as two bench instruments, one above the other. The SUPPLY is always on screen: the voltage, the
// output switch and whether the unit supplies the board or only monitors it. The CURRENT MONITOR is the scope, its readouts and, in a column beside it, the Capture card and the recordings.
// The page fills the window and nothing on it scrolls (docs/specs/navigation.md); what is wanted now and then (the trigger, battery life, the stages across the wakes) opens over the monitor.
// A session runs on the Pi; this page only listens to it. Readings arrive on the live feed and are kept in the same bounded envelope the server keeps, so a 12 hour session is as cheap to hold as
// a 1 minute one. There is no timer here: the clock shown is the latest reading's own time. ONE scope shows either the live session or a replay of a recording.

const isCurrent = (fn) => /^CURR/i.test(fn || "");
const RUNNING = ["watching", "recording"];
const SOURCE = "ppk2";                                           // the multimeter is no longer on this page: the profiler is the instrument

// What the battery-life line says: the whole recording's average, or the wake/sleep cycle found in it (cycle.js), and why none was found when none was.
function lifeSentence(lifeBy, cyc, life, unit) {
  if (lifeBy === "whole") return life ? `At the average of the whole recording it lasts ${life}.` : "Type a capacity to see how long it lasts at the average above.";
  if (!cyc) return "";
  if (cyc.found) {
    const gaps = cyc.regular ? "" : `, gaps from ${lapse(cyc.shortest)} to ${lapse(cyc.longest)}, so not regular`;
    return `Wakes every ${lapse(cyc.period)} (${cyc.cycles} ${cyc.cycles === 1 ? "cycle" : "cycles"} counted${gaps}). Average over them ${si(cyc.mean, unit)}. `
      + (life ? `It lasts ${life}.` : "Type a capacity to see how long it lasts.");
  }
  if (cyc.reason === "short") return "Record a little longer: a cycle needs at least two wakes.";
  if (cyc.reason === "no_sleep") return "The sleep current reads zero or less, so no cycle can be found. Check the leads.";
  if (cyc.reason === "steady") return "The current never rises to twice its sleep level, so no wake was found. Use the whole recording.";
  return `Found ${cyc.wakes} ${cyc.wakes === 1 ? "wake" : "wakes"}: a cycle needs two. Record for longer than one cycle.`;
}

// The trigger the project gives, in words: "asleep 34 µA, awake over 100 µA".
function triggerWords(t) {
  const parts = [];
  if (t.sleep_a) parts.push(`asleep ${si(t.sleep_a, "A")}`);
  if (t.awake_a) parts.push(`awake over ${si(t.awake_a, "A")}`);
  if (t.hold_s) parts.push(`a wake ends after ${t.hold_s} s`);
  if (t.lead_s) parts.push(`${t.lead_s} s of sleep kept before`);
  if (t.max_s) parts.push(`longest wake ${t.max_s} s`);
  return parts.join(", ");
}

export default function MeterPage({ commands, onDone }) {
  const nav = useContext(NavContext);
  const find = useCallback((name) => (commands || []).find((c) => c.name === name) || null, [commands]);
  const [profiler, setProfiler] = useState(null);
  const [session, setSession] = useState(null);
  const [latest, setLatest] = useState(null);
  const [, bump] = useState(0);
  const [recordings, setRecordings] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [capacity, setCapacity] = useState("");
  const [replayOf, setReplayOf] = useState(null);                     // the recording on the scope, or null for the live session; starting a session returns the scope to it
  const [lifeBy, setLifeBy] = useState("whole");                       // "whole": the Average figure; "cycle": the mean over whole wake/sleep cycles (cycle.js)
  const [ask, setAsk] = useState(null);                                // the recording whose delete is being confirmed
  const [trigger, setTrigger] = useState(EMPTY_TRIGGER);               // what counts as a wake, as typed (docs/specs/ppk2-stages.md, "The wake profile"); blank is found from the data
  const [forced, setForced] = useState(null);                          // the profile of the project whose board is on the profiler, or null (meter.project_profile)
  const [drawer, setDrawer] = useState(null);                          // "trigger", "life", "stages", "session" or null
  const [feedState, setFeedState] = useState({ state: "connecting" });
  const env = useRef(new Envelope());
  const stats = useRef(new Stats());
  const number = useRef(0);

  const running = Boolean(session && RUNNING.includes(session.state));

  const loadProfile = useCallback(async () => {
    const cmd = find("meter.profile");
    if (!cmd) return null;
    const r = await readJson(cmd.path);
    if (!r.ok || !r.body || !r.body.data) return null;
    const d = r.body.data;
    env.current = Envelope.from(d.buckets);
    stats.current = Stats.from(d.stats, d.last);
    number.current = d.session;
    return d;
  }, [find]);

  const loadRecordings = useCallback(async () => {
    const cmd = find("meter.recordings");
    if (!cmd) return;
    const r = await readJson(cmd.path);
    if (r.ok && r.body && r.body.data) setRecordings(r.body.data.recordings);
  }, [find]);

  const loadProfiler = useCallback(async () => {
    const cmd = find("ppk2.show");
    if (!cmd) return;
    try { setProfiler(await readJson(cmd.path)); } catch (e) { setProfiler({ status: 0, body: { message: String(e) } }); }
  }, [find]);

  const loadForced = useCallback(async () => {
    const cmd = find("meter.project_profile");
    if (!cmd) return;
    try {
      const r = await readJson(cmd.path);
      if (r.ok && r.body && r.body.data) setForced(r.body.data.in_force || null);
    } catch (e) { /* the project's profile is a default: without it the trigger is found from the data */ }
  }, [find]);

  useEffect(() => {
    if (!find("meter.profile")) return undefined;
    let live = true;
    let feed = null;
    const onPage = (_name, p) => {
      for (const e of p.events) {
        if (e.type === "meter.state") {
          const st = e.payload;
          if (st.session !== number.current) {                      // a new session: nothing of the old one belongs on its chart
            env.current = new Envelope();
            stats.current = new Stats();
            number.current = st.session;
            setLatest(null);
          }
          setSession(st);
          if (!RUNNING.includes(st.state)) { loadProfiler(); loadForced(); }          // the unit is free again: ask it what it is set to now
          if (!RUNNING.includes(st.state) || st.mode === "record") loadRecordings();
        } else if (e.type === "meter.reading") {
          const r = e.payload;
          if (r.session !== number.current) continue;               // a reading of an earlier session
          if (r.overload) stats.current.overload();
          else { env.current.add(r.t_s, r.value, r.min, r.max, r.n); stats.current.add(r.t_s, r.value, r.min, r.max, r.n, r.dt); }
          setLatest(r);
          if (r.facts && Object.keys(r.facts).length) setSession((s) => (s ? { ...s, ...r.facts } : s));    // lost and filtered samples show while it runs, not only at its end
        }
      }
      if (p.reset) loadProfile().then(() => bump((n) => n + 1));    // the service restarted: its cursors are new, so start again from its profile
      bump((n) => n + 1);
    };
    (async () => {
      const d = await loadProfile();
      if (!live) return;
      const s = find("meter.session");
      let st = null;
      if (s) { const r = await readJson(s.path); if (live && r.ok && r.body) { st = r.body.data; setSession(st); } }
      loadRecordings();
      loadForced();
      if (!(st && RUNNING.includes(st.state))) loadProfiler();
      if (!live) return;
      feed = createFeed({ feeds: ["meter"], from: d ? { meter: { cursor: d.cursor, epoch: d.epoch } } : undefined, onPage, onState: setFeedState });
      feed.start();
      bump((n) => n + 1);
    })();
    return () => { live = false; if (feed) feed.stop(); };
  }, [find, loadProfile, loadRecordings, loadProfiler, loadForced]);

  async function run(cmd, args) {
    setOutcome({ pending: true });
    try {
      const r = await writeJson(cmd.path, args || {});
      setOutcome(r.ok ? null : r);
      if (r.ok && cmd.name !== "meter.stop") setReplayOf(null);          // starting a session returns the scope to it
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  async function saveCsv(name) {
    const cmd = find("meter.recording");
    const r = await readJson(cmd.path, { name });
    if (!r.ok || !r.body || !r.body.data) { setOutcome(r); return; }
    const url = URL.createObjectURL(new Blob([r.body.data.csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  async function deleteRecording(name) {
    const cmd = find("meter.recording_delete");
    setAsk(null);
    if (!cmd) return;
    setOutcome({ pending: true });
    try {
      const r = await writeJson(cmd.path, { name });
      setOutcome(r.ok ? null : r);
      if (r.ok && replayOf === name) setReplayOf(null);
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
    loadRecordings();
  }

  const watch = find("meter.watch");
  const record = find("meter.record");
  const wakeSet = find("meter.wake_set");
  const wake = triggerArgs(trigger);                                   // the wake_* arguments for the fields filled in, and what is wrong with any that are not numbers
  const stop = find("meter.stop");
  const replayCmd = find("meter.replay");
  const deleteCmd = find("meter.recording_delete");
  const pending = Boolean(outcome && outcome.pending);

  if (!watch || !find("ppk2.show")) {
    return (
      <div className="space-y-4">
        <h2 className="text-2xl font-semibold">Meter</h2>
        <p role="status" className="text-[15px] text-slate-600 dark:text-slate-400">The Meter needs the power profiler and the readings stream: this bench has no profiler configured (instruments.ppk2) or no readings stream (streams.meter).</p>
      </div>
    );
  }

  const unit = (latest && latest.unit) || (session && session.unit) || "A";
  const figures = stats.current.toObject(true);
  const capacityMah = Number(capacity);
  const buckets = env.current.snapshot();
  const unitFacts = profiler && profiler.body && profiler.body.data;           // what the unit said when last asked (it cannot be asked while a session holds it)
  // The supply voltage, for an energy figure: from the session's facts when it has them (what the unit was set to when the session began), else from what the unit last said.
  const volts = session && session.unit_mode ? (session.unit_mode === "source" && session.supply === "on" ? session.vdd_mv / 1000 : null)
    : unitFacts && unitFacts.mode === "source" && unitFacts.supply === "on" ? unitFacts.vdd_mv / 1000 : null;
  const cyc = lifeBy === "cycle" ? findCycle(buckets) : null;
  const life = batteryLife(lifeBy === "cycle" ? (cyc && cyc.found ? cyc.mean : null) : figures.mean, capacityMah);
  const count = figures.readings.toLocaleString("en-US");
  // The recording the stages section reads: the one on the scope, else the one the last session wrote, and only while the list says it is there (a deleted one is not asked for).
  const wanted = replayOf || (session && session.mode === "record" && session.name ? session.name : null);
  const scopeRecording = wanted && (recordings === null || recordings.some((r) => r.name === wanted)) ? wanted : null;
  const justRecorded = !running && session && session.session > 0 && session.mode === "record" && session.name && (recordings || []).some((r) => r.name === session.name);
  // A session that waits for wakes says how many whole ones it has seen of how many it wants, and when it gives up (docs/specs/power-workbench.md, "Capture"); how a finished one ended comes from `ended_by`.
  const wantWakes = session && session.stop_after_wakes ? session.stop_after_wakes : null;
  const seenWakes = session && session.wakes ? session.wakes : 0;
  const noun = (k) => (k === 1 ? "wake" : "wakes");
  const giveUp = session && session.bound_s >= 43200 ? "12 h" : session ? clock(session.bound_s) : "";
  const sessionText = running
    ? `${session.state}, ${clock(latest ? latest.t_s : 0)}${wantWakes ? `, ${seenWakes} of ${wantWakes} ${noun(wantWakes)}, gives up at ${giveUp}` : ` ${session.bound_s >= 43200 ? "(stops at 12 h)" : `of ${clock(session.bound_s)}`}`}, ${count} samples${session.name ? `, to ${session.name}.csv` : ""}`
    : session && session.session > 0
      ? session.ended_by === "gave_up"
        ? `ended: gave up after ${giveUp}, ${seenWakes} of ${wantWakes} ${noun(wantWakes)} seen. ${count} samples`
        : `${session.state === "error" ? "ended by a fault" : "ended"}: ${session.reason}. ${count} samples`
      : "No session has run since the service started.";

  const notes = [];
  if (session && session.lost > 0) notes.push(`${session.lost.toLocaleString("en-US")} samples were lost between the unit and the Pi, so the charge is a little low.`);
  if (session && session.rate_low) notes.push("The samples are arriving slower than the unit sends them: some may be missing without the counter showing it.");
  if (session && session.filtered > 0) notes.push(`${session.filtered.toLocaleString("en-US")} samples taken just after the unit changed range were left out, as they are not the current.`);
  if (figures.mean !== null && figures.mean < 0) notes.push("The current reads negative: it flows the other way through the unit, so the leads are probably the wrong way round. The numbers are right, only their sign is reversed.");
  const noteLine = notes.length > 0 ? <p role="note" className="truncate text-xs text-amber-800 dark:text-amber-300" title={notes.join(" ")}>{notes.join(" ")}</p> : null;

  const lead = replayOf ? [] : [{ label: "Now", aria: "Latest reading", value: latest ? (latest.overload ? "overload" : si(latest.value, latest.unit)) : "—" }];
  const tail = [{ label: "Battery life", value: life || "—", onClick: () => setDrawer("life") }];

  const triggerText = forced ? `From project ${forced.project}: ${triggerWords(forced.trigger) || "no trigger fields, so found from the data"}` : "Found from the data";
  const side = (
    <>
      <CaptureCard watch={watch} record={record} stop={stop} running={running} pending={pending} sessionText={sessionText} outcome={outcome} blocked={wake.problems.length > 0}
        onRun={(cmd, args) => run(cmd, cmd === stop ? {} : { ...args, source: SOURCE, ...wake.args })}
        extra={justRecorded && replayCmd ? <Button tone="quiet" onClick={() => setReplayOf(session.name)} aria-label={`Replay ${session.name}, just recorded`}>Replay {session.name}</Button> : null} />
      <div className="flex items-start gap-2 rounded-md border border-slate-200 px-3 py-2 dark:border-slate-800">
        <p className="min-w-0 flex-1 text-[13px]" aria-label="Trigger in force" title={triggerText}>
          <span className="block text-xs text-slate-600 dark:text-slate-400">Trigger{isChanged(trigger) ? " (changed here)" : ""}</span>
          <span className="block max-h-10 overflow-hidden">{triggerText}</span>
        </p>
        {wakeSet && <Button tone="quiet" className="min-h-8! px-3! py-1!" aria-expanded={drawer === "trigger"} onClick={() => setDrawer(drawer === "trigger" ? null : "trigger")}>Change</Button>}
      </div>
      {record && (
        <section aria-label="Recordings" className="flex min-h-0 flex-1 flex-col">
          <h3 className="text-[15px] font-semibold">Recordings</h3>
          <FitList label="Recordings" rowH={66} items={recordings || []} keyOf={(r) => r.name} empty={recordings === null ? "Reading the recordings…" : "Nothing recorded yet. Choose Stop after, then Record: the file is kept on the Pi, and Replay shows it on the scope."}
            render={(r) => (
              <div className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[13px]">{r.name}.csv</span>
                <span className="block truncate text-xs text-slate-600 dark:text-slate-400"
                  title={`${r.started ? new Date(r.started * 1000).toLocaleString() : "unknown start"}`}>
                  {r.started ? new Date(r.started * 1000).toLocaleString() : "unknown start"}, {Math.max(1, Math.round(r.bytes / 1024))} KB
                  {r.stats && r.stats.mean != null ? `, average ${si(r.stats.mean, r.unit || "")}` : ""}
                  {r.lost > 0 ? `, ${r.lost.toLocaleString("en-US")} lost` : ""}{r.recording ? ", recording now" : r.interrupted ? ", cut short" : ""}
                </span>
                {ask === r.name ? (
                  <span role="group" aria-label={`Delete ${r.name}?`} className="mt-0.5 flex items-center gap-1 text-xs">
                    <span>Delete it, with its saved wakes?</span>
                    <Button tone="danger" className="min-h-7! px-2! py-0.5!" onClick={() => deleteRecording(r.name)} aria-label={`Yes, delete ${r.name}`}>Delete</Button>
                    <Button tone="quiet" className="min-h-7! px-2! py-0.5!" onClick={() => setAsk(null)}>Keep it</Button>
                  </span>
                ) : (
                  <span className="mt-0.5 flex gap-1">
                    {replayCmd && <Button tone={replayOf === r.name ? "primary" : "quiet"} className="min-h-7! px-2! py-0.5!" aria-label={`Replay ${r.name}`} onClick={() => setReplayOf(r.name)}>Replay</Button>}
                    <Button tone="quiet" className="min-h-7! px-2! py-0.5!" onClick={() => saveCsv(r.name)} aria-label={`Save ${r.name} as a CSV file`}>Save CSV</Button>
                    {deleteCmd && <Button tone="quiet" className="min-h-7! px-2! py-0.5!" disabled={r.recording || pending} onClick={() => setAsk(r.name)} aria-label={`Delete ${r.name}`}>Delete</Button>}
                  </span>
                )}
              </div>
            )} />
        </section>
      )}
    </>
  );

  const backgroundNote = replayOf && running
    ? <p role="note" className="truncate text-[13px]">A session is {session.state} in the background ({clock(latest ? latest.t_s : 0)} of {clock(session.bound_s)}). <button type="button" className="text-blue-700 underline dark:text-blue-300" onClick={() => setReplayOf(null)}>Back to live</button></p>
    : null;
  const shownNotes = <>{backgroundNote}{noteLine}</>;
  const mode = replayOf ? "replay" : "live";
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <h2 className="sr-only">Meter</h2>
      {feedState.state !== "live" && (
        <p role="note" className="rounded-md bg-amber-100 px-3 py-1 text-[13px] text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          Live updates are not arriving ({feedState.state}), so the figures below may be behind.
        </p>
      )}
      <Supply commands={commands} shown={profiler} running={running} session={session} reload={loadProfiler} onDone={onDone} />
      <section aria-label="Current monitor" className="relative flex min-h-0 flex-1 flex-col rounded-lg border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-slate-200 px-4 py-2 dark:border-slate-800">
          <span aria-hidden="true" className="h-5 w-2 rounded-sm bg-amber-600 dark:bg-amber-400" />
          <h2 className="text-[15px] font-semibold">Current monitor</h2>
          <span className="hidden text-[13px] text-slate-600 dark:text-slate-400 lg:inline">Power profiler, 100,000 samples a second</span>
          <div role="group" aria-label="What the scope shows" className="flex gap-1">
            {[["live", "Live"], ...(replayOf ? [["replay", `Replay: ${replayOf}`]] : [])].map(([key, text]) => (
              <Button key={key} tone={mode === key ? "primary" : "quiet"} className="min-h-8! px-3! py-1!" aria-pressed={mode === key} onClick={() => { if (key === "live") setReplayOf(null); }}>{text}</Button>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1">
            {scopeRecording && <Button tone="quiet" className="min-h-8! px-3! py-1!" aria-expanded={drawer === "stages"} onClick={() => setDrawer(drawer === "stages" ? null : "stages")}>Stages</Button>}
            {session && session.session > 0 && <Button tone="quiet" className="min-h-8! px-3! py-1!" aria-expanded={drawer === "session"} onClick={() => setDrawer(drawer === "session" ? null : "session")}>Session</Button>}
            <button type="button" onClick={() => nav.help("meter")} aria-label="Help: Meter" title="Help: Meter"
              className="grid h-8 w-8 place-items-center rounded-full border border-slate-300 text-sm font-semibold text-slate-700 hover:bg-slate-200 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">?</button>
          </div>
        </div>
        {mode === "live" ? (
          <LiveScope key={session ? session.session : 0} buckets={buckets} unit={unit} current figures={figures} lead={lead} tail={tail} notes={shownNotes} side={side}
            marks={cyc && cyc.found ? cyc.starts.map((t) => ({ key: `c${t}`, t })) : []} shades={cyc && cyc.found ? cyc.spans.map((s) => ({ key: `s${s.t0}`, t0: s.t0, t1: s.t1 })) : []}
            empty="No readings yet. Press Watch to look, or Record to keep it and replay it afterwards." />
        ) : (
          <ReplayScope key={replayOf} commands={commands} recording={replayOf} volts={volts} lead={lead} tail={tail} notes={shownNotes} side={side} />
        )}
        {drawer === "trigger" && (
          <Drawer title="The trigger: what counts as a wake" onClose={() => setDrawer(null)}>
            <Trigger bare values={trigger} onChange={setTrigger} forced={forced} found={session && session.session > 0 ? { sleep_a: session.sleep_a, awake_a: session.awake_a } : null}
              running={running} busy={pending} onApply={() => run(wakeSet, wake.args)} />
          </Drawer>
        )}
        {drawer === "life" && (
          <Drawer title="Battery life" wide onClose={() => setDrawer(null)}>
            <div className="flex flex-wrap items-end gap-3">
              <label className="block w-44"><span className="mb-1 block text-sm font-medium">Battery capacity, mAh</span>
                <input className={inputClass} inputMode="decimal" value={capacity} onChange={(e) => setCapacity(e.target.value)} aria-label="Battery capacity in milliamp hours" /></label>
              <div role="group" aria-label="Worked out from" className="flex gap-2 pb-px">
                {[["whole", "Whole recording"], ["cycle", "Wake/sleep cycle"]].map(([key, text]) => (
                  <Button key={key} tone={lifeBy === key ? "primary" : "quiet"} aria-pressed={lifeBy === key} onClick={() => setLifeBy(key)}>{text}</Button>
                ))}
              </div>
            </div>
            <p className="break-words text-[15px]" aria-label="Battery life">{lifeSentence(lifeBy, cyc, life, unit)}</p>
            {cyc && cyc.found && <CycleTable cyc={cyc} unit={unit} volts={volts} />}
          </Drawer>
        )}
        {drawer === "stages" && scopeRecording && (
          <Drawer title="Stages across the wakes, each wake at full rate" wide onClose={() => setDrawer(null)}>
            <Scope key={`${scopeRecording}:${session ? session.session : 0}`} commands={commands} count={session ? session.captures || 0 : 0} session={session} recording={scopeRecording} />
          </Drawer>
        )}
        {drawer === "session" && session && (
          <Drawer title="The session's whole record" onClose={() => setDrawer(null)}><Json value={session} /></Drawer>
        )}
      </section>
    </div>
  );
}

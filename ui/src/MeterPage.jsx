import { useCallback, useEffect, useRef, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { createFeed } from "./feed.js";
import { Button, Card, Field, Json, PageHeader, cx, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";
import { Envelope, Stats } from "./meter/envelope.js";
import { findCycle } from "./meter/cycle.js";
import { CycleTable } from "./meter/Cycle.jsx";
import { Scope } from "./meter/Scope.jsx";
import { batteryLife, bandChart, clock, headline, lapse, si } from "./meter/format.js";
import { Profiler } from "./meter/Profiler.jsx";

// The Meter page (docs/specs/meter-page.md, docs/specs/ppk2.md): power over minutes to hours, from the multimeter or the Nordic Power Profiler. A session runs on the Pi;
// this page only listens to it. Readings arrive on the live feed and are kept in the same bounded envelope the server keeps, so a 12 hour session is as cheap to hold as a
// 1 minute one. There is no timer here: the clock shown is the latest reading's own time.

const DURATIONS = [[60, "1 minute"], [600, "10 minutes"], [3600, "1 hour"], [14400, "4 hours"], [43200, "12 hours"]];
const isCurrent = (fn) => /^CURR/i.test(fn || "");
const RUNNING = ["watching", "recording"];
const SOURCES = { meter: "Multimeter", ppk2: "Power profiler" };

function Chart({ buckets, unit, marks = [], spans = [] }) {
  const c = bandChart(buckets, 480, 120, unit, marks, spans);
  if (!c) return <p className="rounded-md bg-slate-100 px-3 py-6 text-center text-[13px] text-slate-600 dark:bg-slate-950 dark:text-slate-400">No readings yet. Press Watch or Record.</p>;
  return (
    <figure className="space-y-1">
      <svg viewBox="0 0 480 120" preserveAspectRatio="none" role="img" aria-label={`Readings over ${clock(c.span)}, from ${c.lo} to ${c.hi}`}
        className="h-40 w-full rounded-md bg-slate-100 dark:bg-slate-950">
        {c.shades.map((s) => (
          <rect key={s.x} x={s.x} y="0" width={s.w} height="120" data-testid="wake-span" className="fill-amber-200/70 dark:fill-amber-500/25" />
        ))}
        <polygon points={c.band} className="fill-blue-200 stroke-blue-300 dark:fill-blue-900 dark:stroke-blue-700" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <polyline points={c.mean} fill="none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" className="stroke-blue-700 dark:stroke-blue-300" />
        {c.marks.map((x) => (
          <line key={x} x1={x} x2={x} y1="0" y2="120" strokeWidth="1" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" data-testid="wake-mark" className="stroke-amber-600 dark:stroke-amber-400" />
        ))}
      </svg>
      <figcaption className="flex flex-wrap justify-between gap-x-4 text-xs text-slate-600 dark:text-slate-400">
        <span>bottom of the chart: {c.axisLo}</span>
        <span>top: {c.axisHi}</span>
        <span>{clock(c.span)} shown, the band is lowest to highest, the line the mean</span>
      </figcaption>
    </figure>
  );
}

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

function Figure({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-600 dark:text-slate-400">{label}</dt>
      <dd className="truncate text-[17px] font-semibold">{value}</dd>
    </div>
  );
}

export default function MeterPage({ commands, onDone }) {
  const find = useCallback((name) => (commands || []).find((c) => c.name === name) || null, [commands]);
  const [setting, setSetting] = useState(null);
  const [profiler, setProfiler] = useState(null);
  const [session, setSession] = useState(null);
  const [latest, setLatest] = useState(null);
  const [, bump] = useState(0);
  const [recordings, setRecordings] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [bound, setBound] = useState("600");
  const [recName, setRecName] = useState("");
  const [capacity, setCapacity] = useState("");
  const [openRec, setOpenRec] = useState(null);                       // a finished recording whose wakes and stages are open on the scope (a restart forgets which the last session was)
  const [lifeBy, setLifeBy] = useState("whole");                       // "whole": the Average figure; "cycle": the mean over whole wake/sleep cycles (cycle.js)
  const [chosen, setChosen] = useState(null);
  const [feedState, setFeedState] = useState({ state: "connecting" });
  const env = useRef(new Envelope());
  const stats = useRef(new Stats());
  const number = useRef(0);

  const available = ["meter", "ppk2"].filter((s) => find(`${s}.show`));
  const running = Boolean(session && RUNNING.includes(session.state));
  // The instrument a new session will read: the one a running session is reading, else the one chosen, else the first this bench has.
  const source = running && session.source ? session.source : chosen && available.includes(chosen) ? chosen : available[0] || "meter";
  // The instrument the figures on screen came from.
  const shownSource = (session && session.session > 0 && session.source) || (latest && latest.source) || source;

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

  const loadSetting = useCallback(async () => {
    const cmd = find("meter.show");
    if (!cmd) return;
    try { setSetting(await readJson(cmd.path)); } catch (e) { setSetting({ status: 0, body: { message: String(e) } }); }
  }, [find]);

  const loadProfiler = useCallback(async () => {
    const cmd = find("ppk2.show");
    if (!cmd) return;
    try { setProfiler(await readJson(cmd.path)); } catch (e) { setProfiler({ status: 0, body: { message: String(e) } }); }
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
          if (!RUNNING.includes(st.state)) loadProfiler();          // the unit is free again: ask it what it is set to now
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
      loadSetting();
      if (!(st && RUNNING.includes(st.state))) loadProfiler();
      if (!live) return;
      feed = createFeed({ feeds: ["meter"], from: d ? { meter: { cursor: d.cursor, epoch: d.epoch } } : undefined, onPage, onState: setFeedState });
      feed.start();
      bump((n) => n + 1);
    })();
    return () => { live = false; if (feed) feed.stop(); };
  }, [find, loadProfile, loadRecordings, loadSetting, loadProfiler]);

  async function run(cmd, args) {
    setOutcome({ pending: true });
    try {
      const r = await writeJson(cmd.path, args || {});
      setOutcome(r.ok ? null : r);
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  async function download(name) {
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

  const watch = find("meter.watch");
  const record = find("meter.record");
  const stop = find("meter.stop");
  const pending = Boolean(outcome && outcome.pending);
  const fn = (session && session.function) || (shownSource === "ppk2" ? "CURR" : (setting && setting.body && setting.body.data && setting.body.data.function) || "");
  const unit = (latest && latest.unit) || (session && session.unit) || (shownSource === "ppk2" ? "A" : (setting && setting.body && setting.body.data && setting.body.data.unit) || "");
  const current = isCurrent(fn);
  const figures = stats.current.toObject(current);
  const capacityMah = Number(capacity);
  const buckets = env.current.snapshot();
  const unitFacts = profiler && profiler.body && profiler.body.data;           // what the unit said when last asked (it cannot be asked while a session holds it)
  const volts = shownSource === "ppk2" && unitFacts && unitFacts.mode === "source" && unitFacts.supply === "on" ? unitFacts.vdd_mv / 1000 : null;
  const cyc = current && lifeBy === "cycle" ? findCycle(buckets) : null;
  const life = current ? batteryLife(lifeBy === "cycle" ? (cyc && cyc.found ? cyc.mean : null) : figures.mean, capacityMah) : "";
  const sd = setting && setting.body && setting.body.data && Object.keys(setting.body.data).length ? setting.body : null;
  const word = shownSource === "ppk2" ? "samples" : "readings";
  const count = figures.readings.toLocaleString("en-US");

  if (!watch) {
    return (
      <div className="space-y-4">
        <PageHeader title="Meter" topic="meter" />
        <p role="status" className="text-[15px] text-slate-600 dark:text-slate-400">The meter's live sessions are not available on this bench: they need the readings stream to be configured (streams.meter).</p>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <PageHeader title="Meter" topic="meter" />
      {feedState.state !== "live" && (
        <p role="note" className="rounded-md bg-amber-100 px-3 py-2 text-[13px] text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          Live updates are not arriving ({feedState.state}), so the figures below may be behind.
        </p>
      )}

      {available.length > 1 && (
        <div role="group" aria-label="Instrument" className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] text-slate-600 dark:text-slate-400">Read the</span>
          {available.map((s) => (
            <Button key={s} tone={source === s ? "primary" : "quiet"} aria-pressed={source === s} disabled={running} onClick={() => setChosen(s)}>{SOURCES[s]}</Button>
          ))}
        </div>
      )}

      {source === "meter" && find("meter.show") && (
        <Card title="The meter" aside={<Button tone="quiet" onClick={loadSetting}>Check again</Button>}>
          {sd ? (
            <div className="space-y-1">
              <p className="break-words text-[15px] font-medium">{headline("meter.show", sd.data)}</p>
              {sd.data.in_series && <p className="text-[13px] text-amber-800 dark:text-amber-300">Set to current: the board is in series with the meter, so the meter's own settings must not be changed while it runs.</p>}
              <p className="text-[13px] text-slate-600 dark:text-slate-400">Function, range and integration time are set at the meter's front panel. A lower integration time shows faster changes.</p>
            </div>
          ) : setting ? <ResultView outcome={setting} /> : <p className="text-[13px] text-slate-600 dark:text-slate-400">Asking the meter…</p>}
        </Card>
      )}
      {source === "ppk2" && find("ppk2.show") && (
        <Profiler commands={commands} shown={profiler} running={running} session={session} reload={loadProfiler} onDone={onDone} />
      )}

      <Card title="Power over time">
        <div className="space-y-4">
          <div>
            <p className="text-xs text-slate-600 dark:text-slate-400">Now</p>
            <p role="status" aria-label="Latest reading" className="break-words text-4xl font-semibold leading-tight">
              {latest ? (latest.overload ? "overload" : si(latest.value, latest.unit)) : "—"}
            </p>
            <p className="text-[13px] text-slate-600 dark:text-slate-400" aria-label="Session">
              {running
                ? `${session.state}, ${clock(latest ? latest.t_s : 0)} of ${clock(session.bound_s)}, ${count} ${word}${session.name ? `, to ${session.name}.csv` : ""}`
                : session && session.session > 0
                  ? `${session.state === "error" ? "ended by a fault" : "ended"}: ${session.reason}. ${count} ${word}`
                  : "No session has run since the service started."}
            </p>
          </div>

          <div className="flex flex-wrap items-end gap-2">
            <Field label="For">
              <select className={cx(inputClass, "w-40")} value={bound} onChange={(e) => setBound(e.target.value)} disabled={running} aria-label="How long to run">
                {DURATIONS.map(([s, label]) => <option key={s} value={String(s)}>{label}</option>)}
              </select>
            </Field>
            <Button disabled={running || pending} onClick={() => run(watch, { bound_s: Number(bound), source })}>Watch</Button>
            {record && (
              <>
                <div className="w-44"><Field label="Recording name"><input className={inputClass} value={recName} placeholder="the start time" disabled={running}
                  onChange={(e) => setRecName(e.target.value.toLowerCase())} aria-label="Name for the recording" autoComplete="off" spellCheck="false" /></Field></div>
                <Button tone="quiet" disabled={running || pending} onClick={() => run(record, { bound_s: Number(bound), source, ...(recName.trim() ? { name: recName.trim() } : {}) })}>Record</Button>
              </>
            )}
            {stop && <Button tone="danger" disabled={!running || pending} onClick={() => run(stop)}>Stop</Button>}
          </div>
          {outcome && !outcome.pending && <ResultView outcome={outcome} />}

          <Chart buckets={buckets} unit={unit} marks={cyc && cyc.found ? cyc.starts : []} spans={cyc && cyc.found ? cyc.spans : []} />

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4" aria-label="Figures">
            <Figure label="Lowest" value={figures.min === null ? "—" : si(figures.min, unit)} />
            <Figure label="Highest" value={figures.max === null ? "—" : si(figures.max, unit)} />
            <Figure label="Average" value={figures.mean === null ? "—" : si(figures.mean, unit)} />
            {current && <Figure label="Charge used" value={`${Number(figures.charge_mah.toPrecision(4))} mAh`} />}
          </dl>

          {shownSource === "ppk2" && session && session.lost > 0 && (
            <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">
              {session.lost.toLocaleString("en-US")} samples were lost between the unit and the Pi, so the charge is a little low.
            </p>
          )}
          {shownSource === "ppk2" && session && session.rate_low && (
            <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">
              The samples are arriving slower than the unit sends them: some may be missing without the counter showing it.
            </p>
          )}
          {shownSource === "ppk2" && session && session.filtered > 0 && (
            <p className="text-[13px] text-slate-600 dark:text-slate-400">
              {session.filtered.toLocaleString("en-US")} samples taken just after the unit changed range were left out, as they are not the current.
            </p>
          )}
          {current && figures.mean !== null && figures.mean < 0 && (
            <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">
              The current reads negative: it flows the other way through the {shownSource === "ppk2" ? "unit" : "meter"}, so the leads are probably the wrong way round. The numbers are right, only their sign is reversed.
            </p>
          )}
          {current && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-end gap-3">
                <div className="w-44"><Field label="Battery capacity, mAh"><input className={inputClass} inputMode="decimal" value={capacity} onChange={(e) => setCapacity(e.target.value)} aria-label="Battery capacity in milliamp hours" /></Field></div>
                <div role="group" aria-label="Worked out from" className="flex gap-2 pb-px">
                  {[["whole", "Whole recording"], ["cycle", "Wake/sleep cycle"]].map(([key, text]) => (
                    <Button key={key} tone={lifeBy === key ? "primary" : "quiet"} aria-pressed={lifeBy === key} onClick={() => setLifeBy(key)}>{text}</Button>
                  ))}
                </div>
              </div>
              <p className="break-words text-[15px]" aria-label="Battery life">{lifeSentence(lifeBy, cyc, life, unit)}</p>
              {cyc && cyc.found && <CycleTable cyc={cyc} unit={unit} volts={volts} />}
            </div>
          )}
          <p className="text-[13px] text-slate-600 dark:text-slate-400">
            {shownSource === "ppk2"
              ? "The power profiler takes 100,000 samples a second. Each point on the chart is 0.2 s: its band runs from the lowest to the highest sample, so a burst of a few milliseconds shows as the top of the band."
              : "This meter gives one reading per integration time and averages what happens inside it, so it shows sleep and awake levels and cycles of seconds or more. A radio burst of a few milliseconds needs the Nordic Power Profiler."}
          </p>
        </div>
      </Card>

      {(shownSource === "ppk2" || openRec) && (
        <Scope key={`${openRec || ""}:${session ? session.session : 0}`} commands={commands} count={session ? session.captures || 0 : 0} session={session}
          recording={openRec || (session && session.mode === "record" && session.name ? session.name : null)} />
      )}

      {record && (
        <Card title="Recordings">
          {recordings === null ? <p className="text-[13px] text-slate-600 dark:text-slate-400">Reading the recordings…</p>
            : recordings.length === 0 ? <p className="text-[13px] text-slate-600 dark:text-slate-400">Nothing recorded yet. Choose how long and press Record: the file is kept on the Pi and can be downloaded here.</p> : (
              <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="Recordings">
                {recordings.map((r) => (
                  <li key={r.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[13px]">{r.name}.csv</span>
                      <span className="block text-xs text-slate-600 dark:text-slate-400">
                        {r.started ? new Date(r.started * 1000).toLocaleString() : "unknown start"}, {Math.max(1, Math.round(r.bytes / 1024))} KB
                        {available.length > 1 ? `, ${(SOURCES[r.source] || r.source).toLowerCase()}` : ""}
                        {r.stats ? `, ${r.stats.readings.toLocaleString("en-US")} ${r.source === "ppk2" ? "samples" : "readings"}` : ""}{r.stats && r.stats.mean != null ? `, average ${si(r.stats.mean, r.unit || "")}` : ""}
                        {r.lost > 0 ? `, ${r.lost.toLocaleString("en-US")} samples lost` : ""}
                        {r.recording ? ", recording now" : r.interrupted ? ", cut short" : ""}
                      </span>
                    </span>
                    {r.source === "ppk2" && <Button tone="quiet" onClick={() => setOpenRec(r.name)} aria-label={`Open the wakes of ${r.name}`}>Wakes</Button>}
                    <Button tone="quiet" onClick={() => download(r.name)} aria-label={`Download ${r.name}`}>Download</Button>
                  </li>
                ))}
              </ul>
            )}
        </Card>
      )}
      {session && session.session > 0 && (
        <details className="text-[13px]">
          <summary className="min-h-8 cursor-pointer py-1 text-blue-700 underline dark:text-blue-300">The session's whole record</summary>
          <div className="mt-2"><Json value={session} /></div>
        </details>
      )}
    </div>
  );
}

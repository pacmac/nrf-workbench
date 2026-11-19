import { useCallback, useEffect, useRef, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { createFeed } from "./feed.js";
import { Button, Card, Field, Json, PageHeader, cx, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";
import { Envelope, Stats } from "./meter/envelope.js";
import { batteryLife, bandChart, clock, headline, si } from "./meter/format.js";

// The Meter page (docs/specs/meter-page.md): power over minutes to hours. A session runs on the Pi; this page only listens to it. Readings arrive on the live feed and
// are kept in the same bounded envelope the server keeps, so a 12 hour session is as cheap to hold as a 1 minute one. There is no timer here: the clock shown is the
// latest reading's own time.

const DURATIONS = [[60, "1 minute"], [600, "10 minutes"], [3600, "1 hour"], [14400, "4 hours"], [43200, "12 hours"]];
const isCurrent = (fn) => /^CURR/i.test(fn || "");
const RUNNING = ["watching", "recording"];

function Chart({ buckets, unit }) {
  const c = bandChart(buckets, 480, 120, unit);
  if (!c) return <p className="rounded-md bg-slate-100 px-3 py-6 text-center text-[13px] text-slate-600 dark:bg-slate-950 dark:text-slate-400">No readings yet. Press Watch or Record.</p>;
  return (
    <figure className="space-y-1">
      <svg viewBox="0 0 480 120" preserveAspectRatio="none" role="img" aria-label={`Readings over ${clock(c.span)}, from ${c.lo} to ${c.hi}`}
        className="h-40 w-full rounded-md bg-slate-100 dark:bg-slate-950">
        <polygon points={c.band} className="fill-blue-200 stroke-blue-300 dark:fill-blue-900 dark:stroke-blue-700" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <polyline points={c.mean} fill="none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" className="stroke-blue-700 dark:stroke-blue-300" />
      </svg>
      <figcaption className="flex flex-wrap justify-between gap-x-4 text-xs text-slate-600 dark:text-slate-400">
        <span>bottom of the chart: {c.axisLo}</span>
        <span>top: {c.axisHi}</span>
        <span>{clock(c.span)} shown, the band is lowest to highest, the line the mean</span>
      </figcaption>
    </figure>
  );
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
  const [session, setSession] = useState(null);
  const [latest, setLatest] = useState(null);
  const [, bump] = useState(0);
  const [recordings, setRecordings] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [bound, setBound] = useState("600");
  const [recName, setRecName] = useState("");
  const [capacity, setCapacity] = useState("");
  const [feedState, setFeedState] = useState({ state: "connecting" });
  const env = useRef(new Envelope());
  const stats = useRef(new Stats());
  const number = useRef(0);

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
          if (!RUNNING.includes(st.state) || st.mode === "record") loadRecordings();
        } else if (e.type === "meter.reading") {
          const r = e.payload;
          if (r.session !== number.current) continue;               // a reading of an earlier session
          if (r.overload) stats.current.overload();
          else { env.current.add(r.t_s, r.value); stats.current.add(r.t_s, r.value); }
          setLatest(r);
        }
      }
      if (p.reset) loadProfile().then(() => bump((n) => n + 1));    // the service restarted: its cursors are new, so start again from its profile
      bump((n) => n + 1);
    };
    (async () => {
      const d = await loadProfile();
      if (!live) return;
      const s = find("meter.session");
      if (s) { const r = await readJson(s.path); if (live && r.ok && r.body) setSession(r.body.data); }
      loadRecordings();
      loadSetting();
      if (!live) return;
      feed = createFeed({ feeds: ["meter"], from: d ? { meter: { cursor: d.cursor, epoch: d.epoch } } : undefined, onPage, onState: setFeedState });
      feed.start();
      bump((n) => n + 1);
    })();
    return () => { live = false; if (feed) feed.stop(); };
  }, [find, loadProfile, loadRecordings, loadSetting]);

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
  const running = Boolean(session && RUNNING.includes(session.state));
  const pending = Boolean(outcome && outcome.pending);
  const fn = (session && session.function) || (setting && setting.body && setting.body.data && setting.body.data.function) || "";
  const unit = (latest && latest.unit) || (session && session.unit) || (setting && setting.body && setting.body.data && setting.body.data.unit) || "";
  const current = isCurrent(fn);
  const figures = stats.current.toObject(current);
  const capacityMah = Number(capacity);
  const life = current ? batteryLife(figures.mean, capacityMah) : "";
  const sd = setting && setting.body && setting.body.data && Object.keys(setting.body.data).length ? setting.body : null;

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

      <Card title="The meter" aside={<Button tone="quiet" onClick={loadSetting}>Check again</Button>}>
        {sd ? (
          <div className="space-y-1">
            <p className="break-words text-[15px] font-medium">{headline("meter.show", sd.data)}</p>
            {sd.data.in_series && <p className="text-[13px] text-amber-800 dark:text-amber-300">Set to current: the board is in series with the meter, so the meter's own settings must not be changed while it runs.</p>}
            <p className="text-[13px] text-slate-600 dark:text-slate-400">Function, range and integration time are set at the meter's front panel. A lower integration time shows faster changes.</p>
          </div>
        ) : setting ? <ResultView outcome={setting} /> : <p className="text-[13px] text-slate-600 dark:text-slate-400">Asking the meter…</p>}
      </Card>

      <Card title="Power over time">
        <div className="space-y-4">
          <div>
            <p className="text-xs text-slate-600 dark:text-slate-400">Now</p>
            <p role="status" aria-label="Latest reading" className="break-words text-4xl font-semibold leading-tight">
              {latest ? (latest.overload ? "overload" : si(latest.value, latest.unit)) : "—"}
            </p>
            <p className="text-[13px] text-slate-600 dark:text-slate-400" aria-label="Session">
              {running
                ? `${session.state}, ${clock(latest ? latest.t_s : 0)} of ${clock(session.bound_s)}, ${figures.readings} readings${session.name ? `, to ${session.name}.csv` : ""}`
                : session && session.session > 0
                  ? `${session.state === "error" ? "ended by a fault" : "ended"}: ${session.reason}. ${figures.readings} readings`
                  : "No session has run since the service started."}
            </p>
          </div>

          <div className="flex flex-wrap items-end gap-2">
            <Field label="For">
              <select className={cx(inputClass, "w-40")} value={bound} onChange={(e) => setBound(e.target.value)} disabled={running} aria-label="How long to run">
                {DURATIONS.map(([s, label]) => <option key={s} value={String(s)}>{label}</option>)}
              </select>
            </Field>
            <Button disabled={running || pending} onClick={() => run(watch, { bound_s: Number(bound) })}>Watch</Button>
            {record && (
              <>
                <div className="w-44"><Field label="Recording name"><input className={inputClass} value={recName} placeholder="the start time" disabled={running}
                  onChange={(e) => setRecName(e.target.value.toLowerCase())} aria-label="Name for the recording" autoComplete="off" spellCheck="false" /></Field></div>
                <Button tone="quiet" disabled={running || pending} onClick={() => run(record, { bound_s: Number(bound), ...(recName.trim() ? { name: recName.trim() } : {}) })}>Record</Button>
              </>
            )}
            {stop && <Button tone="danger" disabled={!running || pending} onClick={() => run(stop)}>Stop</Button>}
          </div>
          {outcome && !outcome.pending && <ResultView outcome={outcome} />}

          <Chart buckets={env.current.snapshot()} unit={unit} />

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4" aria-label="Figures">
            <Figure label="Lowest" value={figures.min === null ? "—" : si(figures.min, unit)} />
            <Figure label="Highest" value={figures.max === null ? "—" : si(figures.max, unit)} />
            <Figure label="Average" value={figures.mean === null ? "—" : si(figures.mean, unit)} />
            {current && <Figure label="Charge used" value={`${Number(figures.charge_mah.toPrecision(4))} mAh`} />}
          </dl>

          {current && (
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-44"><Field label="Battery capacity, mAh"><input className={inputClass} inputMode="decimal" value={capacity} onChange={(e) => setCapacity(e.target.value)} aria-label="Battery capacity in milliamp hours" /></Field></div>
              <p className="pb-2 text-[15px]" aria-label="Battery life">{life ? `At this average it lasts ${life}.` : "Type a capacity to see how long it lasts at the average above."}</p>
            </div>
          )}
          <p className="text-[13px] text-slate-600 dark:text-slate-400">
            This meter gives one reading per integration time and averages what happens inside it, so it shows sleep and awake levels and cycles of seconds or more. A radio burst of a few
            milliseconds needs the Nordic Power Profiler.
          </p>
        </div>
      </Card>

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
                        {r.stats ? `, ${r.stats.readings} readings` : ""}{r.stats && r.stats.mean != null ? `, average ${si(r.stats.mean, r.unit || "")}` : ""}
                        {r.recording ? ", recording now" : r.interrupted ? ", cut short" : ""}
                      </span>
                    </span>
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

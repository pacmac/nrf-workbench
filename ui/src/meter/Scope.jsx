import { useCallback, useEffect, useRef, useState } from "react";
import { readJson, writeJson } from "../api.js";
import { Button, Card, Field, inputClass } from "../ui.jsx";
import { chargeText, si } from "./format.js";
import { scopeChart, timeLabel } from "./scope.js";

// One wake the power profiler kept at its full 100,000 samples a second, as a scope trace, and the stages of it a person has named (docs/specs/ppk2-stages.md, stages 1 and 2).
// The Pi keeps the raw samples and answers any stretch of a wake as an exact envelope, so zooming asks again for that stretch and nothing is thinned. `count` is how many wakes
// the session has finished (it arrives on the live feed), so the list is asked for again when it changes and never polled. `recording` is the recording the wakes belong to:
// stages are kept with a recording, and its wakes are read from where it saved them.

const W = 480;
const H = 160;
const POINTS = 1000;                               // a wide chart's pixels: one point a pixel at most
const ROUND_S = 1e-4;                              // a stage's ends are rounded to 0.1 ms: ten samples, finer than a person can place a drag
// A stage's colour on the trace and on its chip; literal class names so the stylesheet keeps them.
const TINTS = [
  { rect: "fill-emerald-300/50 dark:fill-emerald-500/25", chip: "bg-emerald-200 text-emerald-950 dark:bg-emerald-900 dark:text-emerald-100" },
  { rect: "fill-violet-300/50 dark:fill-violet-500/25", chip: "bg-violet-200 text-violet-950 dark:bg-violet-900 dark:text-violet-100" },
  { rect: "fill-rose-300/50 dark:fill-rose-500/25", chip: "bg-rose-200 text-rose-950 dark:bg-rose-900 dark:text-rose-100" },
  { rect: "fill-sky-300/50 dark:fill-sky-500/25", chip: "bg-sky-200 text-sky-950 dark:bg-sky-900 dark:text-sky-100" },
  { rect: "fill-lime-300/50 dark:fill-lime-500/25", chip: "bg-lime-200 text-lime-950 dark:bg-lime-900 dark:text-lime-100" },
  { rect: "fill-fuchsia-300/50 dark:fill-fuchsia-500/25", chip: "bg-fuchsia-200 text-fuchsia-950 dark:bg-fuchsia-900 dark:text-fuchsia-100" },
];

const find = (commands, name) => (commands || []).find((c) => c.name === name) || null;
const share = (x) => (x === null || x === undefined ? "—" : x < 0.001 ? "under 0.1 %" : `${(x * 100).toFixed(x < 0.1 ? 1 : 0)} %`);

export function Scope({ commands, count, session, recording }) {
  const list = find(commands, "meter.captures");
  const one = find(commands, "meter.capture");
  const setStage = find(commands, "meter.stage_set");
  const delStage = find(commands, "meter.stage_delete");
  const stagesCmd = find(commands, "meter.stages");
  const [wakes, setWakes] = useState(null);
  const [n, setN] = useState(null);
  const [win, setWin] = useState(null);             // {t0, t1} seconds from the wake's start, or null for the whole capture
  const [data, setData] = useState(null);
  const [log, setLog] = useState(false);
  const [drag, setDrag] = useState(null);           // {a, b} in the chart's own units while the pointer is down
  const [problem, setProblem] = useState("");
  const [table, setTable] = useState(null);         // meter.stages' answer for the recording
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [stageProblem, setStageProblem] = useState("");
  const svg = useRef(null);
  const rec = recording ? { recording } : {};

  const loadList = useCallback(async () => {
    if (!list) return;
    try {
      const r = await readJson(list.path, rec);
      setWakes(r.ok && r.body && r.body.data ? r.body.data.captures : []);
    } catch (e) {
      setProblem(String(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, recording]);

  const loadTable = useCallback(async () => {
    if (!stagesCmd || !recording) { setTable(null); return; }
    try {
      const r = await readJson(stagesCmd.path, { recording });
      setTable(r.ok && r.body && r.body.data ? r.body.data : null);
    } catch (e) {
      setStageProblem(String(e));
    }
  }, [stagesCmd, recording]);

  useEffect(() => { loadList(); loadTable(); }, [loadList, loadTable, count, session && session.session]);

  useEffect(() => {
    if (!wakes || wakes.length === 0) { setN(null); return; }
    if (!wakes.some((w) => w.n === n)) { setN(wakes[wakes.length - 1].n); setWin(null); }
  }, [wakes, n]);

  useEffect(() => {
    if (n === null || !one) { setData(null); return undefined; }
    let live = true;
    (async () => {
      try {
        const q = { n, points: POINTS, ...rec, ...(win ? { t0: win.t0, t1: win.t1 } : {}) };
        const r = await readJson(one.path, q);
        if (!live) return;
        if (r.ok && r.body && r.body.data) { setData(r.body.data); setProblem(""); }
        else { setProblem(r.body && r.body.message ? r.body.message : "The wake could not be read."); }
      } catch (e) {
        if (live) setProblem(String(e));
      }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n, win, one, recording]);

  async function write(cmd, args) {
    setBusy(true);
    setStageProblem("");
    try {
      const r = await writeJson(cmd.path, args);
      if (!r.ok) setStageProblem(r.body && r.body.message ? r.body.message : "The change was refused.");
      else setName("");
      await loadTable();
    } catch (e) {
      setStageProblem(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!list || !one) return null;
  const chart = data ? scopeChart(data.buckets, W, H, { log, unit: "A" }) : null;
  const stages = table ? table.stages : [];
  const unitX = (e) => {
    const box = svg.current.getBoundingClientRect();
    return ((e.clientX - box.left) / box.width) * W;
  };
  function down(e) {
    if (!chart) return;
    svg.current.setPointerCapture(e.pointerId);
    const x = unitX(e);
    setDrag({ a: x, b: x });
  }
  function move(e) {
    if (drag) setDrag({ a: drag.a, b: unitX(e) });
  }
  function up() {
    if (!drag || !chart) return;
    const lo = Math.min(drag.a, drag.b);
    const hi = Math.max(drag.a, drag.b);
    setDrag(null);
    if (hi - lo >= 6) setWin({ t0: chart.timeAt(lo), t1: chart.timeAt(hi) });          // under 6 units wide is a click, not a zoom
  }
  const rounded = (t) => Number((Math.round(t / ROUND_S) * ROUND_S).toFixed(4));
  const span = win ? rounded(win.t1) - rounded(win.t0) : 0;
  const nameOk = name.trim().length >= 1 && name.trim().length <= 40;

  return (
    <Card title="The wake at full rate" aside={win ? <Button tone="quiet" onClick={() => setWin(null)}>Show all of it</Button> : null}>
      <div className="space-y-3">
        {wakes === null && <p className="text-[13px] text-slate-600 dark:text-slate-400">Asking for the wakes it kept…</p>}
        {wakes && wakes.length === 0 && (
          <p className="text-[13px] text-slate-600 dark:text-slate-400" role="status">
            No wake kept yet. One is kept once a block rises to at least twice the sleep current and falls quiet again, and the profiler's own 100,000 samples a second are kept for it.
          </p>
        )}
        {wakes && wakes.length > 0 && (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <div className="w-72 max-w-full">
                <Field label="Wake">
                  <select className={inputClass} aria-label="Wake" value={n === null ? "" : n} onChange={(e) => { setN(Number(e.target.value)); setWin(null); }}>
                    {wakes.map((w) => (
                      <option key={w.n} value={w.n}>wake {w.n}, {timeLabel(w.duration_s, w.duration_s)}, peak {si(w.peak, "A")}{w.truncated ? ", cut at 5 s" : ""}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <label className="flex items-center gap-2 pb-2 text-[15px]">
                <input type="checkbox" checked={log} onChange={(e) => setLog(e.target.checked)} aria-label="Log current axis" />
                Log axis
              </label>
            </div>
            {chart ? (
              <figure className="space-y-1">
                <svg ref={svg} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`Current of wake ${data.n}, from ${timeLabel(chart.t0, chart.span)} to ${timeLabel(chart.t1, chart.span)}`}
                  className="h-48 w-full touch-none rounded-md bg-slate-100 dark:bg-slate-950" style={{ cursor: "crosshair" }}
                  onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => setDrag(null)}>
                  {stages.filter((s) => s.end_s > chart.t0 && s.start_s < chart.t1).map((s) => {
                    const a = chart.at(s.start_s);
                    const b = chart.at(s.end_s);
                    return (
                      <rect key={s.name} x={a} y="0" width={Math.max(2, b - a)} height={H} data-testid="stage-span" className={TINTS[stages.indexOf(s) % TINTS.length].rect}>
                        <title>{s.name}</title>
                      </rect>
                    );
                  })}
                  <polygon points={chart.band} className="fill-blue-200 stroke-blue-300 dark:fill-blue-900 dark:stroke-blue-700" strokeWidth="1" vectorEffect="non-scaling-stroke" />
                  <polyline points={chart.mean} fill="none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" className="stroke-blue-700 dark:stroke-blue-300" />
                  {chart.zero !== null && (
                    <line x1={chart.zero} x2={chart.zero} y1="0" y2={H} strokeWidth="1" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" data-testid="wake-start" className="stroke-amber-600 dark:stroke-amber-400" />
                  )}
                  {drag && <rect x={Math.min(drag.a, drag.b)} y="0" width={Math.abs(drag.b - drag.a)} height={H} data-testid="zoom-selection" className="fill-amber-300/40 dark:fill-amber-400/20" />}
                </svg>
                <div className="relative h-5 text-xs text-slate-600 dark:text-slate-400" aria-hidden="true">
                  {chart.ticks.map((t) => {
                    const at = t.x / W;
                    const shift = at < 0.06 ? "" : at > 0.94 ? "-translate-x-full" : "-translate-x-1/2";          // a label at either end stays inside the card
                    return <span key={t.t} className={`absolute whitespace-nowrap ${shift}`} style={{ left: `${at * 100}%` }}>{t.label}</span>;
                  })}
                </div>
                <figcaption className="flex flex-wrap justify-between gap-x-4 text-xs text-slate-600 dark:text-slate-400">
                  <span>{chart.log ? `log scale, bottom ${chart.bottom}` : `bottom of the chart: ${chart.bottom}`}</span>
                  <span>top: {chart.top}</span>
                  <span>time from the wake's start{chart.zero !== null ? " (dashed line)" : ""}. Drag across the chart to zoom in.</span>
                </figcaption>
              </figure>
            ) : (
              <p className="text-[13px] text-slate-600 dark:text-slate-400">Reading the wake…</p>
            )}
            {stages.length > 0 && (
              <ul className="flex flex-wrap gap-2" aria-label="Stage colours">
                {stages.map((s, i) => <li key={s.name} className={`rounded px-2 py-0.5 text-xs ${TINTS[i % TINTS.length].chip}`}>{s.name}</li>)}
              </ul>
            )}
            {data && (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4" aria-label="Wake figures">
                <div className="min-w-0"><dt className="text-xs text-slate-600 dark:text-slate-400">Shown</dt><dd className="truncate text-[17px] font-semibold">{timeLabel(data.t1 - data.t0, data.t1 - data.t0)}</dd></div>
                <div className="min-w-0"><dt className="text-xs text-slate-600 dark:text-slate-400">Average</dt><dd className="truncate text-[17px] font-semibold">{si(data.mean, "A")}</dd></div>
                <div className="min-w-0"><dt className="text-xs text-slate-600 dark:text-slate-400">Peak</dt><dd className="truncate text-[17px] font-semibold">{si(data.peak, "A")}</dd></div>
                <div className="min-w-0"><dt className="text-xs text-slate-600 dark:text-slate-400">Charge</dt><dd className="truncate text-[17px] font-semibold">{chargeText(data.charge_c)}</dd></div>
              </dl>
            )}
            {data && data.lost > 0 && (
              <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{data.lost} samples were lost inside this wake, so its times are a little short.</p>
            )}

            <div className="space-y-3 border-t border-slate-200 pt-3 dark:border-slate-800">
              <h3 className="text-[15px] font-semibold">Stages</h3>
              {!recording && (
                <p className="text-[13px] text-slate-600 dark:text-slate-400" role="status">Stages are kept with a recording. Record, and the wakes and the stages you name are saved with it.</p>
              )}
              {recording && setStage && (
                win ? (
                  <div className="flex flex-wrap items-end gap-3">
                    <p className="pb-2 text-[13px]" aria-label="Selected stretch">Selected: {timeLabel(rounded(win.t0), span)} to {timeLabel(rounded(win.t1), span)}</p>
                    <div className="w-56 max-w-full"><Field label="Stage name"><input className={inputClass} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} aria-label="Stage name" autoComplete="off" /></Field></div>
                    <Button disabled={busy || !nameOk} onClick={() => write(setStage, { recording, name: name.trim(), start_s: rounded(win.t0), end_s: rounded(win.t1) })}>Add as a stage</Button>
                  </div>
                ) : (
                  <p className="text-[13px] text-slate-600 dark:text-slate-400">Drag across the chart to select a stretch, then name it: a stage is the same stretch of every wake, counted from the wake's start.</p>
                )
              )}
              {stageProblem && <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{stageProblem}</p>}
              {recording && stages.length > 0 && (
                <div className="space-y-2">
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[22rem] text-left" aria-label="Stages across the wakes">
                      <thead>
                        <tr className="border-b border-slate-200 text-xs text-slate-600 dark:border-slate-800 dark:text-slate-400">
                          <th scope="col" className="py-1 pr-3 font-normal">Stage</th>
                          <th scope="col" className="py-1 pr-3 font-normal">Average current</th>
                          <th scope="col" className="py-1 pr-3 font-normal">Peak</th>
                          <th scope="col" className="py-1 font-normal">Charge per wake</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
                        {stages.map((s, i) => (
                          <tr key={s.name}>
                            <th scope="row" className="py-2 pr-3 align-top font-normal">
                              <span className={`rounded px-2 py-0.5 text-[13px] font-semibold ${TINTS[i % TINTS.length].chip}`}>{s.name}</span>
                              <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{timeLabel(s.start_s, s.length_s)} to {timeLabel(s.end_s, s.length_s)}, {timeLabel(s.length_s, s.length_s)} long</div>
                              {delStage && <div className="mt-1"><Button tone="quiet" disabled={busy} onClick={() => write(delStage, { recording, name: s.name })} aria-label={`Remove the stage ${s.name}`}>Remove</Button></div>}
                            </th>
                            <td className="py-2 pr-3 align-top text-[15px] font-medium">
                              {s.mean === null ? "—" : si(s.mean, "A")}
                              {s.wakes > 1 && s.lowest_mean !== s.highest_mean && <div className="text-xs font-normal text-slate-600 dark:text-slate-400">{si(s.lowest_mean, "A")} to {si(s.highest_mean, "A")} across {s.wakes} wakes</div>}
                            </td>
                            <td className="py-2 pr-3 align-top text-[15px] font-medium">{s.peak === null ? "—" : si(s.peak, "A")}</td>
                            <td className="py-2 align-top text-[15px] font-medium">
                              {s.charge_c === null ? "—" : chargeText(s.charge_c)}
                              <div className="text-xs font-normal text-slate-600 dark:text-slate-400">{share(s.share)} of the named stages</div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-xs text-slate-600 dark:text-slate-400">
                    Over {table.wakes} saved {table.wakes === 1 ? "wake" : "wakes"}, from their raw samples. {table.untagged_charge_c === null ? "" : `The rest of a wake, not in a stage: ${chargeText(table.untagged_charge_c)}.`}
                  </p>
                </div>
              )}
            </div>
          </>
        )}
        {problem && <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{problem}</p>}
      </div>
    </Card>
  );
}

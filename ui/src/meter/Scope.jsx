import { useCallback, useEffect, useRef, useState } from "react";
import { readJson } from "../api.js";
import { Button, Card, Field, inputClass } from "../ui.jsx";
import { chargeText, si } from "./format.js";
import { scopeChart, timeLabel } from "./scope.js";

// One wake the power profiler kept at its full 100,000 samples a second, as a scope trace (docs/specs/ppk2-stages.md, stage 1). The Pi keeps the raw samples and answers any stretch of
// a wake as an exact envelope, so zooming asks again for that stretch and nothing is thinned. `count` is how many wakes the session has taken (it arrives on the live feed), so
// the list is asked for again when it changes and never polled.

const W = 480;
const H = 160;
const POINTS = 1000;                               // a wide chart's pixels: one point a pixel at most

const find = (commands, name) => (commands || []).find((c) => c.name === name) || null;

export function Scope({ commands, count, session }) {
  const list = find(commands, "meter.captures");
  const one = find(commands, "meter.capture");
  const [wakes, setWakes] = useState(null);
  const [n, setN] = useState(null);
  const [win, setWin] = useState(null);             // {t0, t1} seconds from the wake's start, or null for the whole capture
  const [data, setData] = useState(null);
  const [log, setLog] = useState(false);
  const [drag, setDrag] = useState(null);           // {a, b} in the chart's own units while the pointer is down
  const [problem, setProblem] = useState("");
  const svg = useRef(null);

  const loadList = useCallback(async () => {
    if (!list) return;
    try {
      const r = await readJson(list.path);
      setWakes(r.ok && r.body && r.body.data ? r.body.data.captures : []);
    } catch (e) {
      setProblem(String(e));
    }
  }, [list]);

  useEffect(() => { loadList(); }, [loadList, count, session && session.session]);

  useEffect(() => {
    if (!wakes || wakes.length === 0) { setN(null); return; }
    if (!wakes.some((w) => w.n === n)) { setN(wakes[wakes.length - 1].n); setWin(null); }
  }, [wakes, n]);

  useEffect(() => {
    if (n === null || !one) { setData(null); return; }
    let live = true;
    (async () => {
      try {
        const q = { n, points: POINTS, ...(win ? { t0: win.t0, t1: win.t1 } : {}) };
        const r = await readJson(one.path, q);
        if (!live) return;
        if (r.ok && r.body && r.body.data) { setData(r.body.data); setProblem(""); }
        else { setProblem(r.body && r.body.message ? r.body.message : "The wake could not be read."); }
      } catch (e) {
        if (live) setProblem(String(e));
      }
    })();
    return () => { live = false; };
  }, [n, win, one]);

  if (!list || !one) return null;
  const chart = data ? scopeChart(data.buckets, W, H, { log, unit: "A" }) : null;
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
          </>
        )}
        {problem && <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{problem}</p>}
      </div>
    </Card>
  );
}

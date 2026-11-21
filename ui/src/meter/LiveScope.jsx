import { useState } from "react";
import { clampWindow, isWhole, panWindow, windowStats, zoomWindow } from "./axes.js";
import { timeText } from "./EventPanel.jsx";
import { chargeText, si } from "./format.js";
import { ScopeBar } from "./ScopeBar.jsx";
import { TraceChart } from "./TraceChart.jsx";

// The live session on the scope (docs/specs/meter-page.md, "Live and replay"): the same trace and controls as a replay, drawn from the envelope this page keeps as the readings arrive, so zooming
// is local and asks the Pi for nothing. Shown whole until the person zooms; a zoomed stretch stays where it is while the session runs on. `figures` are the whole session's, exact; a zoomed
// stretch's are worked out from its buckets. `marks` and `shades` are what the cycle finder found (cycle.js).

const MIN_VIEW_S = 1.0;                  // five readings of the profiler's 0.2 s blocks: fewer is not a trace

function Figure({ label, value }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-slate-600 dark:text-slate-400">{label}</dt>
      <dd className="truncate text-[17px] font-semibold">{value}</dd>
    </div>
  );
}

export function LiveScope({ buckets, unit, current, figures, marks = [], shades = [], empty }) {
  const [win, setWin] = useState(null);
  const [log, setLog] = useState(false);
  if (!buckets || buckets.length === 0) {
    return <p role="status" className="flex h-64 items-center justify-center rounded-md bg-slate-100 px-3 text-center text-[13px] text-slate-600 dark:bg-slate-950 dark:text-slate-400">{empty}</p>;
  }
  const bounds = { t0: buckets[0].t0, t1: buckets[buckets.length - 1].t1 };
  const floor = Math.min(MIN_VIEW_S, bounds.t1 - bounds.t0);
  const view = win ? clampWindow(win, bounds, floor) : bounds;
  const isAll = isWhole(view, bounds);
  const set = (w) => setWin(isWhole(w, bounds) ? null : w);
  const zoom = (factor, anchor) => set(zoomWindow(view, bounds, factor, anchor, floor));
  const pan = (frac) => set(panWindow(view, bounds, frac, floor));
  const shown = isAll ? null : windowStats(buckets, view);
  const lowest = shown ? shown.lowest : figures.min;
  const highest = shown ? shown.highest : figures.max;
  const mean = shown ? shown.mean : figures.mean;
  const charge = shown ? shown.charge : null;
  return (
    <div className="space-y-3">
      <ScopeBar view={view} bounds={bounds} whole={isAll} tool="zoom" onTool={() => {}} canMark={false} log={log} onLog={setLog} onZoom={zoom} onPan={pan} onWhole={() => setWin(null)}
        atFloor={view.t1 - view.t0 <= floor + 1e-9} clock={(t) => timeText(t)} />
      <div tabIndex={0} className="rounded-md outline-none focus-visible:ring-2 focus-visible:ring-blue-500" aria-label="Scope trace, focus it to use the keys"
        onKeyDown={(e) => {
          const k = e.key;
          if (k === "ArrowLeft") pan(-0.25); else if (k === "ArrowRight") pan(0.25); else if (k === "+" || k === "=") zoom(2, 0.5); else if (k === "-" || k === "_") zoom(0.5, 0.5); else if (k === "0") setWin(null); else return;
          e.preventDefault();
        }}>
        <TraceChart buckets={buckets} view={view} unit={unit} log={log} tool="zoom" marks={marks} shades={shades} onZoom={(a, b) => set(clampWindow({ t0: a, t1: b }, bounds, floor))} onWheel={zoom}
          label={`Readings from ${timeText(view.t0)} to ${timeText(view.t1)}`} />
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4" aria-label="Figures">
        <Figure label="Lowest" value={lowest === null || lowest === undefined ? "—" : si(lowest, unit)} />
        <Figure label="Highest" value={highest === null || highest === undefined ? "—" : si(highest, unit)} />
        <Figure label="Average" value={mean === null || mean === undefined ? "—" : si(mean, unit)} />
        {current && <Figure label="Charge used" value={shown ? chargeText(charge) : `${Number(figures.charge_mah.toPrecision(4))} mAh`} />}
      </dl>
    </div>
  );
}

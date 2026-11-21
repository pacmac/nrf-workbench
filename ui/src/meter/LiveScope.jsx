import { useState } from "react";
import { clampWindow, isWhole, panWindow, windowStats, zoomWindow } from "./axes.js";
import { timeText } from "./EventPanel.jsx";
import { chargeText, si } from "./format.js";
import { Monitor, Readouts } from "./Monitor.jsx";
import { ScopeBar } from "./ScopeBar.jsx";
import { TraceChart } from "./TraceChart.jsx";

// The live session on the scope (docs/specs/meter-page.md, "Live and replay"): the same trace and controls as a replay, drawn from the envelope this page keeps as the readings arrive, so zooming
// is local and asks the Pi for nothing. Shown whole until the person zooms; a zoomed stretch stays where it is while the session runs on. `figures` are the whole session's, exact; a zoomed
// stretch's are worked out from its buckets. `marks` and `shades` are what the cycle finder found (cycle.js). `lead` and `tail` are readouts the page adds before and after the figures,
// `notes` what it has to say under the scope, `side` the controls in the right column.

const MIN_VIEW_S = 1.0;                  // five readings of the profiler's 0.2 s blocks: fewer is not a trace

export function LiveScope({ buckets, unit, current, figures, marks = [], shades = [], empty, lead = [], tail = [], notes, side }) {
  const [win, setWin] = useState(null);
  const [log, setLog] = useState(false);
  const none = !buckets || buckets.length === 0;
  const bounds = none ? null : { t0: buckets[0].t0, t1: buckets[buckets.length - 1].t1 };
  const floor = none ? MIN_VIEW_S : Math.min(MIN_VIEW_S, bounds.t1 - bounds.t0);
  const view = none ? null : win ? clampWindow(win, bounds, floor) : bounds;
  const isAll = none ? true : isWhole(view, bounds);
  const set = (w) => setWin(isWhole(w, bounds) ? null : w);
  const zoom = (factor, anchor) => set(zoomWindow(view, bounds, factor, anchor, floor));
  const pan = (frac) => set(panWindow(view, bounds, frac, floor));
  const shown = none || isAll ? null : windowStats(buckets, view);
  const lowest = shown ? shown.lowest : figures.min;
  const highest = shown ? shown.highest : figures.max;
  const mean = shown ? shown.mean : figures.mean;
  const charge = shown ? shown.charge : null;
  const dash = (v) => (v === null || v === undefined ? "—" : si(v, unit));
  const cells = [...lead,
    { label: "Lowest", value: dash(lowest) }, { label: "Average", value: dash(mean) }, { label: "Highest", value: dash(highest) },
    ...(current ? [{ label: "Charge used", value: chargeText(shown ? charge : figures.charge_mah * 3.6) }] : []), ...tail];
  return (
    <Monitor
      readouts={<Readouts cells={cells} />}
      scope={none ? (
        <p role="status" className="flex h-full items-center justify-center rounded-md bg-slate-100 px-3 text-center text-[13px] text-slate-600 dark:bg-slate-950 dark:text-slate-400">{empty}</p>
      ) : (
        <div tabIndex={0} className="h-full rounded-md outline-none focus-visible:ring-2 focus-visible:ring-blue-500" aria-label="Scope trace, focus it to use the keys"
          onKeyDown={(e) => {
            const k = e.key;
            if (k === "ArrowLeft") pan(-0.25); else if (k === "ArrowRight") pan(0.25); else if (k === "+" || k === "=") zoom(2, 0.5); else if (k === "-" || k === "_") zoom(0.5, 0.5); else if (k === "0") setWin(null); else return;
            e.preventDefault();
          }}>
          <TraceChart fill buckets={buckets} view={view} unit={unit} log={log} tool="zoom" marks={marks} shades={shades} onZoom={(a, b) => set(clampWindow({ t0: a, t1: b }, bounds, floor))} onWheel={zoom}
            label={`Readings from ${timeText(view.t0)} to ${timeText(view.t1)}`} />
        </div>
      )}
      bar={none ? null : (
        <ScopeBar view={view} bounds={bounds} whole={isAll} tool="zoom" onTool={() => {}} canMark={false} log={log} onLog={setLog} onZoom={zoom} onPan={pan} onWhole={() => setWin(null)}
          atFloor={view.t1 - view.t0 <= floor + 1e-9} clock={(t) => timeText(t)} />
      )}
      notes={notes}
      side={side} />
  );
}

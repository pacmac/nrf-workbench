import { useEffect, useMemo, useRef, useState } from "react";
import { H, W, plot } from "./trace.js";
import { readout } from "./axes.js";
import { TINTS } from "./tints.js";

// The scope's trace (docs/specs/meter-page.md, "Live and replay"): a band from each point's lowest to its highest reading with the mean as a line, a labelled vertical axis, a labelled time
// axis, and a cursor: move over it (or touch it) and the time and the three values under the pointer are read out above the chart. It draws what it is given and reports what a person
// does to it: `onZoom(t0, t1)` for a drag across it (tool "zoom"), `onPick(t)` for a click (tool "mark": the first click is where an event starts, the second where it ends),
// `onWheel(factor, anchor)` for ctrl + wheel (a trackpad pinch is the same) with the pointer's place across the chart.
//
// `spans` are named stretches ({key, name, t0, t1, tint}); `marks` are single lines ({key, t, tone}); `pending` is the start line of an event whose end has not been clicked yet.

const PLOT_H = "h-64";

export function TraceChart({ buckets, view, unit, log, tool, spans = [], shades = [], marks = [], pending = null, onZoom, onPick, onWheel, label, readoutId }) {
  const p = useMemo(() => plot(buckets, view, { log, unit }), [buckets, view.t0, view.t1, log, unit]);          // eslint-disable-line react-hooks/exhaustive-deps
  const svg = useRef(null);
  const box = useRef(null);
  const [hover, setHover] = useState(null);                // {x, t}: the pointer in the chart's own units and its time
  const [drag, setDrag] = useState(null);                  // {a, b} while the pointer is down in the zoom tool
  const wheel = useRef(onWheel);
  wheel.current = onWheel;

  // A wheel that zooms must not also scroll the page, and a React wheel handler is passive: so it is attached by hand.
  useEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const on = (e) => {
      if (!(e.ctrlKey || e.metaKey) || !wheel.current) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const anchor = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
      wheel.current(e.deltaY < 0 ? 1.5 : 1 / 1.5, anchor);
    };
    el.addEventListener("wheel", on, { passive: false });
    return () => el.removeEventListener("wheel", on);
  }, []);

  if (!p) {
    return <p className={`flex ${PLOT_H} items-center justify-center rounded-md bg-slate-100 px-3 text-center text-[13px] text-slate-600 dark:bg-slate-950 dark:text-slate-400`}>Nothing to show here yet.</p>;
  }
  const unitX = (e) => {
    const r = svg.current.getBoundingClientRect();
    return ((e.clientX - r.left) / r.width) * W;
  };
  const move = (e) => {
    const x = unitX(e);
    setHover({ x: Math.min(Math.max(x, 0), W), t: p.timeAt(x) });
    if (drag) setDrag({ a: drag.a, b: x });
  };
  const down = (e) => {
    svg.current.setPointerCapture(e.pointerId);
    move(e);
    if (tool === "zoom") { const x = unitX(e); setDrag({ a: x, b: x }); }
  };
  const up = (e) => {
    const x = unitX(e);
    if (tool === "mark") { if (onPick) onPick(p.timeAt(x)); return; }
    if (!drag) return;
    const lo = Math.min(drag.a, drag.b);
    const hi = Math.max(drag.a, drag.b);
    setDrag(null);
    if (hi - lo >= 6 && onZoom) onZoom(p.timeAt(lo), p.timeAt(hi));            // under 6 units wide is a click, not a zoom
  };
  const r = hover ? readout(buckets, hover.t, unit, p.span) : null;
  const pct = (x) => `${(x / W) * 100}%`;
  const ypct = (y) => `${(y / H) * 100}%`;

  return (
    <figure className="space-y-1">
      <p id={readoutId} aria-label="Under the pointer" className="min-h-5 text-[13px] text-slate-700 dark:text-slate-300">
        {r ? (
          <>
            <span className="font-medium">{r.t}</span>
            <span className="text-slate-600 dark:text-slate-400"> · lowest </span><span className="font-medium">{r.lowest}</span>
            <span className="text-slate-600 dark:text-slate-400"> · average </span><span className="font-medium">{r.mean}</span>
            <span className="text-slate-600 dark:text-slate-400"> · highest </span><span className="font-medium">{r.highest}</span>
            <span className="text-slate-600 dark:text-slate-400"> · each point {r.width}</span>
          </>
        ) : <span className="text-slate-600 dark:text-slate-400">{tool === "mark" ? "Click the trace where the event starts, then where it ends." : "Move over the trace to read it. Drag across it to zoom in."}</span>}
      </p>
      <div className="flex gap-1">
        <div className={`relative ${PLOT_H} w-14 shrink-0 text-right text-[11px] text-slate-600 dark:text-slate-400`} aria-hidden="true" data-testid="y-axis">
          {p.yTicks.map((t) => <span key={t.value} className="absolute right-0 -translate-y-1/2 whitespace-nowrap leading-none" style={{ top: ypct(t.y) }}>{t.label}</span>)}
        </div>
        <div ref={box} className="relative min-w-0 flex-1">
          <svg ref={svg} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}
            className={`${PLOT_H} w-full touch-none rounded-md bg-slate-100 dark:bg-slate-950`} style={{ cursor: tool === "mark" ? "crosshair" : "col-resize" }}
            onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => setDrag(null)} onPointerLeave={() => { if (!drag) setHover(null); }}>
            {p.yTicks.map((t) => <line key={t.value} x1="0" x2={W} y1={t.y} y2={t.y} strokeWidth="1" vectorEffect="non-scaling-stroke" className="stroke-slate-300 dark:stroke-slate-800" />)}
            {p.xTicks.map((t) => <line key={t.t} x1={t.x} x2={t.x} y1="0" y2={H} strokeWidth="1" vectorEffect="non-scaling-stroke" className="stroke-slate-200 dark:stroke-slate-900" />)}
            {shades.filter((s) => s.t1 > view.t0 && s.t0 < view.t1).map((s) => (
              <rect key={s.key} x={p.at(s.t0)} y="0" width={Math.max(2, p.at(s.t1) - p.at(s.t0))} height={H} data-testid="wake-span" className="fill-amber-200/70 dark:fill-amber-500/25" />
            ))}
            {spans.filter((s) => s.t1 > view.t0 && s.t0 < view.t1).map((s) => {
              const a = p.at(s.t0);
              const b = p.at(s.t1);
              const tint = TINTS[(s.tint || 0) % TINTS.length];
              return (
                <g key={s.key}>
                  <rect x={a} y="0" width={Math.max(2, b - a)} height={H} data-testid="event-span" className={tint.rect}><title>{s.name}</title></rect>
                  {s.t0 >= view.t0 && <line x1={a} x2={a} y1="0" y2={H} strokeWidth="2" vectorEffect="non-scaling-stroke" data-testid="event-start" className={tint.line} />}
                  {s.t1 <= view.t1 && <line x1={b} x2={b} y1="0" y2={H} strokeWidth="2" vectorEffect="non-scaling-stroke" data-testid="event-end" className={tint.line} />}
                </g>
              );
            })}
            <polygon points={p.band} className="fill-blue-200 stroke-blue-300 dark:fill-blue-900 dark:stroke-blue-700" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <polyline points={p.mean} fill="none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" className="stroke-blue-700 dark:stroke-blue-300" />
            {marks.filter((m) => m.t >= view.t0 && m.t <= view.t1).map((m) => (
              <line key={m.key} x1={p.at(m.t)} x2={p.at(m.t)} y1="0" y2={H} strokeWidth="1" strokeDasharray="4 3" vectorEffect="non-scaling-stroke" data-testid="wake-mark" className="stroke-amber-600 dark:stroke-amber-400" />
            ))}
            {pending && pending.t >= view.t0 && pending.t <= view.t1 && (
              <line x1={p.at(pending.t)} x2={p.at(pending.t)} y1="0" y2={H} strokeWidth="2" vectorEffect="non-scaling-stroke" data-testid="pending-start" className="stroke-orange-600 dark:stroke-orange-400" />
            )}
            {drag && <rect x={Math.min(drag.a, drag.b)} y="0" width={Math.abs(drag.b - drag.a)} height={H} data-testid="zoom-selection" className="fill-amber-300/40 dark:fill-amber-400/20" />}
            {hover && <line x1={hover.x} x2={hover.x} y1="0" y2={H} strokeWidth="1" vectorEffect="non-scaling-stroke" data-testid="cursor" className="stroke-slate-700 dark:stroke-slate-300" />}
          </svg>
          <div className="pointer-events-none absolute inset-x-0 top-0 h-0" aria-hidden="true">
            {spans.filter((s) => s.t1 > view.t0 && s.t0 < view.t1).map((s) => (
              <span key={s.key} data-testid="event-name" className="absolute top-1 max-w-[40%] truncate rounded bg-white/80 px-1 text-[11px] font-medium text-slate-900 dark:bg-slate-900/80 dark:text-slate-100"
                style={{ left: pct(p.at(Math.max(s.t0, view.t0))) }}>{s.name}</span>
            ))}
          </div>
          <div className="relative h-5 text-xs text-slate-600 dark:text-slate-400" aria-hidden="true" data-testid="x-axis">
            {p.xTicks.map((t) => {
              const at = t.x / W;
              const shift = at < 0.06 ? "" : at > 0.94 ? "-translate-x-full" : "-translate-x-1/2";          // a label at either end stays inside the card
              return <span key={t.t} className={`absolute whitespace-nowrap ${shift}`} style={{ left: `${at * 100}%` }}>{t.label}</span>;
            })}
          </div>
        </div>
      </div>
      <figcaption className="text-xs text-slate-600 dark:text-slate-400">
        {p.log ? `Log scale, from ${p.bottomText} up.` : "The scale starts at " + p.bottomText + "."} The band runs from the lowest to the highest reading in each point, the line is the average.
      </figcaption>
    </figure>
  );
}

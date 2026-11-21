import { useMemo, useRef, useState } from "react";
import { plot } from "./trace.js";
import { TINTS } from "./tints.js";

// The whole recording in a strip under the scope, as a scope's navigator: the stretch shown is the lit box, the saved wakes are amber ticks and the named events are tinted. Press on it
// (or drag along it) to move the stretch shown there, keeping its length. `onMove(centre)` is the time under the pointer.

const OW = 960;
const OH = 56;

export function Overview({ buckets, bounds, view, unit, wakes = [], spans = [], onMove }) {
  const p = useMemo(() => plot(buckets, bounds, { unit, width: OW, height: OH }), [buckets, bounds.t0, bounds.t1, unit]);          // eslint-disable-line react-hooks/exhaustive-deps
  const svg = useRef(null);
  const [down, setDown] = useState(false);
  if (!p) return null;
  const at = (e) => {
    const r = svg.current.getBoundingClientRect();
    return p.timeAt(((e.clientX - r.left) / r.width) * OW);
  };
  const a = p.at(view.t0);
  const b = p.at(view.t1);
  return (
    <div className="space-y-1">
      <svg ref={svg} viewBox={`0 0 ${OW} ${OH}`} preserveAspectRatio="none" role="img" aria-label="Overview of the whole recording, the part on the scope lit. Press on it to move there."
        data-testid="overview" className="h-14 w-full touch-none rounded-md bg-slate-100 dark:bg-slate-950" style={{ cursor: "pointer" }}
        onPointerDown={(e) => { svg.current.setPointerCapture(e.pointerId); setDown(true); onMove(at(e)); }}
        onPointerMove={(e) => { if (down) onMove(at(e)); }} onPointerUp={() => setDown(false)} onPointerCancel={() => setDown(false)}>
        {spans.map((s) => (
          <rect key={s.key} x={p.at(s.t0)} y="0" width={Math.max(2, p.at(s.t1) - p.at(s.t0))} height={OH} className={TINTS[(s.tint || 0) % TINTS.length].rect} />
        ))}
        <polygon points={p.band} className="fill-blue-200 stroke-blue-300 dark:fill-blue-900 dark:stroke-blue-700" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        {wakes.map((w) => (
          <line key={w.n} x1={p.at(w.t0)} x2={p.at(w.t0)} y1="0" y2="10" strokeWidth="2" vectorEffect="non-scaling-stroke" data-testid="overview-wake" className="stroke-amber-600 dark:stroke-amber-400" />
        ))}
        <rect x={a} y="0" width={Math.max(3, b - a)} height={OH} strokeWidth="2" vectorEffect="non-scaling-stroke" data-testid="overview-window"
          className="fill-amber-300/30 stroke-amber-600 dark:fill-amber-400/20 dark:stroke-amber-400" />
      </svg>
    </div>
  );
}

import { Button } from "../ui.jsx";
import { durationText } from "./axes.js";

// The scope's controls (docs/specs/meter-page.md, "Live and replay"): the same for a live session and a replay, in ONE row so the scope keeps the height. Whole, back and forward through the
// recording, zoom out and in, the log axis, and for a replay the tool: Zoom (drag across the trace) or Mark an event (click where it starts, click where it ends). `view` is the stretch shown,
// `bounds` all there is; `after` is what the page puts at the end of the row (the replay's events). The keys with the trace focused are in the title of the row.
const SMALL = "min-h-8! px-3! py-1!";

export function ScopeBar({ view, bounds, whole, tool, onTool, canMark, log, onLog, onZoom, onPan, onWhole, atFloor, clock, after }) {
  return (
    <div role="toolbar" aria-label="Scope controls" title="With the trace focused: ← → move, + − zoom, 0 shows all of it. Ctrl and the mouse wheel zooms about the pointer." className="flex flex-wrap items-center gap-1.5">
      <Button tone="quiet" className={SMALL} onClick={onWhole} disabled={whole} aria-label="Show the whole recording">Whole</Button>
      <Button tone="quiet" className={SMALL} onClick={() => onPan(-0.5)} disabled={whole} aria-label="Back half a screen">‹ Back</Button>
      <Button tone="quiet" className={SMALL} onClick={() => onPan(0.5)} disabled={whole} aria-label="Forward half a screen">Forward ›</Button>
      <Button tone="quiet" className={SMALL} onClick={() => onZoom(0.5, 0.5)} disabled={whole} aria-label="Zoom out">−</Button>
      <Button tone="quiet" className={SMALL} onClick={() => onZoom(2, 0.5)} disabled={atFloor} aria-label="Zoom in">+</Button>
      {canMark && (
        <div role="group" aria-label="What a click does" className="flex gap-1.5">
          <Button tone={tool === "zoom" ? "primary" : "quiet"} className={SMALL} aria-pressed={tool === "zoom"} onClick={() => onTool("zoom")}>Zoom</Button>
          <Button tone={tool === "mark" ? "primary" : "quiet"} className={SMALL} aria-pressed={tool === "mark"} onClick={() => onTool("mark")}>Mark an event</Button>
        </div>
      )}
      <label className="flex items-center gap-1.5 text-[13px]">
        <input type="checkbox" checked={log} onChange={(e) => onLog(e.target.checked)} aria-label="Log current axis" />
        Log axis
      </label>
      <p className="ml-auto min-w-0 truncate text-[13px] text-slate-700 dark:text-slate-300" aria-label="Stretch shown">
        {clock(view.t0, bounds)} to {clock(view.t1, bounds)}, {durationText(view.t1 - view.t0)}{whole ? ", all of it" : ` of ${durationText(bounds.t1 - bounds.t0)}`}
      </p>
      {after}
    </div>
  );
}

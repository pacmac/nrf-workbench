import { useEffect } from "react";
import { Button, cx } from "../ui.jsx";

export { FitList } from "../ui.jsx";                 // moved to ui.jsx so the other full-height pages share it; the Meter page still imports it from here

// The pieces the Meter page is laid out from (docs/specs/meter-page.md, "Bench layout"): a body of two columns that fills the instrument, a row of readouts, and a drawer for what does
// not need to be on screen all the time. (The list that shows the rows that fit and pages is `FitList` in ui.jsx, shared with the other full-height pages.) Nothing here scrolls; the one exception is a drawer, which holds an editable table and may be
// longer than the window.

// The monitor's body: the scope and what goes with it on the left, the controls in a column on the right. A phone gets one column and the page flows (a narrow layout is its own piece of work).
export function Monitor({ readouts, scope, overview, bar, notes, side }) {
  return (
    <div className="grid min-h-0 flex-1 gap-3 p-3 md:grid-cols-[minmax(0,1fr)_19rem]">
      <div className="flex min-h-0 min-w-0 flex-col gap-2">
        {readouts}
        <div className="h-72 md:h-auto md:min-h-0 md:flex-1">{scope}</div>
        {overview}
        {bar}
        {notes}
      </div>
      <div className="flex min-h-0 min-w-0 flex-col gap-3">{side}</div>
    </div>
  );
}

// A row of figures read off the instrument: `cells` is [{label, value, aria?, onClick?}]. A cell with an `aria` is a live status; one with `onClick` opens something.
export function Readouts({ cells }) {
  return (
    <div role="group" aria-label="Readings" className="grid auto-cols-fr grid-flow-col divide-x divide-slate-200 overflow-hidden rounded-md border border-slate-200 bg-white dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-950">
      {cells.map((c) => {
        const inner = (
          <>
            <span className="block truncate text-xs text-slate-600 dark:text-slate-400">{c.label}</span>
            <span role={c.aria ? "status" : undefined} aria-label={c.aria} title={c.value} className="block truncate text-lg font-semibold leading-tight text-amber-800 dark:text-amber-400">{c.value}</span>
          </>
        );
        return c.onClick
          ? <button key={c.label} type="button" onClick={c.onClick} className="min-w-0 px-3 py-1.5 text-left hover:bg-slate-100 dark:hover:bg-slate-900" aria-label={`${c.label}: ${c.value}. Open`}>{inner}</button>
          : <div key={c.label} className="min-w-0 px-3 py-1.5">{inner}</div>;
      })}
    </div>
  );
}

// Something that sits over the instrument until it is closed, for what is wanted now and then (the trigger, the events, the stages): Escape closes it.
export function Drawer({ title, onClose, children, wide }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div role="dialog" aria-label={title}
      className={cx("absolute right-3 top-12 z-20 flex max-h-[calc(100%-4rem)] flex-col gap-2 overflow-auto rounded-lg border border-slate-300 bg-white p-3 shadow-xl dark:border-slate-700 dark:bg-slate-900", wide ? "w-[min(56rem,calc(100%-1.5rem))]" : "w-[min(36rem,calc(100%-1.5rem))]")}>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[15px] font-semibold">{title}</h3>
        <Button tone="quiet" onClick={onClose}>Close</Button>
      </div>
      {children}
    </div>
  );
}

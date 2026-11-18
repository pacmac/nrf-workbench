import { useState } from "react";
import { Card, ClassBadge, Chip, Details, Json, PageHeader, inputClass } from "./ui.jsx";
import { ROWS_AT_FIRST, ROWS_PER_STEP, seconds, when } from "./format.js";
import { Button } from "./ui.jsx";

const CLASSES = ["all", "refused", "failed", "suspect", "error", "usage", "ok"];

// Everything every caller has done, newest first. A refusal is as prominent as a success and never folded away:
// for an AI that flashes hardware, this is the page a human most needs.
export default function Activity({ activity }) {
  const [filter, setFilter] = useState("all");
  const [shown, setShown] = useState(ROWS_AT_FIRST);
  const rows = activity.rows.filter((r) => filter === "all" || r.payload.class === filter).slice().reverse();
  const visible = rows.slice(0, shown);
  return (
    <div className="space-y-4">
    <PageHeader title="Activity" topic="commands" />
    <Card
      aside={
        <label className="flex items-center gap-2 text-sm">
          Show
          <select className={inputClass + " w-auto"} value={filter} onChange={(e) => { setFilter(e.target.value); setShown(ROWS_AT_FIRST); }}>
            {CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      }
    >
      {activity.lost > 0 && (
        <p role="note" className="mb-3 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {activity.lost} earlier {activity.lost === 1 ? "entry is" : "entries are"} no longer retained.
        </p>
      )}
      {activity.reset && (
        <p role="note" className="mb-3 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          The service restarted, so its history started again from here.
        </p>
      )}
      {rows.length === 0 ? (
        <p className="text-sm text-slate-600 dark:text-slate-400">Nothing has been done through the service{filter !== "all" ? ` with class “${filter}”` : ""} yet.</p>
      ) : (
        <ol className="divide-y divide-slate-200 dark:divide-slate-800">
          {visible.map((e) => {
            const p = e.payload;
            return (
              <li key={e.cursor} className="space-y-1 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <ClassBadge cls={p.class} />
                  {p.code !== p.class && p.code !== "ok" && <Chip>{p.code}</Chip>}
                  <span className="font-mono text-sm font-medium">{p.command}</span>
                  <span className="ml-auto text-xs text-slate-600 dark:text-slate-400">{when(e.wall)}</span>
                </div>
                {p.message && <p className="text-sm">{p.message}</p>}
                <p className="text-xs text-slate-600 dark:text-slate-400">
                  by <strong>{p.caller}</strong> · took {seconds(p.duration_s)}
                </p>
                {Object.keys(p.args || {}).length > 0 && (
                  <Details summary="arguments"><Json value={p.args} /></Details>
                )}
              </li>
            );
          })}
        </ol>
      )}
      {rows.length > shown && (
        <div className="mt-3">
          <Button tone="quiet" onClick={() => setShown(shown + ROWS_PER_STEP)}>Show {Math.min(ROWS_PER_STEP, rows.length - shown)} more</Button>
        </div>
      )}
    </Card>
    </div>
  );
}

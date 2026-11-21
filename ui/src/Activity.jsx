import { useState } from "react";
import { Card, ClassBadge, Chip, FitList, PageHeader, inputClass, useWide } from "./ui.jsx";
import { ROWS_AT_FIRST, ROWS_PER_STEP, seconds, when } from "./format.js";
import { Button, cx } from "./ui.jsx";

const CLASSES = ["all", "refused", "failed", "suspect", "error", "usage", "ok"];
const ROW_H = 64;               // two lines: 24 px for the badges and the command, 20 for the message, and the room between them

// The arguments in one short line: `key=value` pairs. The whole of them are in the row's tooltip.
function argsLine(args) {
  return Object.entries(args || {}).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(" ");
}

// One entry, two lines. `wrap` lets a long message take more lines (a phone's flowing list); a full-height row cuts it short, since its height is fixed.
function Entry({ e, wrap }) {
  const p = e.payload;
  const line = wrap ? "break-all" : "truncate";
  const row = cx("flex items-center gap-x-2", wrap && "flex-wrap gap-y-1");      // a phone's line may take a second line; a full-height row's may not
  return (
    <div className="min-w-0 flex-1 leading-tight">
      <div className={row}>
        <ClassBadge cls={p.class} />
        {p.code !== p.class && p.code !== "ok" && <Chip>{p.code}</Chip>}
        <span data-command className="shrink-0 font-mono text-sm font-medium">{p.command}</span>
        <span title={JSON.stringify(p.args || {})} className={cx("min-w-0 flex-1 font-mono text-xs text-slate-600 dark:text-slate-400", line)}>{argsLine(p.args)}</span>
        <span className="shrink-0 text-xs text-slate-600 dark:text-slate-400">{when(e.wall)}</span>
      </div>
      <div className={cx("mt-1 text-sm", row)}>
        <span className={cx("min-w-0 flex-1", wrap ? "break-words" : "truncate")}>{p.message}</span>
        <span className="shrink-0 text-xs text-slate-600 dark:text-slate-400">by <strong>{p.caller}</strong> · took {seconds(p.duration_s)}</span>
      </div>
    </div>
  );
}

// Everything every caller has done, newest first. A refusal is as prominent as a success and never folded away:
// for an AI that flashes hardware, this is the page a human most needs. On a wide window the page owns the height and shows the entries that
// fit, with a pager (docs/specs/navigation.md, "Full-height pages"); on a phone it flows, with Show more.
export default function Activity({ activity }) {
  const wide = useWide();
  const [filter, setFilter] = useState("all");
  const [shown, setShown] = useState(ROWS_AT_FIRST);
  const rows = activity.rows.filter((r) => filter === "all" || r.payload.class === filter).slice().reverse();
  const visible = rows.slice(0, shown);
  const none = <p className="text-sm text-slate-600 dark:text-slate-400">Nothing has been done through the service{filter !== "all" ? ` with class “${filter}”` : ""} yet.</p>;
  return (
    <div className={wide ? "flex min-h-0 flex-1 flex-col gap-2" : "space-y-4"}>
    <PageHeader title="Activity" topic="commands" />
    <Card
      className={wide ? "flex min-h-0 flex-1 flex-col" : undefined}
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
        <p role="note" className="mb-3 shrink-0 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          {activity.lost} earlier {activity.lost === 1 ? "entry is" : "entries are"} no longer retained.
        </p>
      )}
      {activity.reset && (
        <p role="note" className="mb-3 shrink-0 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          The service restarted, so its history started again from here.
        </p>
      )}
      {wide ? (
        <FitList label="Entries" rowH={ROW_H} items={rows} keyOf={(e) => e.cursor} render={(e) => <Entry e={e} />} empty={none} />
      ) : rows.length === 0 ? none : (
        <ol className="divide-y divide-slate-200 dark:divide-slate-800">
          {visible.map((e) => <li key={e.cursor} className="flex py-3"><Entry e={e} wrap /></li>)}
        </ol>
      )}
      {!wide && rows.length > shown && (
        <div className="mt-3">
          <Button tone="quiet" onClick={() => setShown(shown + ROWS_PER_STEP)}>Show {Math.min(ROWS_PER_STEP, rows.length - shown)} more</Button>
        </div>
      )}
    </Card>
    </div>
  );
}

import { useState } from "react";
import { Button, Chip } from "../ui.jsx";
import { describe } from "./model.js";

// One connection, drawn as the wire it is: where it leaves the Pi, what it carries, where it lands on the device, then
// one plain sentence and what it is doing now (docs/specs/wiring.md, "The UI"). The sentence is the same one the add
// sheet ends with, so what you build is what you see.

function End({ title, detail, align }) {
  return (
    <div className={`min-w-0 sm:w-44 ${align === "right" ? "sm:text-right" : ""}`}>
      <p className="truncate text-sm font-medium">{title}</p>
      {detail && <p className="truncate font-mono text-xs text-slate-600 dark:text-slate-400">{detail}</p>}
    </div>
  );
}

export default function WireLine({ conn, ctx, hue, focused, onFocus, onEdit, onRemove, onOpenConsole }) {
  const [asking, setAsking] = useState(false);
  const d = describe(conn, ctx);
  const device = ctx.devices.find((x) => x.serial === conn.device);
  return (
    <li
      onMouseEnter={() => onFocus(conn.id)}
      onMouseLeave={() => onFocus(null)}
      className={`flex gap-3 rounded-xl border bg-white p-3 dark:bg-slate-900 ${focused ? "border-blue-500 dark:border-blue-400" : "border-slate-200 dark:border-slate-800"}`}
    >
      <span aria-hidden="true" className={`w-1.5 shrink-0 rounded-full ${hue.dot}`} />
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-col items-stretch gap-1 sm:flex-row sm:items-center sm:gap-3">
          <End title={d.pi.title} detail={d.pi.detail} />
          {/* The wire. Stacked (phone): a visible bar with its signal beside it. Side by side: a line with the signal above. */}
          <div className="flex items-center gap-2 pl-1 sm:min-w-24 sm:flex-1 sm:flex-col sm:gap-0 sm:pl-0" aria-hidden="true">
            <span className={`h-7 w-1 rounded-full sm:order-2 sm:h-0.5 sm:w-full ${hue.dot}`} />
            <span className="truncate text-xs text-slate-600 sm:order-1 dark:text-slate-400">{d.signal}</span>
          </div>
          <End title={d.device.title} detail={d.device.detail} align="right" />
        </div>
        <p className="text-sm">{d.summary}</p>
        <div className="flex flex-wrap items-center gap-2">
          {d.chips.map((c) => <Chip key={c.text} tone={c.tone}>{c.text}</Chip>)}
          <span className="flex-1" />
          {conn.spec.intent === "log" && <Button tone="quiet" onClick={onOpenConsole}>Open console</Button>}
          {asking ? (
            <span className="flex items-center gap-2" role="group" aria-label={`Remove this connection from ${device ? device.model : "the device"}?`}>
              <span className="text-sm">Remove?</span>
              <Button tone="danger" onClick={() => { setAsking(false); onRemove(); }}>Yes</Button>
              <Button tone="quiet" onClick={() => setAsking(false)}>No</Button>
            </span>
          ) : (
            <>
              <Button tone="quiet" onClick={onEdit}>Edit</Button>
              <Button tone="quiet" onClick={() => setAsking(true)}>Remove</Button>
            </>
          )}
        </div>
      </div>
    </li>
  );
}

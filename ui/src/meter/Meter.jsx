import { useState } from "react";
import { readJson } from "../api.js";
import { Button, Card, Field, Json, inputClass } from "../ui.jsx";
import { ResultView } from "../Commands.jsx";
import { headline, series } from "./format.js";

// The multimeter on the bench's LAN (docs/specs/meter.md). Three buttons ask it a question and show the answer in words, then the whole result.
// Nothing here can change the meter: the function is where the leads are plugged, so it is set at the meter's front panel.

const find = (commands, name) => (commands || []).find((c) => c.name === name) || null;

function Chart({ points, unit }) {
  const s = series(points, 480, 96, unit);
  if (!s) return null;
  return (
    <figure className="space-y-1">
      <svg viewBox="0 0 480 96" preserveAspectRatio="none" role="img" aria-label={`${points.length} readings, from ${s.lo} to ${s.hi} ${unit}`} className="h-24 w-full rounded-md bg-slate-100 dark:bg-slate-950">
        <polyline points={s.line} fill="none" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" className="stroke-blue-700 dark:stroke-blue-300" />
      </svg>
      <figcaption className="flex justify-between text-xs text-slate-600 dark:text-slate-400"><span>bottom of the chart: {s.axisLo}</span><span>top: {s.axisHi}</span></figcaption>
    </figure>
  );
}

export default function Meter({ commands, onDone }) {
  const [count, setCount] = useState("20");
  const [outcome, setOutcome] = useState(null);
  const [shown, setShown] = useState("");
  const show = find(commands, "meter.show");
  const read = find(commands, "meter.read");
  const sample = find(commands, "meter.sample");
  const pending = Boolean(outcome && outcome.pending);
  const n = Number(count);
  const countOk = Number.isInteger(n) && n >= 1 && n <= 1000;

  async function ask(command, query) {
    setShown(command.name);
    setOutcome({ pending: true });
    try {
      const r = await readJson(command.path, query);
      setOutcome(r);
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  if (!show) return null;
  // A headline needs a reading to talk about: a failure carries none, and is shown as the whole result instead.
  const body = outcome && !outcome.pending ? outcome.body : null;
  const r = body && body.data && Object.keys(body.data).length > 0 ? body : null;
  return (
    <Card title="Multimeter" aside={<span className="text-xs text-slate-600 dark:text-slate-400">asks only: it never changes the meter's setting</span>}>
      <div className="space-y-3">
        <p className="text-[13px] text-slate-600 dark:text-slate-400">
          The function and range are set at the meter's front panel, because the leads in its current jacks are in series with the board.
        </p>
        <div className="flex flex-wrap items-end gap-2">
          <Button disabled={pending} onClick={() => ask(show)}>Check the meter</Button>
          {read && <Button tone="quiet" disabled={pending} onClick={() => ask(read)}>Read once</Button>}
          {sample && (
            <>
              <div className="w-24"><Field label="Readings"><input className={inputClass} inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value)} aria-label="How many readings to take" /></Field></div>
              <Button tone="quiet" disabled={pending || !countOk} onClick={() => ask(sample, { count: String(n) })}>Take {countOk ? n : "…"}</Button>
            </>
          )}
        </div>
        {outcome && outcome.pending && <ResultView outcome={outcome} pendingText="Asking the meter…" />}
        {r && (
          <div className="space-y-2">
            <p role="status" className="text-[17px] font-semibold">{headline(shown, r.data)}</p>
            {r.data.in_series && <p className="text-[13px] text-amber-800 dark:text-amber-300">Set to current: this is in series with the circuit if the leads are in the current jacks.</p>}
            {shown === "meter.sample" && r.data.points && <Chart points={r.data.points} unit={r.data.unit} />}
            {r.class !== "ok" && <ResultView outcome={outcome} />}
            {r.class === "ok" && (
              <details>
                <summary className="cursor-pointer text-xs text-blue-700 underline dark:text-blue-300">The whole result</summary>
                <div className="mt-2"><Json value={r} /></div>
              </details>
            )}
          </div>
        )}
        {outcome && !outcome.pending && !r && <ResultView outcome={outcome} />}
      </div>
    </Card>
  );
}

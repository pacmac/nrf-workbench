import { useState } from "react";
import { Button, cx, inputClass } from "../ui.jsx";
import { ResultView } from "../Commands.jsx";

// The Capture card (docs/specs/power-workbench.md, "Capture: stop on the clock, or on the board"): when a capture stops is chosen, not picked from a fixed list. Stop after is Time (a length typed
// in seconds, minutes or hours), Never (until Stop, or the 12 hour limit every capture has), and Wakes (N whole wakes of the board) when the service can do it. This card builds the call; the
// page runs it. A capture ends at the first of its condition and Stop.

export const MIN_BOUND_S = 1;
export const MAX_BOUND_S = 43200;                                  // core.md: nothing runs unbounded; 12 h is about 13 MB of CSV
const UNITS = [["s", "seconds", 1], ["m", "minutes", 60], ["h", "hours", 3600]];
const SEG = "min-h-8! px-3! py-1!";

// Whether a command takes an argument: the card offers what the service can do, so a service without stop_after_wakes shows no Wakes choice.
export const takes = (command, name) => Boolean(command && (command.args || []).some((a) => a.name === name));
const required = (command, name) => Boolean(command && (command.args || []).some((a) => a.name === name && a.required));

export function CaptureCard({ watch, record, stop, running, pending, sessionText, outcome, blocked, extra, onRun }) {
  const wakes = takes(record, "stop_after_wakes");
  const [how, setHow] = useState("time");
  const [len, setLen] = useState("10");
  const [unit, setUnit] = useState("m");
  const [count, setCount] = useState("1");
  const [name, setName] = useState("");
  const factor = UNITS.find((u) => u[0] === unit)[2];
  const seconds = Number(len) * factor;
  const timeOk = len.trim() !== "" && Number.isFinite(seconds) && seconds >= MIN_BOUND_S && seconds <= MAX_BOUND_S;
  const n = Number(count);
  const wakesOk = Number.isInteger(n) && n >= 1 && n <= MAX_BOUND_S / 0.2;          // a wake is at least one 0.2 s block, so 12 h holds at most 216,000
  const ready = how === "time" ? timeOk : how === "wakes" ? wakesOk : true;
  const argsFor = (command) => {
    if (how === "wakes") return { stop_after_wakes: n, ...(required(command, "bound_s") ? { bound_s: MAX_BOUND_S } : {}) };
    return { bound_s: how === "never" ? MAX_BOUND_S : Math.round(seconds * 1000) / 1000 };
  };
  const named = (command) => (command === record && name.trim() ? { name: name.trim() } : {});
  const start = (command) => onRun(command, { ...argsFor(command), ...named(command) });

  return (
    <section aria-label="Capture" className="space-y-3 rounded-md border border-slate-200 p-3 dark:border-slate-800">
      <h3 className="text-[15px] font-semibold">Capture</h3>
      <div>
        <span id="stop-after" className="mb-1 block text-xs text-slate-600 dark:text-slate-400">Stop after</span>
        <div role="group" aria-labelledby="stop-after" className="grid auto-cols-fr grid-flow-col gap-1">
          {[...(wakes ? [["wakes", "Wakes"]] : []), ["time", "Time"], ["never", "Never"]].map(([key, label]) => (
            <Button key={key} tone={how === key ? "primary" : "quiet"} className={SEG} aria-pressed={how === key} disabled={running} onClick={() => setHow(key)}>{label}</Button>
          ))}
        </div>
      </div>
      {how === "time" && (
        <div>
          <div className="flex gap-1">
            <input className={cx(inputClass, "w-24! text-right")} inputMode="decimal" value={len} onChange={(e) => setLen(e.target.value)} disabled={running} aria-label="Length" aria-invalid={!timeOk} />
            <select className={cx(inputClass, "w-auto! min-w-0 flex-1")} value={unit} onChange={(e) => setUnit(e.target.value)} disabled={running} aria-label="Unit">
              {UNITS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </div>
          {!timeOk && <p role="note" className="mt-1 text-xs text-amber-800 dark:text-amber-300">From 1 second to 12 hours.</p>}
        </div>
      )}
      {how === "wakes" && (
        <div>
          <div className="flex items-center gap-2">
            <input className={cx(inputClass, "w-24! text-right")} inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value)} disabled={running} aria-label="Number of wakes" aria-invalid={!wakesOk} />
            <span className="text-[13px] text-slate-600 dark:text-slate-400">{n === 1 ? "wake" : "wakes"}</span>
          </div>
          <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">Starts recording, waits for the board to wake and go back to sleep, then stops. A wake already under way is not counted. Gives up after 12 hours if no wake comes.</p>
        </div>
      )}
      {how === "never" && <p className="text-xs text-slate-600 dark:text-slate-400">Keeps going until you press Stop, or for 12 hours.</p>}
      {record && (
        <label className="block">
          <span className="mb-1 block text-xs text-slate-600 dark:text-slate-400">Recording name</span>
          <input className={inputClass} value={name} placeholder="the start time" disabled={running} onChange={(e) => setName(e.target.value.toLowerCase())}
            aria-label="Name for the recording" autoComplete="off" spellCheck="false" />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {stop && running && <Button tone="danger" disabled={pending} onClick={() => onRun(stop, {})}>Stop</Button>}
        {!running && watch && <Button tone="quiet" disabled={pending || blocked || !ready} onClick={() => start(watch)}>Watch</Button>}
        {!running && record && <Button disabled={pending || blocked || !ready} onClick={() => start(record)}>Record</Button>}
        {extra}
      </div>
      <p className="text-[13px] text-slate-700 dark:text-slate-300" aria-label="Session">{sessionText}</p>
      {outcome && !outcome.pending && <ResultView outcome={outcome} />}
    </section>
  );
}

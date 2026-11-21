import { useState } from "react";
import { Button, Field, inputClass } from "../ui.jsx";
import { durationText } from "./axes.js";
import { chargeText, energyText, si } from "./format.js";
import { TINTS } from "./tints.js";

// The events of a recording (docs/specs/meter-page.md, "Events"): named stretches a person marks on the scope with two clicks. A new one waits here for its name; each saved one shows what it
// measured. Nothing about a board has to be known before the capture: this is read off the trace afterwards.

export const timeText = (t) => {
  const s = Math.max(0, t);
  if (s < 60) return `${Number(s.toFixed(4))} s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s - h * 3600 - m * 60;
  return `${h > 0 ? `${h}:${String(m).padStart(2, "0")}` : m}:${sec.toFixed(3).padStart(6, "0")}`;
};

export function EventPanel({ events, draft, armed, busy, problem, unit, volts, onSave, onCancel, onDelete, onShow, onArm }) {
  const [typed, setTyped] = useState({ draft: null, text: "" });         // what was typed for this draft; until then the name is the default, there from the first render
  const [renaming, setRenaming] = useState(null);
  const [newName, setNewName] = useState("");
  const taken = (n, except) => events.some((e) => e.name.toLowerCase() === n.trim().toLowerCase() && e.name !== except);
  const defaultName = () => {
    let k = events.length + 1;
    while (taken(`event ${k}`)) k += 1;
    return `event ${k}`;
  };
  const name = draft ? (typed.draft === draft ? typed.text : defaultName()) : "";
  const setName = (text) => setTyped({ draft, text });
  const nameOk = (n, except) => n.trim().length >= 1 && n.trim().length <= 40 && !taken(n, except);

  return (
    <div className="space-y-3">
      {draft && (
        <form className="flex flex-wrap items-end gap-3 rounded-md border border-orange-300 p-3 dark:border-orange-800" aria-label="New event"
          onSubmit={(e) => { e.preventDefault(); if (nameOk(name)) onSave(name.trim(), draft.start, draft.end, null); }}>
          <p className="pb-2 text-[13px]">New event from {timeText(draft.start)} to {timeText(draft.end)}, {durationText(draft.end - draft.start)}.</p>
          <div className="w-56 max-w-full"><Field label="Event name"><input className={inputClass} value={name} maxLength={40} onChange={(e) => setName(e.target.value)} onFocus={(e) => e.target.select()} aria-label="Event name" autoComplete="off" autoFocus /></Field></div>
          <Button type="submit" disabled={busy || !nameOk(name)}>Save the event</Button>
          <Button type="button" tone="quiet" onClick={onCancel}>Cancel</Button>
          {name.trim() && taken(name) && <p role="note" className="w-full text-[13px] text-amber-800 dark:text-amber-300">There is already an event with that name.</p>}
        </form>
      )}
      {armed && <p role="status" className="rounded-md bg-orange-100 px-3 py-2 text-[13px] text-orange-950 dark:bg-orange-950 dark:text-orange-100">Click the trace where {armed.event.name} should {armed.edge === "start" ? "start" : "end"}. <button type="button" className="underline" onClick={onCancel}>Cancel</button></p>}
      {problem && <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{problem}</p>}
      {events.length === 0 && !draft && <p className="text-[13px] text-slate-600 dark:text-slate-400">No events yet. Choose Mark an event, click the trace where one starts and again where it ends, then name it. Every event shows its length, average current, peak and charge.</p>}
      {events.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[34rem] text-left" aria-label="Events">
            <thead>
              <tr className="border-b border-slate-200 text-xs text-slate-600 dark:border-slate-800 dark:text-slate-400">
                <th scope="col" className="py-1 pr-3 font-normal">Event</th>
                <th scope="col" className="py-1 pr-3 font-normal">Length</th>
                <th scope="col" className="py-1 pr-3 font-normal">Average current</th>
                <th scope="col" className="py-1 pr-3 font-normal">Peak</th>
                <th scope="col" className="py-1 font-normal">{volts ? "Charge and energy" : "Charge"}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200 dark:divide-slate-800">
              {events.map((e, i) => (
                <tr key={e.name}>
                  <th scope="row" className="py-2 pr-3 align-top font-normal">
                    {renaming === e.name ? (
                      <form className="flex flex-wrap items-center gap-2" onSubmit={(ev) => { ev.preventDefault(); if (nameOk(newName, e.name)) { onSave(newName.trim(), e.start_s, e.end_s, e.name); setRenaming(null); } }}>
                        <input className={`${inputClass} w-40`} value={newName} maxLength={40} onChange={(ev) => setNewName(ev.target.value)} aria-label={`New name for ${e.name}`} autoComplete="off" />
                        <Button type="submit" disabled={busy || !nameOk(newName, e.name)}>Save</Button>
                        <Button type="button" tone="quiet" onClick={() => setRenaming(null)}>Cancel</Button>
                      </form>
                    ) : <span className={`rounded px-2 py-0.5 text-[13px] font-semibold ${TINTS[i % TINTS.length].chip}`}>{e.name}</span>}
                    <div className="mt-1 text-xs text-slate-600 dark:text-slate-400">{timeText(e.start_s)} to {timeText(e.end_s)}</div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      <Button tone="quiet" onClick={() => onShow(e)} aria-label={`Show ${e.name}`}>Show</Button>
                      <Button tone="quiet" onClick={() => { setRenaming(e.name); setNewName(e.name); }} aria-label={`Rename ${e.name}`}>Rename</Button>
                      <Button tone="quiet" onClick={() => onArm(e, "start")} aria-label={`Move the start of ${e.name}`}>Move start</Button>
                      <Button tone="quiet" onClick={() => onArm(e, "end")} aria-label={`Move the end of ${e.name}`}>Move end</Button>
                      <Button tone="quiet" disabled={busy} onClick={() => onDelete(e.name)} aria-label={`Delete the event ${e.name}`}>Delete</Button>
                    </div>
                  </th>
                  <td className="py-2 pr-3 align-top text-[15px] font-medium">{durationText(e.duration_s)}</td>
                  <td className="py-2 pr-3 align-top text-[15px] font-medium">
                    {e.mean === null ? "—" : si(e.mean, unit)}
                    {e.mean !== null && <div className="text-xs font-normal text-slate-600 dark:text-slate-400">lowest {si(e.lowest, unit)}</div>}
                  </td>
                  <td className="py-2 pr-3 align-top text-[15px] font-medium">{e.peak === null ? "—" : si(e.peak, unit)}</td>
                  <td className="py-2 align-top text-[15px] font-medium">
                    {e.charge_c === null ? "—" : chargeText(e.charge_c)}
                    {e.energy_j !== null && e.charge_c !== null && <div className="text-xs font-normal text-slate-600 dark:text-slate-400">{energyText(e.charge_c, volts)}</div>}
                    {e.source && <div className="text-xs font-normal text-slate-600 dark:text-slate-400">{e.source === "wake" ? "from the samples" : "from the readings"}{e.covered_s !== null && e.covered_s < e.duration_s - 1e-6 ? ", part of it is past the recording" : ""}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

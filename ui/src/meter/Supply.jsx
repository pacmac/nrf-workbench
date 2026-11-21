import { useState } from "react";
import { writeJson } from "../api.js";
import { Button, Lamp, cx, inputClass } from "../ui.jsx";
import { ResultView } from "../Commands.jsx";
import { unitSentence } from "./format.js";

// The Supply instrument of the Meter page (docs/specs/ppk2.md, "Source mode and the supply switch are gated"; docs/specs/meter-page.md, "Bench layout"): always on screen above the current
// monitor. It says what the unit is doing (supplying the board, or only monitoring a board that has its own supply), the voltage, and carries the two writes. The unit does not report
// whether its output is on, so a change that can apply or cut a board's supply is asked for twice: the button, then a sentence saying what it will do. The server refuses it without
// the second answer (supply_unconfirmed). While a session holds the unit nothing here can be changed, and the strip says so.

const find = (commands, name) => (commands || []).find((c) => c.name === name) || null;
const SEG = "min-h-8! px-3! py-1!";

export function Supply({ commands, shown, running, session, reload, onDone }) {
  const modeCmd = find(commands, "ppk2.mode");
  const supplyCmd = find(commands, "ppk2.supply");
  const [mv, setMv] = useState("");
  const [ask, setAsk] = useState(null);                              // the change waiting for its second answer: {kind: "on"} or {kind: "mode", mode}
  const [outcome, setOutcome] = useState(null);
  const d = shown && shown.body && shown.body.data && Object.keys(shown.body.data).length ? shown.body.data : null;
  const held = Boolean(running && session && session.source === "ppk2");
  // What the unit is: from the running session's facts while one holds it (the unit cannot be asked then), else what it last said.
  const u = held && session.unit_mode ? { mode: session.unit_mode, vdd_mv: session.vdd_mv, supply: session.supply, vdd_from: session.vdd_from } : d ? { mode: d.mode, vdd_mv: d.vdd_mv, supply: d.supply, vdd_from: d.vdd_from } : null;
  const pending = Boolean(outcome && outcome.pending);
  const volts = mv !== "" ? mv : u && u.vdd_mv ? String(u.vdd_mv) : "";
  const voltsNumber = Number(volts);
  const voltsOk = Number.isInteger(voltsNumber) && voltsNumber >= 800 && voltsNumber <= 5000;
  const source = u && u.mode === "source";
  const monitoring = u && u.mode === "ampere";
  const locked = held || pending;
  const lamp = !u ? "absent" : monitoring ? "listening" : u.supply === "on" ? "connected" : u.supply === "off" ? "absent" : "attention";
  const state = !u ? "unit not read" : monitoring ? "Monitoring" : u.supply === "on" ? "Output on" : u.supply === "off" ? "Output off" : "Output state unknown";

  async function act(command, args) {
    setOutcome({ pending: true });
    setAsk(null);
    try {
      const r = await writeJson(command.path, args);
      setOutcome(r.ok ? null : r);
      if (onDone) onDone(r);
      reload();
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }
  const step = (delta) => setMv(String(Math.min(5000, Math.max(800, (voltsOk ? voltsNumber : 4000) + delta))));

  return (
    <section aria-label="Supply" className="rounded-lg border border-slate-200 bg-white px-4 py-2 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="h-5 w-2 rounded-sm bg-sky-600 dark:bg-sky-400" />
          <h2 className="text-[15px] font-semibold">Supply</h2>
          <Lamp state={lamp} size={14} />
          <span className="text-[13px] text-slate-600 dark:text-slate-400">{state}</span>
        </div>
        <p aria-label="Source voltage" className={cx("text-3xl font-semibold leading-none text-sky-700 dark:text-sky-300", (monitoring || !u || u.supply !== "on") && "opacity-60")}>
          {monitoring || !u || !u.vdd_mv ? "—" : (u.vdd_mv / 1000).toFixed(3)}<span className="ml-1 text-base font-medium text-slate-600 dark:text-slate-400">V</span>
        </p>
        {u && supplyCmd && source && (
          <div className="flex items-center gap-1">
            <Button tone="quiet" className="min-h-8! px-3! py-1!" disabled={locked} onClick={() => step(-100)} aria-label="Lower by 100 millivolts">−</Button>
            <input className={cx(inputClass, "w-20! px-2! py-1! text-right")} inputMode="numeric" value={volts} onChange={(e) => setMv(e.target.value)} disabled={locked} aria-label="Source voltage in millivolts" />
            <span className="text-[13px] text-slate-600 dark:text-slate-400">mV</span>
            <Button tone="quiet" className="min-h-8! px-3! py-1!" disabled={locked} onClick={() => step(100)} aria-label="Raise by 100 millivolts">+</Button>
          </div>
        )}
        {u && supplyCmd && source && (
          <div role="group" aria-label="Output" className="flex gap-1">
            <Button tone={u.supply === "off" ? "primary" : "quiet"} className={SEG} aria-pressed={u.supply === "off"} disabled={locked} aria-label="Switch off"
              onClick={() => act(supplyCmd, { on: false, ...(voltsOk ? { mv: voltsNumber } : {}) })}>Off</Button>
            <Button tone={u.supply === "on" ? "primary" : "quiet"} className={SEG} aria-pressed={u.supply === "on"} disabled={locked || !voltsOk} aria-label="Switch on"
              onClick={() => setAsk({ kind: "on" })}>On</Button>
          </div>
        )}
        {u && modeCmd && (
          <div role="group" aria-label="What the unit does" className="flex gap-1">
            {[["source", "Source", "Source mode"], ["ampere", "Ampere", "Ampere mode"]].map(([m, label, full]) => (
              <Button key={m} tone={u.mode === m ? "primary" : "quiet"} className={SEG} aria-pressed={u.mode === m} disabled={locked} aria-label={full}
                title={`${full}: ${m === "source" ? "the unit supplies the board" : "the board has its own supply and the unit only monitors it"}`}
                onClick={() => { if (u.mode === m) return; if (m === "ampere" && u.supply !== "off") setAsk({ kind: "mode", mode: m }); else act(modeCmd, { mode: m }); }}>{label}</Button>
            ))}
          </div>
        )}
        <div className="min-w-32 flex-1 text-[13px]" role="status">
          {ask ? (
            <span role="alertdialog" aria-label="Confirm the supply change" className="flex flex-wrap items-center gap-2 rounded-md border border-orange-300 bg-orange-50 px-2 py-1 dark:border-orange-800 dark:bg-orange-950">
              <span>{ask.kind === "on" ? `Apply ${(voltsNumber / 1000).toFixed(3)} V to the board?` : "Leaving source mode can cut the board's supply. Continue?"}</span>
              <Button className="min-h-7! px-3! py-0.5!" onClick={() => (ask.kind === "on" ? act(supplyCmd, { on: true, mv: voltsNumber, accept_supply: true }) : act(modeCmd, { mode: ask.mode, accept_supply: true }))}>
                {ask.kind === "on" ? "Switch on" : "Leave source mode"}
              </Button>
              <Button tone="quiet" className="min-h-7! px-3! py-0.5!" onClick={() => setAsk(null)}>Cancel</Button>
            </span>
          ) : held ? (
            <span className="block truncate text-amber-800 dark:text-amber-300" title={`Held by the running capture, so it cannot be asked or changed until it ends. ${u ? `${unitSentence(u)}.` : ""}`}>
              Held by the running capture. {u ? `${unitSentence(u)}.` : ""}
            </span>
          ) : u ? (
            <span className="block truncate text-slate-600 dark:text-slate-400" aria-label="Profiler" title={d && d.serial ? `Power profiler ${d.serial}: ${unitSentence(u)}` : unitSentence(u)}>
              {d && d.serial ? `Power profiler ${d.serial}: ` : ""}{unitSentence(u)}
            </span>
          ) : shown ? <ResultView outcome={shown} /> : <span className="text-slate-600 dark:text-slate-400">Asking the profiler…</span>}
          {outcome && !outcome.pending && <ResultView outcome={outcome} />}
        </div>
      </div>
    </section>
  );
}

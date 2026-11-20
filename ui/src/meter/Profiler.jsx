import { useState } from "react";
import { readJson, writeJson } from "../api.js";
import { Button, Card, Field, cx, inputClass } from "../ui.jsx";
import { ResultView } from "../Commands.jsx";
import { headline } from "./format.js";

// The Nordic Power Profiler on the bench's USB (docs/specs/ppk2.md). Two pieces: a quick check for Devices and Home, and the card on the Meter page that shows the unit
// and carries the two writes. In SOURCE mode the unit powers whatever is wired to its output, and it does not say whether its output is on, so a change that can cut or
// apply a board's supply is made only with the tick that says so: the server refuses it otherwise (supply_unconfirmed) and says why.

const find = (commands, name) => (commands || []).find((c) => c.name === name) || null;

// Devices and Home: one button that asks the unit what it is set to.
export function ProfilerCheck({ commands, onDone }) {
  const [outcome, setOutcome] = useState(null);
  const show = find(commands, "ppk2.show");
  if (!show) return null;
  const pending = Boolean(outcome && outcome.pending);
  const body = outcome && !outcome.pending ? outcome.body : null;
  const r = body && body.data && Object.keys(body.data).length > 0 ? body : null;
  async function ask() {
    setOutcome({ pending: true });
    try {
      const res = await readJson(show.path);
      setOutcome(res);
      if (onDone) onDone(res);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }
  return (
    <Card title="Power profiler" aside={<span className="text-xs text-slate-600 dark:text-slate-400">measuring changes nothing</span>}>
      <div className="space-y-3">
        <p className="text-[13px] text-slate-600 dark:text-slate-400">
          The Nordic Power Profiler reads a board's current at 100,000 samples a second. Watch or record it on the Meter page.
        </p>
        <Button disabled={pending} onClick={ask}>Check the profiler</Button>
        {outcome && outcome.pending && <ResultView outcome={outcome} pendingText="Asking the profiler…" />}
        {r && r.class === "ok" && <p role="status" className="break-words text-[17px] font-semibold">{headline("ppk2.show", r.data)}</p>}
        {outcome && !outcome.pending && !(r && r.class === "ok") && <ResultView outcome={outcome} />}
      </div>
    </Card>
  );
}

// The Meter page's card. `shown` is the whole answer of ppk2.show (a result, or null while it is being asked); `running` is whether a session holds the unit.
export function Profiler({ commands, shown, running, session, reload, onDone }) {
  const mode = find(commands, "ppk2.mode");
  const supply = find(commands, "ppk2.supply");
  const [accept, setAccept] = useState(false);
  const [mv, setMv] = useState("");
  const [outcome, setOutcome] = useState(null);
  const d = shown && shown.body && shown.body.data && Object.keys(shown.body.data).length ? shown.body.data : null;
  const pending = Boolean(outcome && outcome.pending);
  const volts = mv !== "" ? mv : d ? String(d.vdd_mv) : "";
  const voltsNumber = Number(volts);
  const voltsOk = Number.isInteger(voltsNumber) && voltsNumber >= 800 && voltsNumber <= 5000;

  async function act(command, args) {
    setOutcome({ pending: true });
    try {
      const r = await writeJson(command.path, args);
      setOutcome(r.ok ? null : r);
      if (onDone) onDone(r);
      reload();
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  const facts = running && session && session.source === "ppk2"
    ? "A session is using the unit, so it cannot be asked or changed until it ends."
    : d ? headline("ppk2.show", d) : null;
  return (
    <Card title="The power profiler" aside={<Button tone="quiet" onClick={reload} disabled={running}>Check again</Button>}>
      <div className="space-y-3">
        {facts ? <p className="break-words text-[15px] font-medium" aria-label="Profiler">{facts}</p>
          : shown ? <ResultView outcome={shown} /> : <p className="text-[13px] text-slate-600 dark:text-slate-400">Asking the profiler…</p>}
        {d && !running && d.vdd_from && d.supply !== "on" && (
          <p className="text-[13px] text-slate-600 dark:text-slate-400">The unit reports its supply voltage only when something connects to it, so {d.vdd_mv} mV is {d.vdd_from}.</p>
        )}
        <p className="text-[13px] text-slate-600 dark:text-slate-400">
          <strong>Ampere mode</strong> measures a board that has its own supply, wired through the unit. <strong>Source mode</strong> lets the unit power the board, and it does not
          say whether its output is on. Measuring never changes either.
        </p>
        {d && !running && (mode || supply) && (
          <div className="space-y-3 rounded-md border border-slate-200 p-3 dark:border-slate-800">
            <div className="flex flex-wrap items-end gap-2">
              {mode && ["ampere", "source"].map((m) => (
                <Button key={m} tone={d.mode === m ? "primary" : "quiet"} aria-pressed={d.mode === m} disabled={pending || d.mode === m}
                  onClick={() => act(mode, { mode: m, ...(accept ? { accept_supply: true } : {}) })}>{m === "ampere" ? "Ampere mode" : "Source mode"}</Button>
              ))}
              {supply && d.mode === "source" && (
                <>
                  <div className="w-32"><Field label="Voltage, mV"><input className={inputClass} inputMode="numeric" value={volts} onChange={(e) => setMv(e.target.value)}
                    aria-label="Source voltage in millivolts" /></Field></div>
                  <Button disabled={pending || !voltsOk || !accept} onClick={() => act(supply, { on: true, mv: voltsNumber, accept_supply: true })}>Switch on</Button>
                  <Button tone="danger" disabled={pending} onClick={() => act(supply, { on: false, ...(voltsOk ? { mv: voltsNumber } : {}) })}>Switch off</Button>
                </>
              )}
            </div>
            <label className="flex items-start gap-2 text-[13px]">
              <input type="checkbox" className="mt-1" checked={accept} onChange={(e) => setAccept(e.target.checked)} aria-label="I mean to change what powers the board" />
              <span className={cx(accept && "font-medium")}>I mean to change what powers the board. Switching the output on, or leaving source mode, can apply or cut its supply.</span>
            </label>
            {outcome && !outcome.pending && <ResultView outcome={outcome} />}
          </div>
        )}
      </div>
    </Card>
  );
}

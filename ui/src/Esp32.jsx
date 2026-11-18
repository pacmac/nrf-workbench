import { useEffect, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { ESP_CONNECT_S } from "./format.js";
import { Button, Card, Chip, Field, SlugNote, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";

// The ESP32 boards (docs/specs/esp32.md). A board is an ESP32 only once someone declares it: a USB id says nothing about
// what is behind a bridge chip, and the bench's own dongle has one. Nothing here is probed by looking: Identify, Reset and
// Flash reset the board over the adapter, so each is a button somebody presses. Every action is a command the service
// listed, sent to the path it listed, and its answer is shown whole.

const NONE = "";

// The targets the service knows, reloaded when `attempt` changes. null while loading, [] when there are none.
export function useEspTargets(command, attempt) {
  const [state, setState] = useState({ targets: null, native: [], error: "" });
  useEffect(() => {
    if (!command) return undefined;
    let live = true;
    readJson(command.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) setState({ targets: r.body.data.targets || [], native: r.body.data.undeclared_native || [], error: "" });
      else setState((old) => ({ ...old, targets: old.targets || [], error: `HTTP ${r.status}` + (r.body && r.body.message ? `: ${r.body.message}` : "") }));
    }).catch((e) => { if (live) setState((old) => ({ ...old, targets: old.targets || [], error: String(e && e.message ? e.message : e) })); });
    return () => { live = false; };
  }, [command, attempt]);
  return state;
}

function stateChip(t) {
  if (t.attached === 1 && t.node) return <Chip tone="green" title={t.node}>attached</Chip>;
  if (t.attached === 1) return <Chip tone="amber" title={t.problem}>no port</Chip>;
  if (t.attached > 1) return <Chip tone="red" title={t.problem}>{t.attached} match</Chip>;
  return <Chip tone="slate">not attached</Chip>;
}

export default function EspTargets({ commands, targets, native, error, onChanged, onDone }) {
  const find = (name) => (commands || []).find((c) => c.name === name) || null;
  const declare = find("esp32.declare");
  const forget = find("esp32.forget");
  const identify = find("esp32.identify");
  const reset = find("esp32.reset");
  const wiring = find("wiring.show");

  const [adapters, setAdapters] = useState([]);                    // the USB adapters the wiring page lists, for the picker
  const [name, setName] = useState("");
  const [pick, setPick] = useState(NONE);                          // "serial:XYZ" or "slot:1-4": the argument and its value
  const [outcome, setOutcome] = useState(null);

  useEffect(() => {
    if (!wiring) return undefined;
    let live = true;
    readJson(wiring.path).then((r) => { if (live && r.ok && r.body && r.body.data) setAdapters(r.body.data.adapters || []); }).catch(() => {});
    return () => { live = false; };
  }, [wiring, targets]);

  const pending = Boolean(outcome && outcome.pending);
  // An adapter is offered by its serial when it has one nobody else shares, otherwise by the USB port it is in. One that a
  // wiring connection or another target already uses is not offered at all.
  const taken = new Set((targets || []).map((t) => `${t.key_kind}:${t.key}`));
  const shared = (serial) => adapters.filter((a) => a.serial === serial).length > 1;
  const offered = (a) => {
    const byPort = !a.serial || shared(a.serial);
    return { key: byPort ? `slot:${a.slot}` : `serial:${a.serial}`, label: `${a.name}${a.serial && !byPort ? ` (${a.serial})` : ""}${byPort ? `, USB port ${a.slot}` : ""}` };
  };
  const choices = [
    ...(native || []).map((n) => ({ value: `serial:${n.serial}`, label: `${n.product || "ESP32 USB"} (${n.serial}), built-in USB` })),
    ...adapters.filter((a) => a.node && !a.held_by && !(native || []).some((n) => n.serial === a.serial))
      .map((a) => ({ ...offered(a), value: offered(a).key }))
      .filter((c) => !taken.has(c.value)),
  ];
  const nameOk = /^[a-z0-9][a-z0-9._-]{0,63}$/.test(name.trim());
  const canDeclare = Boolean(declare) && nameOk && pick !== NONE && !pending;

  async function run(request) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok && onChanged) onChanged();
      return r;
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
      return null;
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (!canDeclare) return;
    const [kind, key] = [pick.slice(0, pick.indexOf(":")), pick.slice(pick.indexOf(":") + 1)];
    const r = await run(writeJson(declare.path, { name: name.trim(), [kind]: key }));
    if (r && r.ok) { setName(""); setPick(NONE); }
  }

  const act = (cmd, args) => run(writeJson(cmd.path, args));
  // A give-up ceiling for esptool's connect and reset, not a wait: it returns when the chip has answered.
  const bound = ESP_CONNECT_S;

  if (commands && !declare) return null;                         // the folder these are kept in is not configured on this host

  return (
    <Card title="ESP32 boards" aside={targets && <Chip>{targets.length} declared</Chip>}>
      <div className="space-y-4">
        {error && <p role="alert" className="text-sm text-amber-900 dark:text-amber-200">The boards could not be listed: <span className="font-mono">{error}</span>.</p>}
        {targets === null ? (
          <p className="text-sm text-slate-600 dark:text-slate-400">Loading the boards…</p>
        ) : targets.length === 0 ? (
          <p className="text-sm text-slate-600 dark:text-slate-400">No ESP32 declared yet. Plug a board in, then declare it below: a USB adapter is only an ESP32 once you say so.</p>
        ) : (
          <ul className="space-y-2" aria-label="ESP32 boards">
            {targets.map((t) => (
              <li key={t.name} className="flex flex-wrap items-start gap-x-3 gap-y-2 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 font-medium"><span className="break-all">{t.name}</span> {stateChip(t)}</p>
                  <p className="text-xs text-slate-600 dark:text-slate-400">
                    {t.key_kind === "serial" ? <>adapter serial <span className="font-mono">{t.key}</span></> : <>USB port <span className="font-mono">{t.key}</span>, where it is plugged</>}
                    {t.problem && t.attached !== 1 ? <> · {t.problem}</> : null}
                  </p>
                  {t.console && <p className="text-xs text-slate-600 dark:text-slate-400">console {t.console.state}{t.console.lines ? `, ${t.console.lines} lines` : ""}, receive-only</p>}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {identify && <Button tone="quiet" disabled={pending || t.attached !== 1} title="Asks the chip what it is. This resets the board." onClick={() => act(identify, { target: t.name, bound_s: bound })} aria-label={`Identify ${t.name}`}>Identify</Button>}
                  {reset && <Button tone="quiet" disabled={pending || t.attached !== 1} title="Resets the board to run its application" onClick={() => act(reset, { target: t.name, mode: "run", bound_s: bound })} aria-label={`Reset ${t.name}`}>Reset</Button>}
                  {reset && <Button tone="quiet" disabled={pending || t.attached !== 1} title="Resets the board and holds it in its ROM download mode" onClick={() => act(reset, { target: t.name, mode: "bootloader", bound_s: bound })} aria-label={`Hold ${t.name} in bootloader`}>Hold in bootloader</Button>}
                  {forget && <Button tone="quiet" disabled={pending} onClick={() => act(forget, { name: t.name })} aria-label={`Forget ${t.name}`}>Forget</Button>}
                </div>
              </li>
            ))}
          </ul>
        )}

        {declare && (
          <form onSubmit={submit} className="space-y-3 rounded-lg border border-slate-200 p-3 dark:border-slate-800">
            <p className="text-sm font-medium">Declare an ESP32 board</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Adapter" hint="The USB adapter the board is plugged into. Plugged in just now and not listed? Use Look again on the Wiring page.">
                <select className={inputClass} value={pick} onChange={(e) => setPick(e.target.value)} aria-label="Adapter">
                  <option value={NONE}>Choose an adapter…</option>
                  {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </Field>
              <Field label="Name" hint="What you will call this board.">
                <input className={inputClass} type="text" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} aria-label="Board name" autoComplete="off" spellCheck="false" />
                <SlugNote value={name} />
              </Field>
            </div>
            {pick.startsWith("slot:") && (
              <p className="text-xs text-slate-600 dark:text-slate-400">
                This adapter has no serial of its own, or shares one with another, so it is found by the USB port it is in: moving the board to another port makes it a different board.
              </p>
            )}
            {choices.length === 0 && <p className="text-xs text-slate-600 dark:text-slate-400">No free adapter is plugged in. Plug the board in, then press Look again on the Wiring page.</p>}
            <Button type="submit" disabled={!canDeclare}>Declare board</Button>
          </form>
        )}

        {outcome && <ResultView outcome={outcome} pendingText="Working…" />}
      </div>
    </Card>
  );
}

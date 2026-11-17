import { useEffect, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { PROJECT_NAME, PROJECT_RULE_TEXT } from "./format.js";
import { Button, Card, Chip, Field, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";

// The GPIO pin map (docs/specs/dashboard.md, "The GPIO page"). It records which header pin is assigned to which
// service and action; it never drives or reads a pin. Every action is a command the service listed, sent to the path it
// listed, and every refusal (pin_reserved, pin_taken, voltage_unsafe ...) is the service's, shown whole.

const SLUG = PROJECT_NAME;                    // a slug is the same rule everywhere: a-z 0-9 . _ - , 1 to 64, starting alphanumeric
const BLANK = {
  name: "", direction: "", service: "", action: "", purpose: "", active_low: "", voltage_v: "", voltage_verified: "",
  initial: "", max_pulse_ms: "", drives_dut: "", pull: "",
};
const YES_NO = [["yes", "yes"], ["no", "no"]];

const boolOf = (text) => (text === "yes" ? true : text === "no" ? false : null);
const textOf = (value) => (value === true ? "yes" : value === false ? "no" : "");

function fromRecord(p) {
  return {
    name: p.name, direction: p.direction, service: p.service, action: p.action, purpose: p.purpose,
    active_low: textOf(p.active_low), voltage_v: String(p.voltage.signal), voltage_verified: textOf(p.voltage.verified),
    initial: p.initial || "", max_pulse_ms: p.max_pulse_ms != null ? String(p.max_pulse_ms) : "", drives_dut: textOf(p.drives_dut),
    pull: p.pull || "",
  };
}

// What is sent: only the fields that belong to the chosen direction, every one stated. Nothing is defaulted here.
function payload(gpio, v) {
  const body = {
    name: v.name.trim(), gpio, direction: v.direction, service: v.service.trim(), action: v.action.trim(), purpose: v.purpose.trim(),
    active_low: boolOf(v.active_low), voltage_v: Number(v.voltage_v), voltage_verified: boolOf(v.voltage_verified),
  };
  if (v.direction === "output") {
    body.initial = v.initial;
    body.max_pulse_ms = Number(v.max_pulse_ms);
    body.drives_dut = boolOf(v.drives_dut);
  } else if (v.direction === "input") {
    body.pull = v.pull;
  }
  return body;
}

function complete(v) {
  const common = SLUG.test(v.name.trim()) && v.direction && SLUG.test(v.service.trim()) && SLUG.test(v.action.trim())
    && v.purpose.trim() && v.active_low && v.voltage_v !== "" && v.voltage_verified;
  if (v.direction === "output") return Boolean(common && v.initial && v.max_pulse_ms !== "" && v.drives_dut);
  if (v.direction === "input") return Boolean(common && v.pull);
  return false;
}

function Choose({ label, hint, value, onChange, options }) {
  return (
    <Field label={label} hint={hint}>
      <select className={inputClass} value={value} onChange={(e) => onChange(e.target.value)} required>
        <option value="">choose…</option>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </Field>
  );
}

const CLASS_TONE = { free: "slate", bus: "blue", reserved: "red" };

function PinCell({ row, selected, onPick }) {
  if (row.kind !== "gpio") {
    return (
      <div className="flex min-h-10 items-center gap-2 rounded-lg px-2 py-1 text-xs text-slate-500 dark:text-slate-400">
        <span className="w-5 text-right font-mono">{row.pin}</span>
        <span>{row.label}</span>
      </div>
    );
  }
  const reserved = row.class === "reserved";
  return (
    <button
      type="button"
      disabled={reserved}
      onClick={() => onPick(row.gpio)}
      aria-pressed={selected}
      aria-label={`Pin ${row.pin}, GPIO ${row.gpio}, ${row.assigned ? `assigned to ${row.assigned}` : row.class}`}
      title={reserved ? "The HAT ID EEPROM's bus: never offered" : row.alt || undefined}
      className={`flex min-h-10 w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5 rounded-lg border px-2 py-1 text-left text-xs disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? "border-blue-600 bg-blue-100 dark:border-blue-400 dark:bg-blue-950"
          : row.assigned
            ? "border-emerald-400 bg-emerald-50 dark:border-emerald-700 dark:bg-emerald-950"
            : "border-slate-300 bg-white hover:bg-slate-100 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800"
      }`}
    >
      <span className="w-5 text-right font-mono">{row.pin}</span>
      <span className="font-mono font-medium">{row.label}</span>
      {row.assigned ? <span className="truncate font-medium">{row.assigned}</span> : <Chip tone={CLASS_TONE[row.class]}>{row.class}</Chip>}
    </button>
  );
}

export default function Gpio({ commands, onDone }) {
  const find = (name) => (commands || []).find((c) => c.name === name) || null;
  const headerCmd = find("gpio.header");
  const pinsCmd = find("gpio.pins");
  const assign = find("gpio.assign");
  const unassign = find("gpio.unassign");

  const [rows, setRows] = useState(null);
  const [pins, setPins] = useState([]);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [selected, setSelected] = useState(null);                 // a BCM number
  const [values, setValues] = useState(BLANK);
  const [outcome, setOutcome] = useState(null);
  const [ask, setAsk] = useState(false);

  useEffect(() => {
    if (!headerCmd || !pinsCmd) return undefined;
    let live = true;
    Promise.all([readJson(headerCmd.path), readJson(pinsCmd.path)]).then(([h, p]) => {
      if (!live) return;
      if (h.ok && h.body && h.body.data && p.ok && p.body && p.body.data) {
        setRows(h.body.data.header);
        setPins(p.body.data.pins);
        setError("");
      } else {
        setRows((old) => old || []);
        const bad = !h.ok ? h : p;
        setError(`HTTP ${bad.status}` + (bad.body && bad.body.message ? `: ${bad.body.message}` : ""));
      }
    }).catch((e) => { if (live) { setRows((old) => old || []); setError(String(e && e.message ? e.message : e)); } });
    return () => { live = false; };
  }, [headerCmd, pinsCmd, attempt]);

  function pick(gpio) {
    const existing = pins.find((p) => p.gpio === gpio);
    setSelected(gpio);
    setValues(existing ? fromRecord(existing) : BLANK);
    setOutcome(null);
    setAsk(false);
  }

  const set = (key) => (value) => setValues((old) => ({ ...old, [key]: value }));
  const current = pins.find((p) => p.gpio === selected) || null;
  const pending = Boolean(outcome && outcome.pending);

  async function write(request) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok) setAttempt((n) => n + 1);
      return r;
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
      return null;
    }
  }

  async function save(event) {
    event.preventDefault();
    if (!assign || selected === null || !complete(values) || pending) return;
    await write(writeJson(assign.path, payload(selected, values)));
  }

  async function remove() {
    setAsk(false);
    const r = await write(writeJson(unassign.path, { name: current.name }));
    if (r && r.ok) { setSelected(null); setValues(BLANK); }
  }

  if (commands && !headerCmd) return null;
  if (rows === null) return <Card title="GPIO"><p role="status" className="text-sm">Loading the header…</p></Card>;

  const held = new Map(pins.map((p) => [p.gpio, p.name]));
  const table = rows.map((r) => ({ ...r, assigned: r.gpio !== null ? held.get(r.gpio) || null : null }));
  const left = table.filter((r) => r.pin % 2 === 1);
  const right = table.filter((r) => r.pin % 2 === 0);

  return (
    <div className="space-y-4">
      <Card title="GPIO">
        {error && <p role="note" className="mb-3 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">The pin map could not be read ({error}).</p>}
        <p className="mb-3 text-sm text-slate-700 dark:text-slate-300">
          The Pi&apos;s 40-pin header as it is on the board, pin 1 at the top. This records which pin is for which service and action;
          it does not drive or read any pin.
        </p>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1" role="group" aria-label="40-pin header">
          {left.map((l, i) => (
            <div key={l.pin} className="contents">
              <PinCell row={l} selected={selected === l.gpio && l.gpio !== null} onPick={pick} />
              <PinCell row={right[i]} selected={selected === right[i].gpio && right[i].gpio !== null} onPick={pick} />
            </div>
          ))}
        </div>
      </Card>

      <Card title={selected === null ? "Assignment" : `Assignment for GPIO ${selected}${current ? ` (${current.name})` : ""}`}>
        {selected === null ? (
          <p className="text-sm text-slate-600 dark:text-slate-400">Choose a GPIO above to assign it, or an assigned one to edit it.</p>
        ) : (
          <form onSubmit={save} className="space-y-3">
            <Field label="Name" hint={PROJECT_RULE_TEXT}>
              <input className={inputClass + " font-mono"} value={values.name} onChange={(e) => set("name")(e.target.value)} autoComplete="off" spellCheck={false} />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Service" hint="Who uses it, a short name (programmer, power, manual …).">
                <input className={inputClass + " font-mono"} value={values.service} onChange={(e) => set("service")(e.target.value)} autoComplete="off" spellCheck={false} />
              </Field>
              <Field label="Action" hint="What it is for (reset, boot, power-cycle, led …).">
                <input className={inputClass + " font-mono"} value={values.action} onChange={(e) => set("action")(e.target.value)} autoComplete="off" spellCheck={false} />
              </Field>
            </div>
            <Field label="Purpose" hint="One sentence, at most 200 characters.">
              <input className={inputClass} value={values.purpose} maxLength={200} onChange={(e) => set("purpose")(e.target.value)} autoComplete="off" />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Choose label="Direction" value={values.direction} onChange={set("direction")} options={[["input", "input"], ["output", "output"]]} />
              <Choose label="Active low" hint="Yes if the line is asserted by driving it low." value={values.active_low} onChange={set("active_low")} options={YES_NO} />
              <Field label="Signal voltage (V)" hint="The volts the connected signal swings. Above 3.3 is refused: header pins are not 5 V tolerant.">
                <input className={inputClass} type="number" step="any" value={values.voltage_v} onChange={(e) => set("voltage_v")(e.target.value)} />
              </Field>
              <Choose label="Voltage measured" hint="Yes only if someone measured it." value={values.voltage_verified} onChange={set("voltage_verified")} options={[["yes", "yes, measured"], ["no", "no, not measured"]]} />
            </div>
            {values.direction === "output" && (
              <div className="grid gap-3 sm:grid-cols-2">
                <Choose label="State when taken" hint="The state the line is put in when it is first used." value={values.initial} onChange={set("initial")} options={[["inactive", "inactive"], ["active", "active"]]} />
                <Field label="Longest pulse (ms)" hint="The longest pulse this line may ever be given. Derive it from what the line drives; there is no suggested value.">
                  <input className={inputClass} type="number" min="1" step="1" value={values.max_pulse_ms} onChange={(e) => set("max_pulse_ms")(e.target.value)} />
                </Field>
                <Choose label="Drives a DUT pin" hint="Yes if it reaches a device under test: reset, boot or power." value={values.drives_dut} onChange={set("drives_dut")} options={YES_NO} />
              </div>
            )}
            {values.direction === "input" && (
              <Choose label="Pull" hint="The bias on the input." value={values.pull} onChange={set("pull")} options={[["up", "up"], ["down", "down"], ["none", "none"]]} />
            )}
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={!assign || !complete(values) || pending}>{current ? "Save changes" : "Assign"}</Button>
              {current && !ask && <Button tone="quiet" onClick={() => setAsk(true)} disabled={!unassign || pending}>Unassign</Button>}
              {current && ask && (
                <span className="flex items-center gap-2" role="group" aria-label={`Unassign ${current.name}?`}>
                  <span className="text-sm">Unassign?</span>
                  <Button tone="danger" onClick={remove}>Yes</Button>
                  <Button tone="quiet" onClick={() => setAsk(false)}>No</Button>
                </span>
              )}
            </div>
          </form>
        )}
        <div className="mt-3"><ResultView outcome={outcome} pendingText="Saving…" /></div>
      </Card>
    </div>
  );
}

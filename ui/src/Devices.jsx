import { useContext, useState } from "react";
import { writeJson } from "./api.js";
import DeviceRow from "./DeviceRow.jsx";
import { PROJECT_NAME } from "./format.js";
import { Button, Card, Field, NavContext, PageHeader, SlugNote, cx, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";
import Meter from "./meter/Meter.jsx";

// Every device in one list, connected or not, so what is here and what is not can be read at a glance. A row opens to its detail and its
// actions; an ESP32 board or an adapter hands over to the page that owns its actions (docs/specs/dashboard-redesign.md, "Devices page").

// A name is a label kept against the board's USB serial in a file on the bench: it never chooses a board for anything.
function NameEditor({ d, commands, onChanged, onDone }) {
  const find = (n) => (commands || []).find((c) => c.name === n) || null;
  const set = find("devices.name");
  const clear = find("devices.unname");
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState(null);

  async function send(request) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok) { setEditing(false); if (onChanged) onChanged(); }
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  if (!set) return null;
  const pending = Boolean(outcome && outcome.pending);
  const attached = d.state !== "absent";
  return (
    <div className="space-y-2">
      {!editing ? (
        <div className="flex flex-wrap gap-2">
          {attached && <Button tone="quiet" onClick={() => { setText(d.name || ""); setOutcome(null); setEditing(true); }} aria-label={`${d.name ? "Rename" : "Name"} ${d.serial}`}>{d.name ? "Rename" : "Name it"}</Button>}
          {d.name && clear && <Button tone="quiet" disabled={pending} onClick={() => send(writeJson(clear.path, { serial: d.serial }))} aria-label={`Remove the name of ${d.serial}`}>Remove name</Button>}
        </div>
      ) : (
        <form onSubmit={(e) => { e.preventDefault(); send(writeJson(set.path, { serial: d.serial, name: text.trim() })); }} className="space-y-2">
          <Field label="Name">
            <input className={inputClass} type="text" value={text} onChange={(e) => setText(e.target.value.toLowerCase())} aria-label={`Name for ${d.serial}`} autoComplete="off" spellCheck="false" />
          </Field>
          <SlugNote value={text} />
          <div className="flex gap-2">
            <Button type="submit" disabled={pending || !PROJECT_NAME.test(text.trim())}>Save name</Button>
            <Button tone="quiet" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </form>
      )}
      {outcome && !outcome.pending && !outcome.ok && <ResultView outcome={outcome} pendingText="Working…" />}
    </div>
  );
}

// The board the flash dialog is given: the device row says what it is, the dialog needs it in the shape the device feed uses.
const boardOf = (d) => ({ serial: d.serial, mode: d.mode, product: d.product, profile: d.model, name: d.name });

function Detail({ d, commands, onFlash, onChanged, onDone }) {
  const nav = useContext(NavContext);
  const facts = [["Serial", d.serial], ["Product", d.product], ["Mode", d.mode], ["Port", d.node], ["USB port", d.slot]].filter(([, v]) => v);
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
        {facts.map(([k, v]) => (<span key={k} className="contents"><dt className="text-slate-600 dark:text-slate-400">{k}</dt><dd className="break-all font-mono">{v}</dd></span>))}
      </dl>
      <div className="flex flex-wrap gap-2">
        {d.kind === "nrf" && d.state !== "absent" && (
          <Button onClick={() => onFlash(boardOf(d))}>Flash…</Button>
        )}
        {d.kind === "nrf" && <Button tone="quiet" onClick={() => nav.go("wiring")}>Wiring</Button>}
        {d.kind === "esp32" && <Button tone="quiet" onClick={() => nav.go("firmware")}>{d.undeclared ? "Declare it" : "Flash, reset, identify"}</Button>}
        {d.kind === "adapter" && <Button tone="quiet" onClick={() => nav.go("wiring")}>Connect it</Button>}
        {d.connections && d.connections.length > 0 && <Button tone="quiet" onClick={() => nav.go("console")}>Console</Button>}
      </div>
      {d.kind === "nrf" && <NameEditor d={d} commands={commands} onChanged={onChanged} onDone={onDone} />}
    </div>
  );
}

const TABS = [["connected", "Connected"], ["absent", "Not attached"], ["all", "All"]];

export default function Devices({ overview, commands, onFlash, onChanged, onDone }) {
  const [tab, setTab] = useState("connected");
  const [open, setOpen] = useState(null);
  const { data, error } = overview;
  if (!data) {
    return (
      <div className="space-y-4">
        <PageHeader title="Devices" topic="devices" />
        <p role="status" className="text-[15px] text-slate-600 dark:text-slate-400">{error ? `The devices could not be read: ${error}` : "Reading the bench…"}</p>
      </div>
    );
  }
  const all = data.devices;
  const here = all.filter((d) => d.state !== "absent");
  const gone = all.filter((d) => d.state === "absent");
  const counts = { connected: here.length, absent: gone.length, all: all.length };
  const shown = tab === "connected" ? here : tab === "absent" ? gone : all;
  const empty = { connected: "Nothing is attached. Plug a board in.", absent: "Every device the bench knows is attached.", all: "No device is attached or known." };
  return (
    <div className="space-y-4">
      <PageHeader title="Devices" topic="devices" />
      <div role="tablist" aria-label="Show devices" className="flex gap-1 border-b border-slate-300 dark:border-slate-700">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => setTab(id)}
            className={cx("min-h-11 border-b-2 px-3 text-[15px] font-medium", tab === id
              ? "border-slate-900 text-slate-900 dark:border-slate-100 dark:text-slate-100"
              : "border-transparent text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-slate-100")}>
            {label} <span className="font-normal text-slate-600 dark:text-slate-400">{counts[id]}</span>
          </button>
        ))}
      </div>
      <Card className="p-0!">
        {shown.length === 0 ? (
          <p className="px-4 py-4 text-[13px] text-slate-600 dark:text-slate-400">{empty[tab]}</p>
        ) : (
          <ul className="divide-y divide-slate-200 px-3 dark:divide-slate-800" aria-label="Devices">
            {shown.map((d) => (
              <DeviceRow key={d.id} d={d} open={open === d.id} onOpen={() => setOpen(open === d.id ? null : d.id)}>
                <Detail d={d} commands={commands} onFlash={onFlash} onChanged={onChanged} onDone={onDone} />
              </DeviceRow>
            ))}
          </ul>
        )}
      </Card>
      <Meter commands={commands} onDone={onDone} />
    </div>
  );
}

import { useState } from "react";
import { Button, Card, Chip, PageHeader } from "../ui.jsx";
import { useWiring, wiringActions } from "./store.js";
import { useLiveWiring } from "./live.js";
import { INTENTS, claimsOf, hueFor, short } from "./model.js";
import WireLine from "./WireLine.jsx";
import HeaderMap from "./HeaderMap.jsx";
import AddSheet from "./AddSheet.jsx";

// The Wiring page (docs/specs/wiring.md). The device is the unit: device cards across the top, its connections under it as
// wires. Where the service has the wiring commands the page is LIVE (it reads and writes the bench); where it does not it is
// a clickable preview on sample data, and says so. The two are the same components over two sources.

const SAMPLE_BUILT = INTENTS.map((i) => i.value);

// The sample store behind the same interface the live one has (async, answering {ok, message}).
const sampleActions = {
  connect: async (device, spec) => { wiringActions.connect(device, spec); return { ok: true }; },
  change: async (id, spec) => { wiringActions.change(id, spec); return { ok: true }; },
  disconnect: async (id) => { wiringActions.disconnect(id); return { ok: true }; },
  dismiss: wiringActions.dismiss,
};

function Preview() {
  return (
    <div role="note" className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
      <p><strong>Preview.</strong> These devices and wires are sample data, so you can try the page. Nothing here touches the bench yet.</p>
      <div className="flex flex-wrap gap-2">
        <Button tone="quiet" onClick={wiringActions.plugIn}>Simulate: plug in a USB adapter</Button>
        <Button tone="quiet" onClick={wiringActions.reset}>Start again</Button>
      </div>
    </div>
  );
}

// The top of the live page: a title and the Help link. "Look again" is only for a host that cannot say when a USB device is plugged in.
function LiveNote({ error, onLookAgain }) {
  return (
    <div className="space-y-2">
      <PageHeader title="Wiring" topic="wiring" aside={<Button tone="quiet" onClick={onLookAgain} className="min-h-9! px-2!">Look again</Button>} />
      {error && <p role="alert" className="text-[13px] text-red-800 dark:text-red-300">The bench could not be read ({error}).</p>}
    </div>
  );
}

function Inbox({ adapters, devices, onSetUp, onDismiss }) {
  const [choice, setChoice] = useState({});
  if (adapters.length === 0) return null;
  return (
    <div className="space-y-2">
      {adapters.map((a) => (
        <div key={a.id} role="group" aria-label={`${a.name} is ${a.ids === "pi-uart" ? "available" : "plugged in"} but not connected`} className="space-y-2 rounded-xl border border-blue-300 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950">
          <p className="text-sm">
            <strong>{a.name}</strong> <span className="font-mono text-xs">({a.ids}, {short(a.id)})</span> is {a.ids === "pi-uart" ? "built into the Pi" : "plugged in"}.
            Which device is it wired to?
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label={`Device wired to ${a.name}`}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950"
              value={choice[a.id] || ""}
              onChange={(e) => setChoice({ ...choice, [a.id]: e.target.value })}
            >
              <option value="">choose a device…</option>
              {devices.map((d) => <option key={d.serial} value={d.serial}>{d.model} {short(d.serial)} ({d.mode})</option>)}
            </select>
            <Button disabled={!choice[a.id]} onClick={() => onSetUp(a, choice[a.id])}>Set up the connection</Button>
            <Button tone="quiet" onClick={() => onDismiss(a.id)}>Not now</Button>
          </div>
        </div>
      ))}
    </div>
  );
}

function DeviceCard({ device, hue, count, selected, onPick }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onPick}
      className={`flex w-full items-start gap-3 rounded-xl border p-3 text-left ${selected ? `${hue.edge} ${hue.tint}` : "border-slate-200 bg-white hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-900 dark:hover:bg-slate-800"}`}
    >
      <span aria-hidden="true" className={`mt-1 h-3 w-3 shrink-0 rounded-full ${hue.dot}`} />
      <span className="min-w-0">
        <span className="block text-sm font-semibold">{device.model} <span className="font-mono text-xs font-normal">{short(device.serial)}</span></span>
        <span className="mt-1 flex flex-wrap items-center gap-2">
          <Chip tone={device.mode === "app" ? "green" : "amber"}>{device.mode}</Chip>
          <span className="text-xs text-slate-600 dark:text-slate-400">{count === 0 ? "nothing connected" : `${count} connection${count === 1 ? "" : "s"}`}</span>
        </span>
      </span>
    </button>
  );
}

function Page({ w, actions, live, error, commands, onOpenPage }) {
  const [selected, setSelected] = useState(w.devices.length ? w.devices[0].serial : null);
  const [sheet, setSheet] = useState(null);                 // {editingId, initial, key}
  const [focusId, setFocusId] = useState(null);
  const [wide] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 640px)").matches);

  const device = w.devices.find((d) => d.serial === selected) || w.devices[0] || null;
  const base = { devices: w.devices, adapters: w.adapters, probes: w.probes, connections: w.connections, built: w.built };
  const claimed = new Set(w.connections.flatMap((c) => claimsOf(c.spec).exclusive));
  // An adapter something already holds (a connection, or an ESP32 board's own) is not offered again.
  const unconnected = w.adapters.filter((a) => !a.problem && !a.heldBy && !claimed.has(`adapter:${a.id}`) && !w.dismissed.includes(a.id));

  if (!device) {
    return (
      <div className="space-y-4">
        {live ? <LiveNote error={error} onLookAgain={actions.refresh} /> : <><PageHeader title="Wiring" topic="wiring" /><Preview /></>}
        <Card><p className="text-[13px] text-slate-600 dark:text-slate-400">No nRF board is attached. Plug one in and it appears here.</p></Card>
      </div>
    );
  }
  const hue = hueFor(w.devices, device.serial);
  const mine = w.connections.filter((c) => c.device === device.serial);

  function setUp(adapter, serial) {
    setSelected(serial);
    setSheet({ editingId: null, initial: { intent: "log", via: `adapter:${adapter.id}` }, key: `${adapter.id}-${serial}` });
  }
  async function save(spec) {
    const res = sheet.editingId ? await actions.change(sheet.editingId, spec) : await actions.connect(device.serial, spec);
    if (!res.ok) return res.message;
    setSheet(null);
    return null;
  }

  return (
    <div className="space-y-4">
      {live ? <LiveNote error={error} onLookAgain={actions.refresh} /> : <><PageHeader title="Wiring" topic="wiring" /><Preview /></>}
      <Inbox adapters={unconnected} devices={w.devices} onSetUp={setUp} onDismiss={actions.dismiss} />

      <div role="group" aria-label="Devices" className="grid gap-2 sm:grid-cols-2">
        {w.devices.map((d) => (
          <DeviceCard
            key={d.serial}
            device={d}
            hue={hueFor(w.devices, d.serial)}
            count={w.connections.filter((c) => c.device === d.serial).length}
            selected={d.serial === device.serial}
            onPick={() => { setSelected(d.serial); setSheet(null); }}
          />
        ))}
      </div>

      <Card
        title={`${device.model} ${short(device.serial)}`}
        aside={!sheet && <Button onClick={() => setSheet({ editingId: null, initial: {}, key: "new" })}>Connect something</Button>}
      >
        {mine.length === 0 && !sheet && (
          <p className="text-[13px] text-slate-600 dark:text-slate-400">Nothing is wired to this device yet.</p>
        )}
        {mine.length > 0 && (
          <ul className="space-y-2" aria-label={`Connections of ${device.model} ${short(device.serial)}`}>
            {mine.map((c) => (
              <WireLine
                key={c.id}
                conn={c}
                ctx={base}
                hue={hue}
                focused={focusId === c.id}
                onFocus={setFocusId}
                onEdit={() => setSheet({ editingId: c.id, initial: c.spec, key: c.id })}
                onRemove={() => actions.disconnect(c.id)}
                onOpenConsole={() => onOpenPage && onOpenPage("console")}
              />
            ))}
          </ul>
        )}
        {sheet && (
          <div className={mine.length > 0 ? "mt-3" : ""}>
            <AddSheet key={sheet.key} device={device} hue={hue} base={base} initial={sheet.initial} editingId={sheet.editingId} onSave={save} onCancel={() => setSheet(null)} />
          </div>
        )}
      </Card>

      {!live && (
        <Card title="The Pi's header">
          <details open={wide}>
            <summary className="cursor-pointer text-sm font-medium">Which pin is wired to what</summary>
            <HeaderMap connections={w.connections} devices={w.devices} selected={device.serial} focusId={focusId} />
          </details>
        </Card>
      )}
    </div>
  );
}

export default function Wiring({ commands, refreshKey, onOpenPage }) {
  const sample = useWiring();
  const live = useLiveWiring(commands, refreshKey);
  if (live.live) {
    if (!live.state.loaded) return <div className="space-y-4"><PageHeader title="Wiring" topic="wiring" /><p role="status" className="text-[13px] text-slate-600 dark:text-slate-400">Reading the bench…{live.error ? ` (${live.error})` : ""}</p></div>;
    return <Page key="live" w={live.state} actions={live.actions} live error={live.error} commands={commands} onOpenPage={onOpenPage} />;
  }
  return <Page key="sample" w={{ ...sample, built: SAMPLE_BUILT }} actions={sampleActions} live={false} error="" commands={commands} onOpenPage={onOpenPage} />;
}

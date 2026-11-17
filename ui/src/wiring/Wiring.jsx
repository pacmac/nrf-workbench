import { useState } from "react";
import { Button, Card, Chip } from "../ui.jsx";
import { useWiring, wiringActions } from "./store.js";
import { claimsOf, hueFor, short } from "./model.js";
import WireLine from "./WireLine.jsx";
import HeaderMap from "./HeaderMap.jsx";
import AddSheet from "./AddSheet.jsx";

// The Wiring page (docs/specs/wiring.md). The device is the unit: device cards across the top, its connections under
// it as wires, the Pi's header coloured by device below. THIS IS A PREVIEW ON SAMPLE DATA: nothing here reads or sends
// anything to the bench, and the page says so.

function Preview() {
  return (
    <div role="note" className="space-y-2 rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
      <p><strong>Preview.</strong> These devices and wires are sample data, so you can try the page. Nothing here touches the bench yet.</p>
      <div className="flex flex-wrap gap-2">
        <Button tone="quiet" onClick={wiringActions.plugIn}>Simulate: plug in a USB adapter</Button>
        <Button tone="quiet" onClick={wiringActions.reset}>Start again</Button>
      </div>
    </div>
  );
}

function Inbox({ adapters, devices, onSetUp }) {
  const [choice, setChoice] = useState({});
  if (adapters.length === 0) return null;
  return (
    <div className="space-y-2">
      {adapters.map((a) => (
        <div key={a.id} role="group" aria-label={`${a.name} is plugged in but not connected`} className="space-y-2 rounded-xl border border-blue-300 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950">
          <p className="text-sm">
            <strong>{a.name}</strong> <span className="font-mono text-xs">({a.ids}, {short(a.id)}, {a.node})</span> was just plugged in and is not connected to anything.
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
            <Button tone="quiet" onClick={() => wiringActions.dismiss(a.id)}>Not now</Button>
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

export default function Wiring({ onOpenPage }) {
  const w = useWiring();
  const [selected, setSelected] = useState(w.devices[0].serial);
  const [sheet, setSheet] = useState(null);                 // {editingId, initial}
  const [focusId, setFocusId] = useState(null);
  const [wide] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 640px)").matches);

  const device = w.devices.find((d) => d.serial === selected) || w.devices[0];
  const hue = hueFor(w.devices, device.serial);
  const base = { devices: w.devices, adapters: w.adapters, probes: w.probes, connections: w.connections };
  const mine = w.connections.filter((c) => c.device === device.serial);

  // An adapter is "plugged in, not connected" until a connection claims it.
  const claimed = new Set(w.connections.flatMap((c) => claimsOf(c.spec).exclusive));
  const unconnected = w.adapters.filter((a) => !claimed.has(`adapter:${a.id}`) && !w.dismissed.includes(a.id));

  function setUp(adapter, serial) {
    setSelected(serial);
    setSheet({ editingId: null, initial: { intent: "log", via: `adapter:${adapter.id}` }, key: `${adapter.id}-${serial}` });
  }
  function save(spec) {
    if (sheet.editingId) wiringActions.change(sheet.editingId, spec);
    else wiringActions.connect(device.serial, spec);
    setSheet(null);
  }

  return (
    <div className="space-y-4">
      <Preview />
      <Inbox adapters={unconnected} devices={w.devices} onSetUp={setUp} />

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
          <p className="text-sm text-slate-700 dark:text-slate-300">
            Nothing is wired to this device yet. Plug a USB serial adapter into the Pi and it will appear above, or choose Connect something to start from a Pi pin.
          </p>
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
                onRemove={() => wiringActions.disconnect(c.id)}
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

      <Card title="The Pi's header">
        {/* Reference, and long on a phone: open on a wide screen, folded on a narrow one. Read once, here, as the first paint. */}
        <details open={wide}>
          <summary className="cursor-pointer text-sm font-medium">Which pin is wired to what</summary>
          <p className="my-3 text-sm text-slate-700 dark:text-slate-300">Each pin takes the colour of the device it is wired to. Pins of the other devices are quieter. A pin used by a connection cannot be offered to another one.</p>
          <HeaderMap connections={w.connections} devices={w.devices} selected={device.serial} focusId={focusId} />
        </details>
      </Card>
    </div>
  );
}

import { PI_HEADER, claimsOf, hueFor, whatPinDoes } from "./model.js";

// The Pi's 40-pin header as it is on the board (pin 1 at the top, odd pins left), coloured by the device that owns each
// pin. A bus pin is shared, so it is marked rather than owned. The selected device's pins are strong and the others are
// quiet, so with several devices you still see whose wire is whose.

function ownersOf(connections) {
  const map = {};
  connections.forEach((c) => {
    const claims = claimsOf(c.spec);
    claims.exclusive.filter((k) => k.startsWith("pin:")).forEach((k) => { map[k.slice(4)] = { conn: c, shared: false }; });
    claims.shared.forEach((k) => { if (!map[k.slice(4)]) map[k.slice(4)] = { conn: c, shared: true }; });
  });
  return map;
}

function Cell({ row, owner, devices, selected, focusId }) {
  const [pin, label, gpio] = row;
  if (gpio === null) {
    return (
      <div className="flex min-h-9 items-center gap-2 px-2 text-xs text-slate-500 dark:text-slate-400">
        <span className="w-5 text-right font-mono">{pin}</span><span>{label}</span>
      </div>
    );
  }
  if (!owner) {
    return (
      <div className="flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 px-2 text-xs dark:border-slate-800">
        <span className="w-5 text-right font-mono">{pin}</span>
        <span className="font-mono">{label}</span>
        <span className="text-slate-500 dark:text-slate-400">free</span>
      </div>
    );
  }
  const hue = hueFor(devices, owner.conn.device);
  const mine = owner.conn.device === selected;
  const focused = owner.conn.id === focusId;
  return (
    <div
      className={`flex min-h-9 items-center gap-2 rounded-lg border px-2 text-xs ${hue.edge} ${hue.tint} ${mine ? "" : "opacity-60"} ${focused ? "ring-2 ring-blue-500 dark:ring-blue-400" : ""}`}
      title={`${label}: ${whatPinDoes(owner.conn)}${owner.shared ? " (shared bus)" : ""}`}
    >
      <span className="w-5 text-right font-mono">{pin}</span>
      <span className="font-mono font-medium">{label}</span>
      <span className={`truncate ${hue.ink}`}>{whatPinDoes(owner.conn)}{owner.shared ? " (shared)" : ""}</span>
    </div>
  );
}

export default function HeaderMap({ connections, devices, selected, focusId }) {
  const owners = ownersOf(connections);
  const left = PI_HEADER.filter((r) => r[0] % 2 === 1);
  const right = PI_HEADER.filter((r) => r[0] % 2 === 0);
  return (
    <div className="grid grid-cols-2 gap-x-2 gap-y-1" role="group" aria-label="Pi header, coloured by device">
      {left.map((l, i) => (
        <div key={l[0]} className="contents">
          <Cell row={l} owner={l[2] !== null ? owners[l[2]] : null} devices={devices} selected={selected} focusId={focusId} />
          <Cell row={right[i]} owner={right[i][2] !== null ? owners[right[i][2]] : null} devices={devices} selected={selected} focusId={focusId} />
        </div>
      ))}
    </div>
  );
}

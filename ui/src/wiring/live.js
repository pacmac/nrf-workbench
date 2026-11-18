// The live source of the Wiring page: wiring.show and wiring.connect|change|disconnect, sent to the paths the service
// listed (docs/specs/wiring.md, "The page is live where the commands are"). It maps the service's answer to the shape the
// page already draws, so the sample and the live page are the same components over two sources.
import { useCallback, useEffect, useState } from "react";
import { readJson, writeJson } from "../api.js";

const EMPTY = { devices: [], adapters: [], probes: [], connections: [], dismissed: [], built: [] };

// A connection as the page thinks of it: the answers to the sheet's questions.
function toSpec(c) {
  const spec = { intent: c.intent, via: `adapter:${c.adapter.serial}`, baud: c.baud, send: c.send ? "yes" : "no" };
  if (c.send) spec.voltage = c.voltage_measured ? "measured" : "unmeasured";
  return spec;
}

export function fromShow(data) {
  return {
    devices: data.devices.map((d) => ({ serial: d.serial, model: (d.model || "device").toUpperCase(), mode: d.mode, labels: {} })),
    adapters: data.adapters.map((a) => ({ id: a.serial || `no-serial-${a.ids}`, serial: a.serial, name: a.name, ids: a.ids, node: a.node || "no node", problem: a.problem, heldBy: a.held_by })),
    probes: [],
    // pinState: what a Pi UART connection's pins are doing, read from the pins (driven, floating, mixed, unknown); null for a USB adapter.
    connections: data.connections.map((c) => ({ id: c.name, device: c.device, spec: toSpec(c), live: c.status, canSend: c.can_send, pinState: c.pin_state ? c.pin_state.state : null })),
    built: Object.entries(data.intents).filter(([, v]) => v === "built").map(([k]) => k),
  };
}

// What to send for a debug log. Only what the sheet asked: nothing is defaulted here.
function connectArgs(device, spec) {
  const send = spec.send === "yes";
  const args = { device, intent: spec.intent, adapter: String(spec.via).replace(/^adapter:/, ""), baud: spec.baud, send };
  if (send) args.voltage_measured = spec.voltage === "measured";
  return args;
}

// The service's own answer, worded for the sheet: the message, and the code that names a refusal.
function outcome(r) {
  const b = r.body;
  if (r.ok && b && b.ok) return { ok: true, message: b.message };
  if (b && b.message) return { ok: false, message: `${b.message}${b.code && b.code !== "usage" ? ` (${b.code})` : ""}` };
  return { ok: false, message: `The request failed (HTTP ${r.status}).` };
}

export function useLiveWiring(commands, refreshKey) {
  const find = (name) => (commands || []).find((c) => c.name === name) || null;
  const show = find("wiring.show");
  const [state, setState] = useState({ ...EMPTY, loaded: false });
  const [error, setError] = useState("");
  const [dismissed, setDismissed] = useState([]);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    if (!show) return undefined;
    let live = true;
    readJson(show.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) { setState({ ...fromShow(r.body.data), dismissed: [], loaded: true }); setError(""); }
      else setError(`HTTP ${r.status}${r.body && r.body.message ? `: ${r.body.message}` : ""}`);
    }).catch((e) => { if (live) setError(String(e && e.message ? e.message : e)); });
    return () => { live = false; };
  }, [show, tick, refreshKey]);

  async function send(name, body) {
    const cmd = find(name);
    if (!cmd) return { ok: false, message: `${name} is not available on this bench.` };
    try {
      const out = outcome(await writeJson(cmd.path, body));
      refresh();
      return out;
    } catch (e) {
      return { ok: false, message: `The request did not complete: ${String(e)}` };
    }
  }

  const actions = {
    connect: (device, spec) => send("wiring.connect", connectArgs(device, spec)),
    change: (id, spec) => {
      const a = connectArgs("", spec);
      const body = { name: id, baud: a.baud, send: a.send };
      if (a.send) body.voltage_measured = a.voltage_measured;
      return send("wiring.change", body);
    },
    disconnect: (id) => send("wiring.disconnect", { name: id }),
    // Float or reclaim a Pi UART connection's pins (docs/specs/line-release.md); absent where the host has no pinctrl.
    release: find("wiring.release") ? (id) => send("wiring.release", { name: id }) : null,
    engage: find("wiring.engage") ? (id) => send("wiring.engage", { name: id }) : null,
    dismiss: (id) => setDismissed((old) => [...old, id]),
    refresh,
  };
  return { live: Boolean(show), state: { ...state, dismissed }, actions, error };
}

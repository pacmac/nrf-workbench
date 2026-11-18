import { useEffect, useState } from "react";
import { readJson } from "./api.js";

// The whole bench in one read (bench.overview). It is read when the page loads, and again whenever `deps` change: a USB change the service
// reported, a link's state, a command that finished. Never on a timer. A refresh keeps what is on screen until the answer comes.
export function useOverview(commands, deps) {
  const row = (commands || []).find((c) => c.name === "bench.overview") || null;
  const [state, setState] = useState({ data: null, error: "", at: null });
  useEffect(() => {
    if (!row) return undefined;
    let live = true;
    readJson(row.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) setState({ data: r.body.data, error: "", at: Date.now() / 1000 });
      else setState((old) => ({ ...old, error: `HTTP ${r.status}` + (r.body && r.body.message ? `: ${r.body.message}` : "") }));
    }).catch((e) => { if (live) setState((old) => ({ ...old, error: String(e && e.message ? e.message : e) })); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [row && row.path, ...deps]);
  return state;
}

// The one sentence at the top of Home, from the counts: what is true of the bench right now, in the bench's words.
export function sentence(data) {
  if (!data) return "";
  const c = data.counts;
  const parts = [];
  if (c.devices === 0) parts.push("Nothing is attached");
  else parts.push(`${c.connected} of ${c.devices} ${c.devices === 1 ? "device" : "devices"} connected`);
  if (c.links > 0) parts.push(`${c.listening} of ${c.links} ${c.links === 1 ? "link" : "links"} listening`);
  if (data.i2c) parts.push(data.i2c.enabled ? (data.i2c.pins_claimed ? "I2C ready" : "I2C on, its pins not claimed") : "I2C off");
  const attention = c.attention + (c.links_attention || 0);
  return parts.join(". ") + "." + (attention > 0 ? ` ${attention} ${attention === 1 ? "needs" : "need"} attention.` : "");
}

export const KIND_TEXT = { nrf: "nRF board", esp32: "ESP32 board", adapter: "USB adapter" };
export const STATE_TEXT = { connected: "connected", absent: "not attached", attention: "needs attention" };

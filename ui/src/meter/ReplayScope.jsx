import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { readJson, writeJson } from "../api.js";
import { clampWindow, isWhole, panWindow, zoomWindow } from "./axes.js";
import { EventPanel, timeText } from "./EventPanel.jsx";
import { chargeText, si } from "./format.js";
import { Drawer, Monitor, Readouts } from "./Monitor.jsx";
import { Button } from "../ui.jsx";
import { Overview } from "./Overview.jsx";
import { ScopeBar } from "./ScopeBar.jsx";
import { TraceChart } from "./TraceChart.jsx";

// A replay (docs/specs/meter-page.md, "Live and replay" and "Events"): a recording from its first reading to its last, on the scope. The Pi answers any stretch of it as an exact envelope
// (meter.replay), so zooming asks again for that stretch and nothing is thinned; inside a wake the recording saved at full rate the answer is the wake's samples. Events are named on it with two
// clicks and kept with the recording.

const POINTS = 1000;                       // a wide chart's pixels: one point a pixel at most
const FLOOR_WAKE_S = 2e-4;                 // inside a saved wake: 20 samples of 10 us, the least a chart of 1000 points can draw from
const MIN_EVENT_S = 1e-5;                  // one sample: the least the Pi keeps as an event
const find = (commands, name) => (commands || []).find((c) => c.name === name) || null;
const round6 = (t) => Number(t.toFixed(6));

// `lead` and `tail` are readouts the page adds before and after the figures, `notes` what it has to say under the scope and `side` the controls in the right column (the Meter page's
// layout, docs/specs/meter-page.md, "Bench layout"). The events are in a drawer: a table with a form and five actions a row does not fit a column.
export function ReplayScope({ commands, recording, volts, onDeleted, lead = [], tail = [], notes, side }) {
  const replay = find(commands, "meter.replay");
  const listEvents = find(commands, "meter.events");
  const setEvent = find(commands, "meter.event_set");
  const delEvent = find(commands, "meter.event_delete");
  const [whole, setWhole] = useState(null);       // the whole recording, as meter.replay answers it
  const [win, setWin] = useState(null);           // {t0, t1} on the recording's clock, or null for all of it
  const [data, setData] = useState(null);         // the stretch shown, as meter.replay answers it
  const [log, setLog] = useState(false);
  const [tool, setTool] = useState("zoom");       // "zoom": drag across the trace; "mark": click where an event starts and where it ends
  const [pending, setPending] = useState(null);   // {t}: the first click of a pair
  const [draft, setDraft] = useState(null);       // {start, end}: a pair waiting for its name
  const [armed, setArmed] = useState(null);       // {event, edge}: the next click moves that edge of an event
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [error, setError] = useState("");
  const [showEvents, setShowEvents] = useState(false);
  const box = useRef(null);

  const bounds = useMemo(() => (whole ? { t0: whole.start_s, t1: whole.duration_s } : null), [whole]);
  const view = win && bounds ? win : bounds;
  const wakes = whole ? whole.wakes : [];
  const csvFloor = whole ? Math.max(1.0, whole.resolution_s * 5) : 1.0;          // five rows: fewer is not a trace
  const insideWake = (w) => wakes.some((k) => w.t0 >= k.t0 - 1e-9 && w.t1 <= k.t1 + 1e-9);
  // A stretch held to the least it can be drawn from: a few samples inside a saved wake, five readings elsewhere.
  const fit = (w) => {
    const a = clampWindow(w, bounds, FLOOR_WAKE_S);
    return insideWake(a) ? a : clampWindow(w, bounds, csvFloor);
  };

  const loadEvents = useCallback(async () => {
    if (!listEvents) return;
    try {
      const r = await readJson(listEvents.path, { recording, ...(volts ? { volts } : {}) });
      if (r.ok && r.body && r.body.data) setEvents(r.body.data.events);
    } catch (e) {
      setProblem(String(e));
    }
  }, [listEvents, recording, volts]);

  useEffect(() => {
    if (!replay) return undefined;
    let live = true;
    setWhole(null); setWin(null); setData(null); setPending(null); setDraft(null); setArmed(null); setError("");
    (async () => {
      try {
        const r = await readJson(replay.path, { recording, points: POINTS });
        if (!live) return;
        if (r.ok && r.body && r.body.data) { setWhole(r.body.data); setData(r.body.data); }
        else setError(r.body && r.body.message ? r.body.message : "The recording could not be read.");
      } catch (e) {
        if (live) setError(String(e));
      }
    })();
    return () => { live = false; };
  }, [replay, recording]);

  useEffect(() => { loadEvents(); }, [loadEvents]);

  useEffect(() => {
    if (!replay || !whole) return undefined;
    if (!win) { setData(whole); return undefined; }
    let live = true;
    (async () => {
      try {
        const r = await readJson(replay.path, { recording, t0: win.t0, t1: win.t1, points: POINTS });
        if (!live) return;
        if (r.ok && r.body && r.body.data) { setData(r.body.data); setError(""); }
        else setError(r.body && r.body.message ? r.body.message : "That stretch could not be read.");
      } catch (e) {
        if (live) setError(String(e));
      }
    })();
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win, whole]);

  if (!replay) return null;
  if (!whole || !data || !view) {
    return (
      <Monitor readouts={<Readouts cells={[...lead, ...tail]} />} side={side}
        scope={error ? <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{error}</p>
          : <p className="flex h-full items-center justify-center rounded-md bg-slate-100 text-[13px] text-slate-600 dark:bg-slate-950 dark:text-slate-400" role="status">Reading the recording…</p>} />
    );
  }

  const unit = whole.unit || "A";
  const isAll = isWhole(view, bounds);
  const set = (w) => setWin(isWhole(w, bounds) ? null : w);
  const zoom = (factor, anchor) => set(fit(zoomWindow(view, bounds, factor, anchor, FLOOR_WAKE_S)));
  const pan = (frac) => set(fit(panWindow(view, bounds, frac, FLOOR_WAKE_S)));
  const atFloor = view.t1 - view.t0 <= (insideWake(view) ? FLOOR_WAKE_S : csvFloor) + 1e-9;
  const spans = events.map((e, i) => ({ key: e.name, name: e.name, t0: e.start_s, t1: e.end_s, tint: i }));
  const clear = () => { setPending(null); setDraft(null); setArmed(null); };

  async function save(name, start, end, was) {
    if (!setEvent) return;
    setBusy(true);
    setProblem("");
    try {
      const r = await writeJson(setEvent.path, { recording, name, start_s: start, end_s: end, ...(was ? { was } : {}) });
      if (!r.ok) setProblem(r.body && r.body.message ? r.body.message : "The event was refused.");
      else clear();
      await loadEvents();
    } catch (e) {
      setProblem(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(name) {
    if (!delEvent) return;
    setBusy(true);
    try {
      const r = await writeJson(delEvent.path, { recording, name });
      if (!r.ok) setProblem(r.body && r.body.message ? r.body.message : "The event could not be deleted.");
      await loadEvents();
    } catch (e) {
      setProblem(String(e));
    } finally {
      setBusy(false);
    }
  }

  function pick(t0) {
    const t = round6(t0);
    if (armed) {
      const e = armed.event;
      const a = armed.edge === "start" ? t : e.start_s;
      const b = armed.edge === "end" ? t : e.end_s;
      const lo = Math.min(a, b);
      const hi = Math.max(a, b);
      if (hi - lo < MIN_EVENT_S) return;
      setArmed(null);
      save(e.name, lo, hi, e.name);
    } else if (!pending) {
      setPending({ t });
    } else {
      const lo = Math.min(pending.t, t);
      const hi = Math.max(pending.t, t);
      if (hi - lo < MIN_EVENT_S) return;
      setPending(null);
      setDraft({ start: lo, end: hi });
    }
  }

  function key(e) {
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
    if (e.key === "ArrowLeft") pan(-0.25);
    else if (e.key === "ArrowRight") pan(0.25);
    else if (e.key === "+" || e.key === "=") zoom(2, 0.5);
    else if (e.key === "-" || e.key === "_") zoom(0.5, 0.5);
    else if (e.key === "0") setWin(null);
    else if (e.key === "Escape") clear();
    else return;
    e.preventDefault();
  }

  const st = data.stats;
  const where = data.source === "wake" ? `Drawn from the samples of wake ${data.wake}, ${si(data.width_s, "s")} a point.`
    : isAll || data.resolution_s > 0 ? `Drawn from the recording's readings, ${si(data.width_s, "s")} a point${data.width_s <= data.resolution_s + 1e-9 ? " (the finest it holds here)" : ""}.` : "";

  const cells = [...lead, { label: "Lowest", value: si(st.lowest, unit) }, { label: "Average", value: si(st.mean, unit) }, { label: "Highest", value: si(st.highest, unit) },
    { label: "Charge used", value: chargeText(st.charge) }, ...tail];
  const eventsOpen = showEvents || Boolean(draft) || Boolean(armed);          // naming an event, or moving an edge, needs its form
  return (
    <>
      <Monitor
        readouts={<Readouts cells={cells} />}
        scope={(
          <div className="h-full rounded-md outline-none" ref={box} tabIndex={-1} onKeyDown={key} aria-label="Replay of the recording">
            <div tabIndex={0} className="h-full rounded-md outline-none focus-visible:ring-2 focus-visible:ring-blue-500" aria-label="Scope trace, focus it to use the keys">
              <TraceChart fill buckets={data.buckets} view={view} unit={unit} log={log} tool={armed ? "mark" : tool} spans={spans} pending={pending}
                onZoom={(a, b) => set(fit({ t0: a, t1: b }))} onPick={pick} onWheel={zoom}
                label={`Current of ${recording}, from ${timeText(view.t0)} to ${timeText(view.t1)}`} />
            </div>
          </div>
        )}
        overview={(
          <Overview buckets={whole.buckets} bounds={bounds} view={view} unit={unit} wakes={wakes} spans={spans}
            onMove={(centre) => { if (isAll) return; const len = view.t1 - view.t0; set(fit({ t0: centre - len / 2, t1: centre + len / 2 })); }} />
        )}
        bar={(
          <ScopeBar view={view} bounds={bounds} whole={isAll} tool={tool} onTool={(t) => { setTool(t); setPending(null); }} canMark={Boolean(setEvent)} log={log} onLog={setLog}
            onZoom={zoom} onPan={pan} onWhole={() => setWin(null)} atFloor={atFloor} clock={(t) => timeText(t)}
            after={<Button tone="quiet" className="min-h-8! px-3! py-1!" aria-expanded={eventsOpen} onClick={() => setShowEvents(!showEvents)}>Events ({events.length})</Button>} />
        )}
        notes={(
          <>
            {error && <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{error}</p>}
            <p className="truncate text-xs text-slate-600 dark:text-slate-400" title={where}>{where}{wakes.length > 0 ? ` The amber ticks are the ${wakes.length} wakes kept at full rate: zoom into one to see every sample.` : ""}</p>
            {notes}
          </>
        )}
        side={side} />
      {eventsOpen && (
        <Drawer title="Events" wide onClose={() => { setShowEvents(false); clear(); }}>
          <EventPanel events={events} draft={draft} armed={armed} busy={busy} problem={problem} unit={unit} volts={volts}
            onSave={save} onCancel={clear} onDelete={remove} onArm={(e, edge) => { setTool("mark"); setPending(null); setDraft(null); setArmed({ event: e, edge }); }}
            onShow={(e) => { const pad = Math.max((e.end_s - e.start_s) * 0.1, FLOOR_WAKE_S); set(fit({ t0: e.start_s - pad, t1: e.end_s + pad })); }} />
        </Drawer>
      )}
    </>
  );
}

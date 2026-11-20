import { useContext } from "react";
import DeviceRow from "./DeviceRow.jsx";
import Meter from "./meter/Meter.jsx";
import { ProfilerCheck } from "./meter/Profiler.jsx";
import { sentence } from "./overview.js";
import { Card, Lamp, NavContext, PageHeader, cx } from "./ui.jsx";
import { BACKGROUND_READS, clock, when } from "./format.js";

// Home answers one question, "is the bench alright?", in a sentence, and then shows the evidence. Every heading and every row is a link to
// the page that has the detail (docs/specs/dashboard-redesign.md, "Home").

function Section({ title, page, count, children }) {
  const nav = useContext(NavContext);
  return (
    <section className="rounded-md border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
      <h3 className="border-b border-slate-200 dark:border-slate-800">
        <button type="button" onClick={() => nav.go(page)} className="flex w-full min-h-11 items-center gap-2 px-4 text-left text-[17px] font-semibold hover:bg-slate-50 dark:hover:bg-slate-800/60" aria-label={`${title}: open the page`}>
          {title}
          {count != null && <span className="text-[13px] font-normal text-slate-600 dark:text-slate-400">{count}</span>}
          <span aria-hidden="true" className="ml-auto text-slate-400 dark:text-slate-500">&rsaquo;</span>
        </button>
      </h3>
      <div className="px-3 py-1">{children}</div>
    </section>
  );
}

// Bluetooth as the kernel has it (docs/specs/pi-uart-move.md): an adapter is registered or it is not. Nothing here says a device is in range.
function BluetoothRow({ bt }) {
  if (!bt) return null;
  const text = bt.present ? (bt.blocked ? `adapter ${bt.adapters.join(", ")}, blocked by rfkill` : `adapter ${bt.adapters.join(", ")}`) : "off, no adapter";
  return (
    <>
      <dt className="text-slate-600 dark:text-slate-400">Bluetooth</dt>
      <dd title={bt.why} className={bt.present && !bt.blocked ? "" : "text-amber-800 dark:text-amber-300"}>{text}</dd>
    </>
  );
}

function Empty({ children }) {
  return <p className="px-1 py-3 text-[13px] text-slate-600 dark:text-slate-400">{children}</p>;
}

// Who holds a pin decides its dot. The same few kinds as the pin map: free, I2C, a serial link, something else in the pin map, and the
// pins that are not GPIO at all (power, ground) or that the bench never offers (the HAT's own).
const DOT = {
  free: "border-2 border-slate-400 dark:border-slate-500",
  i2c: "bg-blue-600 dark:bg-blue-400",
  console: "bg-emerald-600 dark:bg-emerald-400",
  other: "bg-violet-600 dark:bg-violet-400",
  power: "bg-rose-500 dark:bg-rose-400",
  ground: "bg-slate-500 dark:bg-slate-400",
  reserved: "border-2 border-dashed border-slate-400 dark:border-slate-500",
};

function dotKind(p) {
  if (p.kind === "power") return "power";
  if (p.kind === "ground") return "ground";
  if (p.holder) return p.service === "i2c" ? "i2c" : p.service === "console" ? "console" : "other";
  return p.class === "reserved" ? "reserved" : "free";
}

function pinTitle(p) {
  const base = `Pin ${p.pin}, ${p.label}${p.alt ? `, ${p.alt}` : ""}`;
  if (p.holder) return `${base}: ${p.holder} (${p.service})`;
  if (p.kind === "gpio") return `${base}: ${p.class === "reserved" ? "reserved" : "free"}`;
  return base;
}

// The 40-pin header as it is on the board: odd pins on the left, even on the right, pin 1 at the top. Each pin is a dot, a button to the GPIO page.
function Header({ pins }) {
  const nav = useContext(NavContext);
  const rows = [];
  for (let i = 0; i < 40; i += 2) rows.push([pins[i], pins[i + 1]]);
  const cell = (p) => (
    <button key={p.pin} type="button" onClick={() => nav.go("gpio")} title={pinTitle(p)} aria-label={pinTitle(p)}
      className="grid h-6 w-6 place-items-center">
      <span className={cx("block rounded-full", p.kind === "gpio" ? "h-3 w-3" : "h-2 w-2", DOT[dotKind(p)])} />
    </button>
  );
  const legend = [["free", "free"], ["i2c", "I2C"], ["console", "serial link"], ["other", "pin map"], ["power", "power"], ["ground", "ground"]];
  return (
    <div className="space-y-2">
      <div className="flex items-start gap-4">
        <div role="group" aria-label="Pi header, pin 1 at the top" className="grid grid-cols-2 gap-x-2 gap-y-0 rounded-md bg-slate-100 px-2 py-1 dark:bg-slate-800">
          {rows.map(([a, b]) => [cell(a), cell(b)])}
        </div>
        <ul className="space-y-1 pt-1 text-[13px] text-slate-600 dark:text-slate-400" aria-label="Key to the pin colours">
          {legend.map(([k, label]) => (
            <li key={k} className="flex items-center gap-2"><span className={cx("block h-3 w-3 rounded-full", DOT[k])} />{label}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function ago(seconds) {
  if (seconds == null) return null;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${m % 60} min` : `${Math.floor(h / 24)} days`;
}

function ConnectionRow({ c }) {
  const nav = useContext(NavContext);
  const who = c.device_name || c.device;
  const bits = [`${c.baud} baud`, c.rx_only ? "receive only" : "can send"];
  if (c.lines != null) bits.push(`${c.lines} ${c.lines === 1 ? "line" : "lines"}`);
  return (
    <li>
      <button type="button" onClick={() => nav.go("console")} className="flex w-full min-h-14 items-center gap-3 px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60">
        <Lamp state={c.state} size={16} />
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium">{who} <span className="font-normal text-slate-600 dark:text-slate-400">to {c.via}</span></span>
          <span className="block truncate text-[13px] text-slate-600 dark:text-slate-400">{c.state === "attention" || c.state === "absent" ? (c.why || bits.join(", ")) : bits.join(", ")}</span>
        </span>
        <span className="shrink-0 text-[13px] text-slate-600 dark:text-slate-400">{c.state === "listening" ? "listening" : c.state === "absent" ? "not attached" : "needs attention"}</span>
      </button>
    </li>
  );
}

function ProjectLine({ p }) {
  const nav = useContext(NavContext);
  return (
    <li>
      <button type="button" onClick={() => nav.go("projects")} className="flex w-full min-h-14 items-center gap-3 px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60">
        <Lamp state={p.state} size={16} />
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium">{p.name}</span>
          {p.purpose && <span className="block text-[13px] text-slate-600 dark:text-slate-400">{p.purpose}</span>}
        </span>
        <span className="shrink-0 text-[13px] text-slate-600 dark:text-slate-400">{p.word}</span>
      </button>
    </li>
  );
}

function I2cRow({ i2c }) {
  const nav = useContext(NavContext);
  const state = !i2c.enabled ? "absent" : i2c.pins_claimed ? "connected" : "attention";
  const text = !i2c.enabled ? "off" : i2c.pins_claimed ? "ready" : "pins not claimed";
  const bus = i2c.buses.find((b) => b.bus === 1);
  return (
    <li>
      <button type="button" onClick={() => nav.go("gpio")} className="flex w-full min-h-14 items-center gap-3 px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60">
        <Lamp state={state} size={16} />
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium">I2C bus 1</span>
          <span className="block truncate text-[13px] text-slate-600 dark:text-slate-400">{bus && bus.clock_hz ? `${bus.clock_hz / 1000} kHz, header pins 3 and 5` : "header pins 3 and 5"}</span>
        </span>
        <span className="shrink-0 text-[13px] text-slate-600 dark:text-slate-400">{text}</span>
      </button>
    </li>
  );
}

function Recent({ rows }) {
  const nav = useContext(NavContext);
  const last = rows.filter((e) => !BACKGROUND_READS.has(e.payload.command)).slice(-5).reverse();
  if (last.length === 0) return <Empty>Nothing has been run yet.</Empty>;
  const lamp = (cls) => (cls === "ok" ? "connected" : cls === "refused" ? "attention" : "fault");
  return (
    <ol>
      {last.map((e) => (
        <li key={e.cursor}>
          <button type="button" onClick={() => nav.go("activity")} className="flex w-full min-h-11 items-center gap-3 px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60">
            <Lamp state={lamp(e.payload.class)} size={14} />
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[13px]">{e.payload.command}</span>
              <span className="block truncate text-[13px] text-slate-600 dark:text-slate-400">{e.payload.message || e.payload.class}</span>
            </span>
            <span className="shrink-0 text-[13px] text-slate-600 dark:text-slate-400">{e.payload.caller}, {when(e.wall)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

export default function Home({ overview, session, feed, activity, info }) {
  const { data, error, at } = overview;
  const caps = info.capabilities;
  if (!data) {
    return (
      <div className="space-y-4">
        <PageHeader title="Home" topic="start" />
        <p role="status" className="text-[15px] text-slate-600 dark:text-slate-400">{error ? `The bench could not be read: ${error}` : "Reading the bench…"}</p>
      </div>
    );
  }
  const devices = data.devices;
  const attached = devices.filter((d) => d.state !== "absent");
  const absent = devices.filter((d) => d.state === "absent");
  const att = data.counts.attention + (data.counts.links_attention || 0);
  const host = data.host;
  return (
    <div className="space-y-4">
      <PageHeader title="Home" topic="start" aside={at ? <span className="text-[13px] text-slate-600 dark:text-slate-400">as of {clock(at)}</span> : null} />
      <p role="status" className={cx("text-2xl font-semibold leading-snug", att > 0 && "text-amber-800 dark:text-amber-300")}>{sentence(data)}</p>
      {feed.state !== "live" && (
        <p role="note" className="rounded-md bg-amber-100 px-3 py-2 text-[13px] text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          Live updates are not arriving ({feed.state}), so this may be out of date.
        </p>
      )}
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2 md:items-start">
        <div className="min-w-0 space-y-4">
          <Section title="Devices" page="devices" count={devices.length}>
            {devices.length === 0 ? <Empty>Nothing is attached or known. Plug a board in.</Empty> : (
              <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="Devices">
                {attached.map((d) => <DeviceRow key={d.id} d={d} />)}
                {absent.map((d) => <DeviceRow key={d.id} d={d} />)}
              </ul>
            )}
          </Section>
          {data.projects && data.projects.length > 0 && (
            <Section title="Projects" page="projects" count={data.projects.length}>
              <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="Projects">
                {data.projects.map((p) => <ProjectLine key={p.name} p={p} />)}
              </ul>
            </Section>
          )}
          <Section title="Connections" page="wiring" count={(data.connections || []).length}>
            <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="Connections">
              {(data.connections || []).map((c) => <ConnectionRow key={c.name} c={c} />)}
              {data.i2c && <I2cRow i2c={data.i2c} />}
            </ul>
            {(data.connections || []).length === 0 && !data.i2c && <Empty>Nothing is wired yet.</Empty>}
            {(data.connections || []).length === 0 && data.i2c && <p className="px-1 pb-2 text-[13px] text-slate-600 dark:text-slate-400">No debug log is wired yet.</p>}
          </Section>
          <Section title="Recent activity" page="activity">
            <Recent rows={activity.rows} />
          </Section>
        </div>
        <div className="min-w-0 space-y-4">
          <Meter commands={info.commands} />
          <ProfilerCheck commands={info.commands} />
          {data.header && (
            <Section title="The Pi" page="gpio">
              <div className="space-y-3 py-2">
                <Header pins={data.header} />
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
                  <dt className="text-slate-600 dark:text-slate-400">Model</dt><dd>{host.model || "unknown"}</dd>
                  <dt className="text-slate-600 dark:text-slate-400">Address</dt><dd className="break-all font-mono">{window.location.host}</dd>
                  {host.uptime_s != null && (<><dt className="text-slate-600 dark:text-slate-400">Up</dt><dd>{ago(host.uptime_s)}</dd></>)}
                  <BluetoothRow bt={data.bluetooth} />
                  <dt className="text-slate-600 dark:text-slate-400">Access</dt>
                  <dd>{session.access === "open" ? "open, no token" : session.access === "open-read" ? "reads open, writes need the token" : "token"}</dd>
                </dl>
              </div>
            </Section>
          )}
          {!data.header && (
            <Section title="This host" page="commands">
              <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 py-2 text-[13px]">
                <dt className="text-slate-600 dark:text-slate-400">Address</dt><dd className="break-all font-mono">{window.location.host}</dd>
                {host.uptime_s != null && (<><dt className="text-slate-600 dark:text-slate-400">Up</dt><dd>{ago(host.uptime_s)}</dd></>)}
              </dl>
            </Section>
          )}
        </div>
      </div>
      {caps && caps.absent.length > 0 && (
        <details className="text-[13px] text-slate-600 dark:text-slate-400">
          <summary className="min-h-8 cursor-pointer py-1">{caps.absent.length} commands are not available on this host</summary>
          <ul className="mt-1 space-y-1">
            {caps.absent.map((a) => <li key={a.name}><span className="font-mono">{a.name}</span> needs {a.unmet.join(", ")}</li>)}
          </ul>
        </details>
      )}
    </div>
  );
}

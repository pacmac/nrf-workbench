import { useCallback, useEffect, useState } from "react";
import { readJson, writeFile, writeJson } from "./api.js";
import { PROJECT_NAME } from "./format.js";
import { Button, Card, Chip, Field, Lamp, PageHeader, SlugNote, cx, inputClass } from "./ui.jsx";
import { ResultView } from "./Commands.jsx";

// Projects (docs/specs/projects.md). A project is a written-down bench setup. The page lists them, opens one to show its roles, what it
// uses and where the bench differs, and offers Plan, Apply, Check, Release, Probe and Flash as buttons that show the whole Result. Apply is
// offered only after a plan that has nothing blocking. Every action is a command the service listed, sent to the path it listed.

const ACTION_TONE = { create: "blue", use: "slate", have: "slate", conflict: "red", unresolved: "amber", unmet: "red" };
const TEMPLATE = (name) => `name: ${name}\npurpose: ""\ndevices: {}\n`;           // valid as it stands: a new project can be saved at once

function ProjectRow({ p, open, onOpen }) {
  return (
    <li>
      <button type="button" onClick={onOpen} aria-expanded={open}
        className={cx("flex w-full min-h-14 items-center gap-3 px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60", open && "bg-slate-50 dark:bg-slate-800/60")}>
        <Lamp state={p.state} size={16} />
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-medium">{p.name}</span>
          <span className="block text-[13px] text-slate-600 dark:text-slate-400">{p.purpose || (p.manifest ? "no purpose written" : "a firmware folder, no manifest yet")}</span>
        </span>
        <span className="shrink-0 text-[13px] text-slate-600 dark:text-slate-400">{p.word || "no manifest"}</span>
      </button>
    </li>
  );
}

function PlanView({ plan }) {
  return (
    <div className="space-y-2" aria-label="Plan">
      <p className="text-[13px] text-slate-600 dark:text-slate-400">
        {plan.ok ? "Nothing blocks this." : `${plan.blocking.length} ${plan.blocking.length === 1 ? "thing blocks" : "things block"} it.`}{" "}
        {Object.entries(plan.counts).map(([a, n]) => `${n} ${a}`).join(", ")}
      </p>
      <ul className="divide-y divide-slate-200 dark:divide-slate-800">
        {plan.items.map((i) => (
          <li key={`${i.section}-${i.name}`} className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2">
            <Chip tone={ACTION_TONE[i.action] || "slate"}>{i.action}</Chip>
            <span className="min-w-0 flex-1 text-[15px]"><span className="font-medium">{i.name}</span> <span className="text-[13px] text-slate-600 dark:text-slate-400">{i.section}</span>
              {i.why && <span className="block text-[13px] text-slate-600 dark:text-slate-400">{i.why}</span>}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Role({ r, project, flash, onFlashRole, applied }) {
  const [open, setOpen] = useState(false);
  const [bound, setBound] = useState("");
  const [enter, setEnter] = useState("");
  const canFlash = flash && applied && r.firmware && r.firmware.id;
  const ready = Number(bound) > 0 && (r.kind !== "nrf" || Number(enter) > 0);
  return (
    <li className="space-y-2 py-2">
      <div className="flex items-start gap-3">
        <Lamp state={r.board.state} size={16} className="mt-1" />
        <div className="min-w-0 flex-1">
          <p className="text-[15px]"><span className="font-medium">{r.role}</span> <span className="text-[13px] text-slate-600 dark:text-slate-400">{r.kind === "nrf" ? "nRF board" : "ESP32 board"} {r.board.key}</span></p>
          <p className="text-[13px] text-slate-600 dark:text-slate-400">
            {r.board.state === "absent" ? (r.board.why || "not attached") : "attached"}
            {r.console && r.console.link ? `, console ${r.console.link} ${r.console.state || "not open"}` : ""}
            {r.firmware ? `, firmware ${r.firmware.id}${r.firmware.in_store ? "" : " (not in the store)"}` : ""}
          </p>
        </div>
        {canFlash && <Button tone="quiet" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`Flash ${r.role}`}>Flash…</Button>}
      </div>
      {open && (
        <form className="grid gap-3 rounded-md bg-slate-50 p-3 dark:bg-slate-950/40 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); onFlashRole(r, Number(bound), r.kind === "nrf" ? Number(enter) : null); setOpen(false); }}>
          <Field label="Give up after (seconds)"><input className={inputClass} type="number" min="0" step="any" value={bound} onChange={(e) => setBound(e.target.value)} aria-label={`Flash bound for ${r.role}`} /></Field>
          {r.kind === "nrf" && <Field label="Bootloader within (seconds)"><input className={inputClass} type="number" min="0" step="any" value={enter} onChange={(e) => setEnter(e.target.value)} aria-label={`Enter bound for ${r.role}`} /></Field>}
          <div className="flex gap-2 sm:col-span-2"><Button type="submit" tone="danger" disabled={!ready}>{`Erase and flash ${r.role}`}</Button><Button tone="quiet" onClick={() => setOpen(false)}>Cancel</Button></div>
        </form>
      )}
    </li>
  );
}

function Detail({ name, cmd, onChanged, onDone, onGone }) {
  const [show, setShow] = useState(null);
  const [plan, setPlan] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [asking, setAsking] = useState(null);                 // "release" or "delete" being confirmed
  const find = (n) => cmd[n] || null;

  const load = useCallback(async () => {
    const r = await readJson(find("project.show").path, { project: name });
    setShow(r.ok && r.body && r.body.data ? r.body.data : { error: r.body && r.body.message ? r.body.message : `HTTP ${r.status}` });
    return r;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);
  useEffect(() => { setPlan(null); setOutcome(null); setEditing(false); setAsking(null); load().catch(() => {}); }, [name, load]);

  async function run(request, after) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok && after) { after(r); return r; }               // a delete: the project is gone, so it is not read again
      if (r.ok) { await load(); if (onChanged) onChanged(); }
      return r;
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
      return null;
    }
  }
  const act = (n, extra) => run(writeJson(find(n).path, { project: name, ...(extra || {}) }));

  async function doPlan() {
    setOutcome({ pending: true });
    const r = await readJson(find("project.plan").path, { project: name });
    setOutcome(r.ok ? null : r);
    setPlan(r.ok && r.body ? r.body.data : null);
  }
  async function doCheck() {
    setOutcome({ pending: true });
    setOutcome(await readJson(find("project.check").path, { project: name }));
  }
  async function doMeasure() {                      // a read: one reading of the meter against expect.meter, nothing changes
    setOutcome({ pending: true });
    setOutcome(await readJson(find("project.measure").path, { project: name }));
  }
  async function save(event) {
    event.preventDefault();
    const r = await run(writeFile(find("project.put").path, { project: name }, new Blob([text], { type: "text/yaml" })));
    if (r && r.ok) { setEditing(false); setPlan(null); }
  }

  if (!show) return <p role="status" className="px-4 py-3 text-[13px] text-slate-600 dark:text-slate-400">Reading {name}…</p>;
  if (show.error) return <p role="alert" className="px-4 py-3 text-[13px] text-red-800 dark:text-red-300">{show.error}</p>;
  const applied = Boolean(show.applied);
  const busy = Boolean(outcome && outcome.pending);
  return (
    <div className="space-y-4 border-t border-slate-200 px-4 py-4 dark:border-slate-800" aria-label={`Project ${name}`}>
      {!show.manifest ? (
        <p className="text-[13px] text-slate-600 dark:text-slate-400">{show.note}</p>
      ) : (
        <>
          {show.notes && <p className="max-w-[65ch] whitespace-pre-wrap text-[13px] text-slate-600 dark:text-slate-400">{show.notes}</p>}
          {show.requires.length > 0 && (
            <ul className="space-y-1" aria-label="Requirements">
              {show.requires.map((q) => <li key={q.item} className="flex items-center gap-2 text-[13px]"><Lamp state={q.met ? "connected" : "attention"} size={12} /><span className="font-medium">{q.item}</span><span className="text-slate-600 dark:text-slate-400">{q.met ? "met" : q.how}</span></li>)}
            </ul>
          )}
          {show.roles.length > 0 && (
            <ul className="divide-y divide-slate-200 dark:divide-slate-800" aria-label="Roles">
              {show.roles.map((r) => (
                <Role key={r.role} r={r} project={name} applied={applied} flash={find("project.flash")}
                  onFlashRole={(role, bound, enter) => run(writeJson(find("project.flash").path, { project: name, role: role.role, flash_bound_s: bound, ...(enter != null ? { enter_bound_s: enter } : {}) }))} />
              ))}
            </ul>
          )}
          <p className="text-[13px] text-slate-600 dark:text-slate-400">
            Uses {show.resources.connections.length} {show.resources.connections.length === 1 ? "connection" : "connections"}, {show.resources.pins.length} {show.resources.pins.length === 1 ? "pin" : "pins"},
            {" "}{show.resources.macros.length} {show.resources.macros.length === 1 ? "macro" : "macros"}.
          </p>
          {show.drift.length > 0 && (
            <ul className="space-y-1" aria-label="Where the bench differs">
              {show.drift.map((d) => <li key={`${d.section}-${d.name}`} className="flex items-start gap-2 text-[13px]"><Lamp state="attention" size={12} className="mt-0.5" /><span><span className="font-medium">{d.name}</span> {d.status}: {d.why}</span></li>)}
            </ul>
          )}
        </>
      )}

      <div className="flex flex-wrap gap-2">
        {show.manifest && find("project.plan") && <Button tone="quiet" onClick={doPlan} disabled={busy}>Plan</Button>}
        {show.manifest && find("project.apply") && plan && plan.ok && <Button onClick={() => act("project.apply", null).then(() => setPlan(null))} disabled={busy}>Apply</Button>}
        {show.manifest && find("project.check") && <Button tone="quiet" onClick={doCheck} disabled={busy}>Check</Button>}
        {show.manifest && find("project.probe") && show.resources.i2c && <Button tone="quiet" onClick={() => act("project.probe")} disabled={busy}>Probe I2C</Button>}
        {show.manifest && find("project.measure") && show.resources.meter && <Button tone="quiet" onClick={doMeasure} disabled={busy}>Measure</Button>}
        {show.manifest && applied && find("project.release") && asking !== "release" && <Button tone="quiet" onClick={() => setAsking("release")} disabled={busy}>Release</Button>}
        {find("project.put") && <Button tone="quiet" onClick={() => { setText(show.text || TEMPLATE(name)); setEditing(!editing); }} aria-expanded={editing}>{show.manifest ? "Edit manifest" : "Write manifest"}</Button>}
        {show.manifest && !applied && find("project.delete") && asking !== "delete" && <Button tone="quiet" onClick={() => setAsking("delete")} disabled={busy}>Delete</Button>}
      </div>
      {asking && (
        <p className="flex flex-wrap items-center gap-2 text-[15px]" role="alert">
          {asking === "release" ? "Release? It removes what this project made." : "Delete the manifest? The firmware folder stays."}
          <Button tone="danger" onClick={() => { const a = asking; setAsking(null); if (a === "release") act("project.release"); else run(writeJson(find("project.delete").path, { project: name }), onGone); }}>Yes</Button>
          <Button tone="quiet" onClick={() => setAsking(null)}>No</Button>
        </p>
      )}
      {plan && <PlanView plan={plan} />}
      {editing && (
        <form onSubmit={save} className="space-y-2">
          <Field label="Manifest"><textarea className={inputClass + " h-72 font-mono! text-xs!"} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} aria-label="Manifest" /></Field>
          <div className="flex gap-2"><Button type="submit" disabled={busy || !text.trim()}>Save manifest</Button><Button tone="quiet" onClick={() => setEditing(false)}>Cancel</Button></div>
        </form>
      )}
      {outcome && <ResultView outcome={outcome} pendingText="Working…" />}
    </div>
  );
}

export default function Projects({ commands, refreshKey, onChanged, onDone }) {
  const cmd = {};
  (commands || []).forEach((c) => { if (c.name.startsWith("project.")) cmd[c.name] = c; });
  const list = cmd["project.list"] || null;
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);
  const [tick, setTick] = useState(0);
  const [name, setName] = useState("");
  const [outcome, setOutcome] = useState(null);

  useEffect(() => {
    if (!list) return undefined;
    let live = true;
    readJson(list.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) { setProjects(r.body.data.projects || []); setError(""); }
      else setError(`HTTP ${r.status}` + (r.body && r.body.message ? `: ${r.body.message}` : ""));
    }).catch((e) => { if (live) setError(String(e && e.message ? e.message : e)); });
    return () => { live = false; };
  }, [list, tick, refreshKey]);

  const changed = () => { setTick((n) => n + 1); if (onChanged) onChanged(); };
  async function create(event) {
    event.preventDefault();
    const n = name.trim();
    if (!PROJECT_NAME.test(n) || !cmd["project.put"]) return;
    setOutcome({ pending: true });
    const r = await writeFile(cmd["project.put"].path, { project: n }, new Blob([TEMPLATE(n)], { type: "text/yaml" }));
    setOutcome(r.ok ? null : r);
    if (onDone) onDone(r);
    if (r.ok) { setName(""); setOpen(n); changed(); }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Projects" topic="projects" />
      {error && <p role="alert" className="text-[13px] text-red-800 dark:text-red-300">Projects could not be read ({error}).</p>}
      <Card className="p-0!">
        {projects === null ? (
          <p className="px-4 py-4 text-[13px] text-slate-600 dark:text-slate-400">Reading the projects…</p>
        ) : projects.length === 0 ? (
          <p className="px-4 py-4 text-[13px] text-slate-600 dark:text-slate-400">No project yet.</p>
        ) : (
          <ul className="divide-y divide-slate-200 px-3 dark:divide-slate-800" aria-label="Projects">
            {projects.map((p) => (
              <li key={p.name} className="list-none">
                <ul><ProjectRow p={p} open={open === p.name} onOpen={() => setOpen(open === p.name ? null : p.name)} /></ul>
                {open === p.name && <Detail name={p.name} cmd={cmd} onChanged={changed} onDone={onDone} onGone={() => { setOpen(null); changed(); }} />}
              </li>
            ))}
          </ul>
        )}
      </Card>
      {cmd["project.put"] && (
        <form onSubmit={create} className="flex flex-wrap items-end gap-3">
          <div className="min-w-48 flex-1">
            <Field label="New project">
              <input className={inputClass} type="text" value={name} onChange={(e) => setName(e.target.value.toLowerCase())} aria-label="New project name" autoComplete="off" spellCheck="false" />
            </Field>
            <SlugNote value={name} />
          </div>
          <Button type="submit" disabled={!PROJECT_NAME.test(name.trim())}>Create project</Button>
        </form>
      )}
      {outcome && <ResultView outcome={outcome} pendingText="Working…" />}
    </div>
  );
}

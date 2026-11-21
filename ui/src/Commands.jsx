import { useEffect, useRef, useState } from "react";
import { readJson, writeFile, writeJson } from "./api.js";
import { Button, Card, Chip, ClassBadge, Field, FitList, Json, PageHeader, cx, inputClass, useWide } from "./ui.jsx";

// The Commands page is GENERATED from GET api/commands: one form per registry row, nothing written per
// command. A new command appears here the moment the service registers it. Reads go to the row's path with a
// query; a write goes to the row's path as JSON, or, for a raw-body command, the FILE is the body and the other
// arguments are the query. Every write URL is therefore one the server declared (tests/dashboard/test_ui_rules.py).

function initial(command, preset) {
  const values = {};
  command.args.forEach((a) => {
    values[a.name] = preset && preset[a.name] !== undefined ? preset[a.name] : a.type === "bool" ? Boolean(a.default) : "";
  });
  return values;
}

// Only what the person filled in is sent; an empty optional is "not given" (the registry's own rule).
function collect(command, values) {
  const sent = {};
  command.args.forEach((a) => {
    const v = values[a.name];
    if (a.type === "bool") {
      // A required boolean is always sent: an unchecked box is the answer "false", and leaving it out would read as "missing".
      if (a.required || v !== Boolean(a.default)) sent[a.name] = v;
    } else if (v !== "" && v !== undefined) {
      sent[a.name] = a.type === "int" || a.type === "float" ? Number(v) : v;
    }
  });
  return sent;
}

// An argument the bench can list gets quick picks under the box, one press to fill it. The typed value is still allowed, so a serial that is
// not attached can still be sent and the service says why it refuses.
function ArgInput({ arg, value, onChange, options }) {
  const label = arg.name + (arg.unit ? ` (${arg.unit})` : "") + (arg.required ? " *" : "");
  const hint = arg.doc + (!arg.required && arg.default !== null && arg.default !== undefined ? ` Default: ${arg.default}.` : "");
  const picks = options && options.length ? options.slice(0, 12) : null;
  if (arg.type === "bool") {
    return (
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        <span><span className="font-medium">{arg.name}</span><span className="block text-xs text-slate-600 dark:text-slate-400">{hint}</span></span>
      </label>
    );
  }
  return (
    <Field label={label} hint={hint}>
      <input
        className={inputClass}
        type={arg.type === "int" || arg.type === "float" ? "number" : "text"}
        step={arg.type === "float" ? "any" : undefined}
        required={arg.required}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
      {picks && (
        <span className="mt-1.5 flex flex-wrap gap-1.5" role="group" aria-label={`Choose ${arg.name}`}>
          {picks.map((o) => (
            <button key={o.value} type="button" onClick={(e) => { e.preventDefault(); onChange(o.value); }} aria-pressed={value === o.value} title={o.value}
              className={cx("min-h-8 rounded-md px-2 text-[13px] font-medium", value === o.value
                ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                : "bg-slate-100 text-slate-800 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700")}>{o.label}</button>
          ))}
        </span>
      )}
    </Field>
  );
}

// The WHOLE Result is shown: class, code, message, then every byte of data and evidence.
export function ResultView({ outcome, pendingText }) {
  if (!outcome) return null;
  if (outcome.pending) return <p role="status" className="text-sm text-slate-600 dark:text-slate-400">{pendingText || "Running… a flash takes about 20 seconds; the service answers when it is done."}</p>;
  const r = outcome.body;
  if (!r || typeof r !== "object" || !("class" in r)) {
    return (
      <div role="status" className="space-y-2">
        <div className="flex items-center gap-2"><ClassBadge cls={outcome.status === 401 ? "refused" : "error"} /><span className="text-sm">HTTP {outcome.status}</span></div>
        {r && r.message && <p className="text-sm">{r.message}</p>}
        {r && <Json value={r} />}
      </div>
    );
  }
  return (
    <div role="status" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <ClassBadge cls={r.class} />
        {r.code !== "ok" && <Chip>{r.code}</Chip>}
        <span className="text-xs text-slate-600 dark:text-slate-400">HTTP {outcome.status}</span>
      </div>
      {r.message && <p className="text-sm">{r.message}</p>}
      {r.data != null && <Json value={r.data} />}
      {r.evidence != null && Object.keys(r.evidence).length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-slate-600 dark:text-slate-400">Evidence</p>
          <Json value={r.evidence} />
        </div>
      )}
    </div>
  );
}

export function CommandForm({ command, preset, onDone, suggest }) {
  const [values, setValues] = useState(() => initial(command, preset));
  const [file, setFile] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const raw = command.body === "raw";
  const write = command.effect !== "read";

  async function run(event) {
    event.preventDefault();
    setOutcome({ pending: true });
    const sent = collect(command, values);
    try {
      let r;
      if (!write) r = await readJson(command.path, sent);
      else if (raw) r = await writeFile(command.path, sent, file);
      else r = await writeJson(command.path, sent);
      setOutcome(r);
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  return (
    <form onSubmit={run} className="space-y-3">
      <p className="text-sm">{command.summary}</p>
      {command.args.map((a) => (
        <ArgInput key={a.name} arg={a} value={values[a.name]} onChange={(v) => setValues({ ...values, [a.name]: v })} options={suggest ? suggest(command, a) : null} />
      ))}
      {raw && (
        <Field label="File *" hint="The file itself is the request body; it is streamed, not copied through the page.">
          <input className={inputClass} type="file" required onChange={(e) => setFile(e.target.files[0] || null)} />
        </Field>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" tone={write ? "primary" : "quiet"} disabled={outcome && outcome.pending}>
          {write ? "Run" : "Read"}
        </Button>
        <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{write ? "POST" : "GET"} {command.path}</span>
        {command.result && <span className="text-xs text-slate-600 dark:text-slate-400">returns: {command.result}</span>}
      </div>
      <ResultView outcome={outcome} />
    </form>
  );
}

// Which values the bench can offer for an argument, from what it already knows (bench.overview) and, for a stored package, from
// firmware.list. [] or null means a plain text box.
function pickers(overview, packages) {
  const data = overview && overview.data;
  const devs = data ? data.devices : [];
  const opt = (value, label) => ({ value, label: label && label !== value ? label : value });
  const nrf = devs.filter((d) => d.kind === "nrf");
  const esp = devs.filter((d) => d.kind === "esp32" && !d.undeclared);
  const conns = data && data.connections ? data.connections : [];
  const adapters = devs.filter((d) => d.kind === "adapter" && d.serial);
  return (command, arg) => {
    const area = command.name.split(".")[0];
    switch (arg.name) {
      case "serial":
        return area === "esp32" ? null : nrf.filter((d) => d.state !== "absent").map((d) => opt(d.serial, d.label));
      case "device":
        return nrf.map((d) => opt(d.serial, d.label));
      case "target":
        return esp.map((d) => opt(d.name, d.product));
      case "link":
        return [...conns.map((c) => opt(c.name, c.device_name || c.device)), ...esp.map((d) => opt(d.name, d.product))];
      case "adapter":
        return adapters.map((d) => opt(d.serial, d.label));
      case "name":
        if (area === "esp32") return esp.map((d) => opt(d.name, d.product));
        if (area === "wiring") return conns.map((c) => opt(c.name, c.device_name || c.device));
        if (area === "devices") return nrf.map((d) => opt(d.name || "", d.label)).filter((o) => o.value);
        return null;
      case "firmware":
      case "id":
        return area === "firmware" || area === "programmer" || area === "esp32" ? packages.map((p) => opt(p.sha256, `${p.name || "unnamed"} (${p.project})`)) : null;
      case "project":
        return area === "firmware" || area === "programmer" ? [...new Set(packages.map((p) => p.project))].map((p) => opt(p, p)) : null;
      default:
        return null;
    }
  };
}

// The command list on the left, the form on the right. Search narrows the list; the form is generated from the registry row.
export default function Commands({ commands, overview, onDone }) {
  const wide = useWide();
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [area, setArea] = useState("all");                  // the wide page's area choice: the part of a command's name before the dot
  const [chosen, setChosen] = useState(null);
  const [packages, setPackages] = useState([]);
  const form = useRef(null);
  const list = (commands || []).find((c) => c.name === "firmware.list") || null;

  // On a phone the form is below the list, so choosing a command brings it into view (on a wide screen it is already beside the list).
  useEffect(() => {
    if (chosen && form.current && form.current.scrollIntoView && window.matchMedia("(max-width: 767px)").matches) form.current.scrollIntoView({ block: "start" });
  }, [chosen]);

  useEffect(() => {
    if (!list || !chosen) return undefined;
    let live = true;
    readJson(list.path).then((r) => { if (live && r.ok && r.body && r.body.data) setPackages(r.body.data.items || []); }).catch(() => {});
    return () => { live = false; };
  }, [list, chosen]);

  if (commands === null) return <Card title="Commands"><p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p></Card>;
  const q = query.trim().toLowerCase();
  const shown = commands.filter((c) => (kind === "all" || (kind === "read") === (c.effect === "read")) && (area === "all" || c.name.split(".")[0] === area)
    && (!q || c.name.includes(q) || c.summary.toLowerCase().includes(q)));
  const areas = {};
  shown.forEach((c) => { (areas[c.name.split(".")[0]] = areas[c.name.split(".")[0]] || []).push(c); });
  const current = commands.find((c) => c.name === chosen) || null;
  const choose = (c) => setChosen(c.name);
  const suggest = pickers(overview, packages);
  const filter = (
    <div role="group" aria-label="Show" className="flex gap-1">
      {[["all", "All"], ["read", "Reads"], ["write", "Changes"]].map(([id, label]) => (
        <button key={id} type="button" aria-pressed={kind === id} onClick={() => setKind(id)}
          className={cx("min-h-9 flex-1 rounded-md px-2 text-[13px] font-medium", kind === id
            ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
            : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700")}>{label}</button>
      ))}
    </div>
  );
  // The wide page (docs/specs/navigation.md, "Full-height pages", Commands): the commands as a paged list of one-line rows, and the form in a panel that may scroll inside itself.
  if (wide) {
    const areaNames = [...new Set(commands.map((c) => c.name.split(".")[0]))].sort();
    return (
      <div className="flex min-h-0 flex-1 flex-col gap-2">
        <PageHeader title="Commands" topic="commands" aside={<span className="text-[13px] text-slate-600 dark:text-slate-400">{commands.length} on this host</span>} />
        <div className="grid min-h-0 flex-1 grid-cols-[19rem_minmax(0,1fr)] gap-3" style={{ gridTemplateRows: "minmax(0, 1fr)" }}>
          <Card className="flex min-h-0 flex-col gap-2">
            <input className={inputClass} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search commands" aria-label="Search commands" autoComplete="off" />
            {filter}
            <select aria-label="Area" className={inputClass} value={area} onChange={(e) => setArea(e.target.value)}>
              <option value="all">All areas</option>
              {areaNames.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <FitList label="Commands" rowH={36} items={shown} keyOf={(c) => c.name} empty="No command matches."
              render={(c) => (
                <button type="button" onClick={() => choose(c)} aria-current={chosen === c.name ? "true" : undefined}
                  className={cx("flex h-full w-full items-center gap-2 rounded-md px-2 text-left", chosen === c.name ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800")}>
                  <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{c.name}</span>
                  <span className="shrink-0 text-[12px] opacity-70">{c.effect === "read" ? "read" : "changes"}</span>
                </button>
              )} />
          </Card>
          <Card className="flex min-h-0 flex-col" title={current ? current.name : undefined} aside={current ? <Chip tone={current.effect === "read" ? "slate" : "amber"}>{current.effect === "read" ? "reads only" : "changes things"}</Chip> : undefined}>
            <div data-testid="work" className="min-h-0 flex-1 overflow-y-auto">
              {current ? <CommandForm key={current.name} command={current} onDone={onDone} suggest={suggest} /> : <p className="text-[15px] text-slate-600 dark:text-slate-400">Choose a command.</p>}
            </div>
          </Card>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <PageHeader title="Commands" topic="commands" aside={<span className="text-[13px] text-slate-600 dark:text-slate-400">{commands.length} on this host</span>} />
      <div className="grid gap-4 md:grid-cols-[19rem_minmax(0,1fr)] md:items-start">
        <Card className="space-y-3 md:sticky md:top-36">
          <input className={inputClass} type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search commands" aria-label="Search commands" autoComplete="off" />
          <div role="group" aria-label="Show" className="flex gap-1">
            {[["all", "All"], ["read", "Reads"], ["write", "Changes"]].map(([id, label]) => (
              <button key={id} type="button" aria-pressed={kind === id} onClick={() => setKind(id)}
                className={cx("min-h-9 flex-1 rounded-md px-2 text-[13px] font-medium", kind === id
                  ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900"
                  : "bg-slate-100 text-slate-700 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700")}>{label}</button>
            ))}
          </div>
          <div className="max-h-[60vh] space-y-3 overflow-y-auto" aria-label="Commands">
            {Object.keys(areas).length === 0 && <p className="text-[13px] text-slate-600 dark:text-slate-400">No command matches.</p>}
            {Object.keys(areas).sort().map((area) => (
              <div key={area}>
                <h3 className="px-1 pb-1 text-[13px] font-semibold text-slate-600 dark:text-slate-400">{area}</h3>
                <ul>
                  {areas[area].map((c) => (
                    <li key={c.name}>
                      <button type="button" onClick={() => choose(c)} aria-current={chosen === c.name ? "true" : undefined}
                        className={cx("flex w-full min-h-10 items-center gap-2 rounded-md px-2 text-left", chosen === c.name
                          ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800")}>
                        <span className="min-w-0 flex-1 truncate font-mono text-[13px]">{c.name.slice(area.length + 1)}</span>
                        <span className="shrink-0 text-[12px] opacity-70">{c.effect === "read" ? "read" : "changes"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
        <div ref={form}>
          {current ? (
            <Card title={current.name} aside={<Chip tone={current.effect === "read" ? "slate" : "amber"}>{current.effect === "read" ? "reads only" : "changes things"}</Chip>}>
              <CommandForm key={current.name} command={current} onDone={onDone} suggest={suggest} />
            </Card>
          ) : (
            <Card><p className="text-[15px] text-slate-600 dark:text-slate-400">Choose a command.</p></Card>
          )}
        </div>
      </div>
    </div>
  );
}

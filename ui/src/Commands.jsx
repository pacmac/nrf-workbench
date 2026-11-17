import { useState } from "react";
import { readJson, writeFile, writeJson } from "./api.js";
import { Button, Card, Chip, ClassBadge, Field, Json, inputClass } from "./ui.jsx";

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

function ArgInput({ arg, value, onChange }) {
  const label = arg.name + (arg.unit ? ` (${arg.unit})` : "") + (arg.required ? " *" : "");
  const hint = arg.doc + (!arg.required && arg.default !== null && arg.default !== undefined ? ` Default: ${arg.default}.` : "");
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
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-600 dark:text-slate-400">Evidence</p>
          <Json value={r.evidence} />
        </div>
      )}
    </div>
  );
}

export function CommandForm({ command, preset, onDone }) {
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
        <ArgInput key={a.name} arg={a} value={values[a.name]} onChange={(v) => setValues({ ...values, [a.name]: v })} />
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

export default function Commands({ commands, onDone }) {
  const [open, setOpen] = useState(null);
  if (commands === null) return <Card title="Commands"><p className="text-sm text-slate-600 dark:text-slate-400">Loading…</p></Card>;
  const areas = {};
  commands.forEach((c) => {
    const area = c.name.split(".")[0];
    (areas[area] = areas[area] || []).push(c);
  });
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-600 dark:text-slate-400">
        Every command this service has on this host, generated from its registry. They run through the same API, the same guards and the
        same activity log as the command line and the AI tools.
      </p>
      {Object.keys(areas).sort().map((area) => (
        <Card key={area} title={area}>
          <ul className="divide-y divide-slate-200 dark:divide-slate-800">
            {areas[area].map((c) => (
              <li key={c.name} className="py-2">
                <button
                  className="flex w-full flex-wrap items-center gap-2 text-left"
                  aria-expanded={open === c.name}
                  onClick={() => setOpen(open === c.name ? null : c.name)}
                >
                  <span className="font-mono text-sm font-medium">{c.name}</span>
                  <Chip tone={c.effect === "read" ? "slate" : "amber"}>{c.effect}</Chip>
                  {open !== c.name && <span className="text-sm text-slate-600 dark:text-slate-400">{c.summary}</span>}
                </button>
                {open === c.name && <div className="mt-3"><CommandForm command={c} onDone={onDone} /></div>}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

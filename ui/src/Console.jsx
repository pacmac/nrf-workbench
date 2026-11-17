import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { PROJECT_NAME, PROJECT_RULE_TEXT, clock } from "./format.js";
import { cannotSend, conversation, historyOf, sendsOf, walk } from "./consoleLog.js";
import { Button, Card, Chip, ClassBadge, Field, SlugNote, Spinner, inputClass } from "./ui.jsx";

// The serial console (docs/specs/dashboard.md, "The Console page"). Every action is a command the service listed,
// sent to the path it listed, and the page adds no rule of its own: when the input is off the reason is the
// service's own refusal in the service's own words, and the service makes the same refusal if it is sent anyway.

const MACRO_NAME = PROJECT_NAME;            // a macro name follows the same rule as a project name (console.md)
const stateTone = { open: "green", absent: "slate", held: "amber", error: "red", unusable: "red" };

function Answer({ outcome }) {
  if (!outcome) return null;
  if (outcome.pending) return <p role="status" className="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-400"><Spinner />Sending…</p>;
  const r = outcome.body;
  if (!r || typeof r !== "object" || !("class" in r)) {
    return <p role="status" className="text-sm"><ClassBadge cls="error" /> HTTP {outcome.status}{r && r.message ? `: ${r.message}` : ""}</p>;
  }
  return (
    <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
      <ClassBadge cls={r.class} />
      {r.code !== "ok" && <Chip>{r.code}</Chip>}
      <span>{r.message}</span>
      {r.data && r.data.reply && <span className="font-mono text-xs">reply: {r.data.reply}</span>}
    </div>
  );
}

function LinkInfo({ link }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={stateTone[link.state] || "slate"} title={link.reason || ""}>{link.state}</Chip>
        {link.baud != null && <Chip>{link.baud} baud</Chip>}
        <Chip tone={link.rx_only ? "amber" : "blue"}>{link.rx_only ? "receive only" : "can send"}</Chip>
        <Chip tone={link.voltage && link.voltage.verified ? "green" : "amber"}>
          {link.voltage && link.voltage.verified ? "voltage verified" : "voltage NOT verified"}
        </Chip>
        {link.node && <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{link.node}</span>}
      </div>
      {link.reason && <p className="text-sm text-slate-700 dark:text-slate-300">{link.reason}</p>}
      {(link.problems || []).map((p) => (
        <p key={p.code + p.message} role="note" className="rounded-lg bg-red-100 p-2 text-sm text-red-900 dark:bg-red-950 dark:text-red-200">
          <span className="font-mono">{p.code}</span>: {p.message}
        </p>
      ))}
    </div>
  );
}

function Log({ items, empty }) {
  const box = useRef(null);
  const [following, setFollowing] = useState(true);
  const stick = useRef(true);

  // Follow the end only while the person is at the end: a log that jumps while it is being read is unreadable.
  useLayoutEffect(() => {
    if (stick.current && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [items]);

  function onScroll() {
    const el = box.current;
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 4;
    stick.current = atEnd;
    setFollowing((old) => (old === atEnd ? old : atEnd));
  }
  function jump() {
    stick.current = true;
    setFollowing(true);
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }

  return (
    <div className="relative">
      <div
        ref={box}
        onScroll={onScroll}
        role="log"
        aria-label="Console log"
        tabIndex={0}
        className="h-96 overflow-auto rounded-lg border border-slate-300 bg-slate-50 p-2 font-mono text-xs leading-5 text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100"
      >
        {items.length === 0 ? <p className="text-slate-600 dark:text-slate-400">{empty}</p> : items.map((x) => (
          <div key={x.key} className="flex gap-2 whitespace-pre-wrap break-all" data-kind={x.kind}>
            <span className="shrink-0 text-slate-500 dark:text-slate-400">{clock(x.wall)}</span>
            {x.kind === "tx" ? (
              <span className="text-blue-800 dark:text-blue-300">
                {"> "}{x.text}
                {x.cls !== "ok" && <span className="ml-2 text-amber-800 dark:text-amber-300">[{x.cls}: {x.code}]</span>}
              </span>
            ) : (
              <span className={x.reply ? "text-emerald-800 dark:text-emerald-300" : ""}>{x.text}{x.partial ? "…" : ""}</span>
            )}
          </div>
        ))}
      </div>
      {!following && (
        <Button tone="quiet" onClick={jump} className="absolute bottom-3 right-4 shadow">Jump to latest</Button>
      )}
    </div>
  );
}

function Input({ link, sendCommand, history, onDone }) {
  const [text, setText] = useState("");
  const [at, setAt] = useState(-1);                       // -1 is the line being typed, else an index into history
  const draft = useRef("");
  const [outcome, setOutcome] = useState(null);
  const field = useRef(null);
  const off = cannotSend(link);
  const busy = Boolean(outcome && outcome.pending);

  async function send(event) {
    event.preventDefault();
    if (!sendCommand || off || busy) return;
    setOutcome({ pending: true });
    try {
      const r = await writeJson(sendCommand.path, { link: link.name, text });
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok) { setText(""); setAt(-1); draft.current = ""; }
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
    if (field.current) field.current.focus();
  }

  function onKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    if (event.key === "Escape") { setText(""); setAt(-1); draft.current = ""; return; }
    const next = walk(history, at, event.key);
    if (next === null) { if (event.key === "ArrowUp" || event.key === "ArrowDown") event.preventDefault(); return; }
    event.preventDefault();
    if (at === -1) draft.current = text;                // the line being typed is kept while walking
    setAt(next);
    setText(next === -1 ? draft.current : history[next]);
  }

  return (
    <form onSubmit={send} className="space-y-2">
      <div className="flex gap-2">
        <span aria-hidden="true" className="self-center font-mono text-sm text-slate-600 dark:text-slate-400">&gt;</span>
        <input
          ref={field}
          aria-label="Command to send"
          className={inputClass + " font-mono"}
          value={text}
          disabled={Boolean(off)}
          autoComplete="off"
          autoCapitalize="off"
          autoCorrect="off"
          spellCheck={false}
          placeholder={off ? "" : "type a line and press Enter; ↑ ↓ walk the history"}
          onChange={(e) => { setText(e.target.value); setAt(-1); draft.current = e.target.value; }}
          onKeyDown={onKey}
        />
        <Button type="submit" disabled={Boolean(off) || busy || !sendCommand}>Send</Button>
      </div>
      {off && <p role="note" className="text-sm text-amber-900 dark:text-amber-200">{off}</p>}
      <Answer outcome={outcome} />
    </form>
  );
}

function MacroResults({ outcome }) {
  if (!outcome) return null;
  if (outcome.pending) return <p role="status" className="flex items-center gap-2 text-sm"><Spinner />Running…</p>;
  const r = outcome.body;
  if (!r || typeof r !== "object" || !("class" in r)) return <p role="status" className="text-sm">HTTP {outcome.status}</p>;
  const results = (r.data && r.data.results) || [];
  return (
    <div role="status" className="space-y-2">
      <div className="flex flex-wrap items-center gap-2"><ClassBadge cls={r.class} />{r.code !== "ok" && <Chip>{r.code}</Chip>}<span className="text-sm">{r.message}</span></div>
      {results.length > 0 && (
        <ol className="list-decimal space-y-1 pl-5 font-mono text-xs">
          {results.map((x, i) => (
            <li key={i}>{x.line} <ClassBadge cls={x.class} />{x.reply ? ` ${x.reply}` : ""}{x.code !== "ok" ? ` ${x.code}` : ""}</li>
          ))}
        </ol>
      )}
    </div>
  );
}

function Macros({ commands, link, onDone }) {
  const find = (name) => (commands || []).find((c) => c.name === name) || null;
  const list = find("console.macro_list");
  const set = find("console.macro_set");
  const del = find("console.macro_delete");
  const run = find("console.macro_run");
  const [macros, setMacros] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState(null);           // the last save or delete
  const [ran, setRan] = useState(null);                   // the last run, every line's result
  const [ask, setAsk] = useState(null);                   // the macro whose delete is being confirmed
  const off = cannotSend(link);

  useEffect(() => {
    if (!list) return undefined;
    let live = true;
    readJson(list.path).then((r) => {
      if (!live) return;
      if (r.ok && r.body && r.body.data) { setMacros(r.body.data.macros || []); setError(""); }
      else { setMacros((old) => old || []); setError(`HTTP ${r.status}` + (r.body && r.body.message ? `: ${r.body.message}` : "")); }
    }).catch((e) => { if (live) { setMacros((old) => old || []); setError(String(e && e.message ? e.message : e)); } });
    return () => { live = false; };
  }, [list, attempt]);

  async function write(request) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok) setAttempt((n) => n + 1);
      return r;
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
      return null;
    }
  }

  async function save(event) {
    event.preventDefault();
    if (!set || !MACRO_NAME.test(name.trim()) || !text.trim()) return;
    const r = await write(writeJson(set.path, { name: name.trim(), text }));
    if (r && r.ok) { setName(""); setText(""); }
  }

  async function runMacro(m) {
    if (!run || !link) return;
    setRan({ pending: true });
    try {
      const r = await writeJson(run.path, { link: link.name, name: m.name });
      setRan(r);
      if (onDone) onDone(r);
    } catch (e) {
      setRan({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  if (commands && !list) return null;
  const nameOk = MACRO_NAME.test(name.trim());

  return (
    <Card title="Macros">
      {error && <p role="note" className="mb-3 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">The macros could not be listed ({error}).</p>}
      {macros && macros.length === 0 && !error && <p className="mb-3 text-sm text-slate-600 dark:text-slate-400">No macros yet. A macro is a name and some lines to send, so a command used often is not typed again.</p>}
      {macros && macros.length > 0 && (
        <ul className="mb-4 divide-y divide-slate-200 dark:divide-slate-800">
          {macros.map((m) => (
            <li key={m.name} className="flex flex-wrap items-start gap-2 py-2">
              <div className="min-w-0 flex-1">
                <p className="font-mono text-sm font-medium">{m.name}</p>
                <pre className="whitespace-pre-wrap break-all font-mono text-xs text-slate-600 dark:text-slate-400">{m.text}</pre>
              </div>
              {ask === m.name ? (
                <div className="flex items-center gap-2" role="group" aria-label={`Delete ${m.name}?`}>
                  <span className="text-sm">Delete?</span>
                  <Button tone="danger" onClick={async () => { setAsk(null); await write(writeJson(del.path, { name: m.name })); }}>Yes</Button>
                  <Button tone="quiet" onClick={() => setAsk(null)}>No</Button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <Button onClick={() => runMacro(m)} disabled={!run || Boolean(off)} title={off || `Send ${m.name} on ${link ? link.name : "a link"}`}>Run</Button>
                  <Button tone="quiet" onClick={() => { setName(m.name); setText(m.text); }}>Edit</Button>
                  <Button tone="quiet" onClick={() => setAsk(m.name)} disabled={!del}>Delete</Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {ran && <div className="mb-4"><MacroResults outcome={ran} /></div>}
      <form onSubmit={save} className="space-y-2">
        <Field label="Macro name" hint={`${PROJECT_RULE_TEXT}. Capital letters are made lower case for you.`}>
          <input className={inputClass + " font-mono"} value={name} onChange={(e) => setName(e.target.value.toLowerCase())} autoComplete="off" spellCheck={false} />
        </Field>
        <SlugNote value={name} />
        <Field label="Lines to send" hint="One command per line. Each is sent in order through the same guards as a typed line, and the run stops at the first refusal.">
          <textarea className={inputClass + " font-mono"} rows={4} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        </Field>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={!set || !nameOk || !text.trim() || Boolean(outcome && outcome.pending)}>Save macro</Button>
          {/* a greyed-out button says why, next to it */}
          {(!nameOk || !text.trim()) && !outcome && (
            <span className="text-sm text-slate-600 dark:text-slate-400">{!name.trim() ? "Give the macro a name." : !nameOk ? "Fix the name above." : "Add at least one line to send."}</span>
          )}
          <Answer outcome={outcome} />
        </div>
      </form>
    </Card>
  );
}

export default function Console({ commands, links, lines, activity, feed, onDone }) {
  const sendCommand = (commands || []).find((c) => c.name === "console.send") || null;
  const [chosen, setChosen] = useState("");
  const list = links || [];
  const link = list.find((l) => l.name === chosen) || list[0] || null;

  const sends = useMemo(() => (link ? sendsOf(activity.rows, link.name) : []), [activity.rows, link]);
  const items = useMemo(() => (link ? conversation(lines[link.name] || [], sends) : []), [lines, sends, link]);
  const history = useMemo(() => historyOf(sends), [sends]);

  if (links === null) return <Card title="Console"><p role="status" className="text-sm">Loading the links…</p></Card>;
  if (list.length === 0) {
    return (
      <Card title="Console">
        <p className="text-sm">No debug link is declared. Declare the adapter that is wired to the device in <span className="font-mono">config/links.yaml</span> on the bench (the wiring, the baud and whether the voltage has been measured); a link appears here as soon as the service is restarted with it.</p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card
        title="Console"
        aside={
          <label className="flex items-center gap-2 text-sm">
            Link
            <select className={inputClass + " w-auto"} value={link.name} onChange={(e) => setChosen(e.target.value)}>
              {list.map((l) => <option key={l.name} value={l.name}>{l.name}, {l.state}</option>)}
            </select>
          </label>
        }
      >
        <div className="space-y-3">
          <LinkInfo link={link} />
          {feed && feed.state !== "live" && (
            <p role="note" className="rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
              The live log is not connected ({feed.state}{feed.detail ? `: ${feed.detail}` : ""}). Lines that arrive meanwhile are kept by the service and appear when it reconnects.
            </p>
          )}
          <Log items={items} empty={link.state === "open" ? "Nothing has been received on this link yet. A quiet link is not a silent device: only a build with its debug output compiled in prints anything." : "Nothing has been received on this link."} />
          <Input key={link.name} link={link} sendCommand={sendCommand} history={history} onDone={onDone} />
        </div>
      </Card>
      <Macros commands={commands} link={link} onDone={onDone} />
    </div>
  );
}

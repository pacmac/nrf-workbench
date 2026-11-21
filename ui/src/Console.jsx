import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { readJson, writeJson } from "./api.js";
import { PROJECT_NAME, PROJECT_RULE_TEXT, clock } from "./format.js";
import { cannotSend, conversation, historyOf, sendsOf, walk } from "./consoleLog.js";
import { Button, Card, Chip, ClassBadge, Field, PageHeader, SlugNote, Spinner, cx, inputClass, useWide } from "./ui.jsx";
import { Drawer } from "./meter/Monitor.jsx";

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
        {/* The voltage only matters for sending, so a receive-only link says nothing about it. */}
        {!link.rx_only && (
          <Chip tone={link.voltage && link.voltage.verified ? "green" : "amber"}>
            {link.voltage && link.voltage.verified ? "voltage verified" : "voltage NOT verified"}
          </Chip>
        )}
        {link.node && <span className="font-mono text-xs text-slate-600 dark:text-slate-400">{link.node}</span>}
      </div>
      {link.reason && <p className="text-sm text-slate-700 dark:text-slate-300">{link.reason}</p>}
      {link.own_bridge && (
        <p role="note" className="rounded-md bg-slate-100 p-2 text-[13px] text-slate-800 dark:bg-slate-800 dark:text-slate-200">
          The ESP32's own USB adapter: opening it can reset some boards.
        </p>
      )}
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

function Macros({ commands, link, onDone, bare }) {
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
  const Wrap = bare ? "div" : Card;                 // in a drawer the drawer is the panel and says "Macros" itself

  return (
    <Wrap {...(bare ? {} : { title: "Macros" })}>
      {error && <p role="note" className="mb-3 rounded-lg bg-amber-100 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">The macros could not be listed ({error}).</p>}
      {macros && macros.length === 0 && !error && <p className="mb-3 text-[13px] text-slate-600 dark:text-slate-400">No macros yet.</p>}
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
          <textarea className={inputClass + " font-mono!"} rows={4} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
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
    </Wrap>
  );
}

// Turn the addresses in a crash dump into functions, using the ELF of a stored ESP32 bundle (docs/specs/esp32.md). Nothing runs
// until the person presses Decode; the text starts as the last lines of this link's log and is theirs to change.
const DECODE_LINES = 60;           // a panic dump is about 30 lines (header, registers, backtrace); 60 holds it with what led up to it

function Decode({ commands, link, recent, onDone, bare }) {
  const decode = (commands || []).find((c) => c.name === "esp32.decode") || null;
  const list = (commands || []).find((c) => c.name === "firmware.list") || null;
  const [open, setOpen] = useState(Boolean(bare));        // in a drawer the form is open from the start
  const [bundles, setBundles] = useState(null);
  const [firmware, setFirmware] = useState("");
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState(null);

  function load() {
    setText(recent.slice(-DECODE_LINES).join("\n"));
    setOutcome(null);
    if (list) {
      readJson(list.path).then((r) => {
        const items = r.ok && r.body && r.body.data ? r.body.data.items || [] : [];
        setBundles(items.filter((i) => i.kind === "esp-bundle" && i.elf));
      }).catch(() => setBundles([]));
    }
  }
  function toggle() {
    if (!open) load();
    setOpen(!open);
  }
  useEffect(() => { if (bare) load(); }, []);            // eslint-disable-line react-hooks/exhaustive-deps

  async function run(event) {
    event.preventDefault();
    if (!decode || !firmware || !text.trim()) return;
    setOutcome({ pending: true });
    try {
      const r = await writeJson(decode.path, { firmware, text });
      setOutcome(r);
      if (onDone) onDone(r);
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  if (!decode) return null;
  const data = outcome && outcome.body && outcome.body.data;
  const Wrap = bare ? "div" : Card;
  return (
    <Wrap {...(bare ? {} : { title: "Decode a crash", aside: <Button tone="quiet" onClick={toggle} aria-expanded={open}>{open ? "Close" : "Open"}</Button> })}>
      {!open ? (
        <p className="text-[13px] text-slate-600 dark:text-slate-400">Names the functions in a crash dump.</p>
      ) : (
        <form onSubmit={run} className="space-y-3">
          {bundles !== null && bundles.length === 0 ? (
            <p className="text-[13px]">No stored bundle has an ELF. Upload one on the Firmware page.</p>
          ) : (
            <Field label="Firmware that is running" hint="The bundle that was flashed: its ELF is what the addresses are looked up in.">
              <select className={inputClass} value={firmware} onChange={(e) => setFirmware(e.target.value)} aria-label="Firmware to decode against">
                <option value="">Choose a bundle…</option>
                {(bundles || []).map((b) => <option key={b.sha256} value={b.sha256}>{b.name || "unnamed"} ({b.sha256.slice(0, 8)}{b.chip ? `, ${b.chip}` : ""})</option>)}
              </select>
            </Field>
          )}
          <Field label="Crash dump" hint={`The last ${DECODE_LINES} lines of ${link.name}. Change it to paste another.`}>
            <textarea className={inputClass + " h-40 font-mono! text-xs!"} value={text} onChange={(e) => setText(e.target.value)} aria-label="Crash dump" spellCheck="false" />
          </Field>
          <Button type="submit" disabled={!firmware || !text.trim() || Boolean(outcome && outcome.pending)}>Decode</Button>
          {outcome && !outcome.pending && (
            <div role="status" className="space-y-2">
              <Answer outcome={outcome} />
              {data && data.frames && data.frames.length === 0 && <p className="text-[13px]">No address found in that text.</p>}
              {data && data.frames && data.frames.length > 0 && (
                <table className="w-full text-left text-xs">
                  <thead><tr><th className="py-1 pr-3 font-medium">Address</th><th className="py-1 pr-3 font-medium">Function</th><th className="py-1 font-medium">Source</th></tr></thead>
                  <tbody>
                    {data.frames.map((f) => (
                      <tr key={f.address} className="border-t border-slate-200 align-top dark:border-slate-800">
                        <td className="py-1 pr-3 font-mono">{f.address}</td>
                        <td className="py-1 pr-3 font-mono break-all">{f.function ? `${f.function}${f.offset ? ` +0x${f.offset.toString(16)}` : ""}` : "not in the ELF"}</td>
                        <td className="py-1 font-mono break-all">{f.file ? `${f.file}:${f.line}` : ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {data && data.truncated && <p className="text-xs text-slate-600 dark:text-slate-400">Only the first {data.frames.length} addresses are shown.</p>}
              {data && !data.tool && data.frames.length > 0 && <p className="text-xs text-slate-600 dark:text-slate-400">No file and line: the bench has no addr2line.</p>}
            </div>
          )}
        </form>
      )}
    </Wrap>
  );
}

// One line of the log as plain text: what the person would copy. A line sent from here has its outcome after it; a line the board cut short ends in an ellipsis.
function lineText(x) {
  if (x.kind === "tx") return `> ${x.text}${x.cls !== "ok" ? ` [${x.cls}: ${x.code}]` : ""}`;
  return `${x.text}${x.partial ? "…" : ""}`;
}

const LOG_ROW_H = 20;            // one line of 12 px monospace text at 20 px leading, so the rows that fit are the height over this

// The newest lines that fit the space the log has, one row each (docs/specs/navigation.md, "Full-height pages", Console). It FOLLOWS the newest line until the person pages back, and a
// page back stays put when lines arrive: its place is the index just after its last line, counted from the start, so a new line does not move it. A row picked shows its whole text in
// the strip below, since a line (a board's SLEEPNOW) can be longer than a row.
function Tail({ items, empty }) {
  const box = useRef(null);
  const [fit, setFit] = useState(10);
  const [endAt, setEndAt] = useState(null);                  // null follows the newest; else the index after the last line shown
  const [picked, setPicked] = useState(null);                // the key of the line whose whole text is shown
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const measure = () => setFit(Math.max(1, Math.floor(el.clientHeight / LOG_ROW_H)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const n = items.length;
  const following = endAt === null || endAt >= n;
  const end = following ? n : Math.max(endAt, Math.min(fit, n));
  const start = Math.max(0, end - fit);
  const part = items.slice(start, end);
  const shown = items.find((x) => x.key === picked) || part[part.length - 1] || null;
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1">
      <ul ref={box} role="log" aria-label="Console log" className="min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-300 bg-slate-50 font-mono text-xs text-slate-900 dark:border-slate-700 dark:bg-slate-950 dark:text-slate-100">
        {n === 0 && <li className="p-2 text-slate-600 dark:text-slate-400">{empty}</li>}
        {part.map((x) => (
          <li key={x.key} data-kind={x.kind} style={{ height: LOG_ROW_H }} onClick={() => setPicked(x.key)}
            className={cx("flex cursor-pointer items-center gap-2 px-2 hover:bg-slate-200 dark:hover:bg-slate-800", picked === x.key && "bg-blue-100 dark:bg-blue-950")}>
            <span className="shrink-0 text-slate-500 dark:text-slate-400">{clock(x.wall)}</span>
            <span className={cx("min-w-0 flex-1 truncate", x.kind === "tx" ? "text-blue-800 dark:text-blue-300" : x.reply ? "text-emerald-800 dark:text-emerald-300" : "")}>{lineText(x)}</span>
          </li>
        ))}
      </ul>
      <div className="flex h-8 shrink-0 items-center gap-1 text-xs text-slate-600 dark:text-slate-400">
        <span data-testid="follow" className={following ? "font-medium text-emerald-700 dark:text-emerald-400" : "font-medium text-amber-700 dark:text-amber-400"}>{following ? "Following" : "Paused"}</span>
        {n > 0 && <span data-testid="pager" className="ml-2 mr-auto">{start + 1} to {end} of {n}</span>}
        <Button tone="quiet" className="min-h-7! px-3! py-0.5!" disabled={start === 0} onClick={() => setEndAt(Math.max(Math.min(fit, n), end - fit))} aria-label="Older lines">‹</Button>
        <Button tone="quiet" className="min-h-7! px-3! py-0.5!" disabled={following} onClick={() => setEndAt(end + fit >= n ? null : end + fit)} aria-label="Newer lines">›</Button>
        <Button tone="quiet" className="min-h-7! px-3! py-0.5!" disabled={following} onClick={() => setEndAt(null)}>Latest</Button>
      </div>
      <div data-testid="line-detail" className="h-14 shrink-0 overflow-hidden whitespace-pre-wrap break-all rounded-md bg-slate-100 p-2 font-mono text-xs leading-4 text-slate-800 dark:bg-slate-900 dark:text-slate-200">{shown ? `${clock(shown.wall)}  ${lineText(shown)}` : ""}</div>
    </div>
  );
}

// The wide page: everything on one screen (the log, the link, the input) and the two forms in drawers. The page flows on a phone.
function WideConsole({ commands, list, link, setChosen, feed, items, history, sendCommand, lines, onDone }) {
  const [drawer, setDrawer] = useState(null);                // "macros" or "decode"
  const close = () => setDrawer(null);
  return (
    <div className="relative flex min-h-0 flex-1 flex-col gap-2">
      <PageHeader
        title="Console"
        topic="console"
        aside={
          <>
            {commands && commands.some((c) => c.name === "console.macro_list") && <Button tone="quiet" onClick={() => setDrawer("macros")}>Macros</Button>}
            {link.own_bridge && commands && commands.some((c) => c.name === "esp32.decode") && <Button tone="quiet" onClick={() => setDrawer("decode")}>Decode a crash</Button>}
            <label className="flex items-center gap-2 text-sm">
              Link
              <select aria-label="Link" className={inputClass + " w-auto"} value={link.name} onChange={(e) => setChosen(e.target.value)}>
                {list.map((l) => <option key={l.name} value={l.name}>{l.name}, {l.state}</option>)}
              </select>
            </label>
          </>
        }
      />
      <Card className="flex min-h-0 flex-1 flex-col gap-2">
        <LinkInfo link={link} />
        {feed && feed.state !== "live" && (
          <p role="note" className="shrink-0 rounded-md bg-amber-100 p-2 text-[13px] text-amber-900 dark:bg-amber-950 dark:text-amber-200">
            The live log is not connected ({feed.state}). Lines appear when it reconnects.
          </p>
        )}
        <Tail key={`log-${link.name}`} items={items} empty={link.state === "open" ? "Nothing received yet." : "Nothing received."} />
        <Input key={`input-${link.name}`} link={link} sendCommand={sendCommand} history={history} onDone={onDone} />
      </Card>
      {drawer === "macros" && <Drawer title="Macros" onClose={close} wide><Macros bare commands={commands} link={link} onDone={onDone} /></Drawer>}
      {drawer === "decode" && <Drawer title="Decode a crash" onClose={close} wide><Decode bare key={link.name} commands={commands} link={link} recent={(lines[link.name] || []).map((l) => l.text)} onDone={onDone} /></Drawer>}
    </div>
  );
}

export default function Console({ commands, links, lines, activity, feed, onDone }) {
  const sendCommand = (commands || []).find((c) => c.name === "console.send") || null;
  const wide = useWide();
  const [chosen, setChosen] = useState("");
  const list = links || [];
  const link = list.find((l) => l.name === chosen) || list[0] || null;

  const sends = useMemo(() => (link ? sendsOf(activity.rows, link.name) : []), [activity.rows, link]);
  const items = useMemo(() => (link ? conversation(lines[link.name] || [], sends) : []), [lines, sends, link]);
  const history = useMemo(() => historyOf(sends), [sends]);

  if (links === null) return <div className="space-y-4"><PageHeader title="Console" topic="console" /><p role="status" className="text-[13px] text-slate-600 dark:text-slate-400">Loading the links…</p></div>;
  if (list.length === 0) {
    return (
      <div className="space-y-4">
        <PageHeader title="Console" topic="console" />
        <Card><p className="text-[13px] text-slate-600 dark:text-slate-400">No debug log is wired. Connect one on the Wiring page.</p></Card>
      </div>
    );
  }

  if (wide) {
    return <WideConsole commands={commands} list={list} link={link} setChosen={setChosen} feed={feed} items={items} history={history} sendCommand={sendCommand} lines={lines} onDone={onDone} />;
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Console" topic="console" />
      <Card
        aside={
          <label className="flex items-center gap-2 text-sm">
            Link
            <select aria-label="Link" className={inputClass + " w-auto"} value={link.name} onChange={(e) => setChosen(e.target.value)}>
              {list.map((l) => <option key={l.name} value={l.name}>{l.name}, {l.state}</option>)}
            </select>
          </label>
        }
      >
        <div className="space-y-3">
          <LinkInfo link={link} />
          {feed && feed.state !== "live" && (
            <p role="note" className="rounded-md bg-amber-100 p-2 text-[13px] text-amber-900 dark:bg-amber-950 dark:text-amber-200">
              The live log is not connected ({feed.state}). Lines appear when it reconnects.
            </p>
          )}
          <Log items={items} empty={link.state === "open" ? "Nothing received yet." : "Nothing received."} />
          <Input key={link.name} link={link} sendCommand={sendCommand} history={history} onDone={onDone} />
        </div>
      </Card>
      <Macros commands={commands} link={link} onDone={onDone} />
      {link.own_bridge && <Decode key={link.name} commands={commands} link={link} recent={(lines[link.name] || []).map((l) => l.text)} onDone={onDone} />}
    </div>
  );
}

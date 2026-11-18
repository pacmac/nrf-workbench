import { useState } from "react";
import { writeJson } from "./api.js";
import { Button, Card, Chip, Field, SlugNote, inputClass } from "./ui.jsx";
import { PROJECT_NAME, clock } from "./format.js";
import { ResultView } from "./Commands.jsx";

const MODE_TONE = { app: "green", bootloader: "amber" };

const STATE_TEXT = {
  app: "running its application",
  bootloader: "in its bootloader, waiting for a package",
  two: "attached in BOTH modes at once",
  absent: "not attached",
};

// A name is a label kept against the board's USB serial in a file on the bench (devices.md, "Names"): it never chooses a
// board for anything, the serial still does. Saving or removing one is a command the service listed.
function NameEditor({ board, commands, onNamed, onDone }) {
  const find = (n) => (commands || []).find((c) => c.name === n) || null;
  const set = find("devices.name");
  const clear = find("devices.unname");
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const [outcome, setOutcome] = useState(null);

  async function send(request) {
    setOutcome({ pending: true });
    try {
      const r = await request;
      setOutcome(r);
      if (onDone) onDone(r);
      if (r.ok) { setEditing(false); if (onNamed) onNamed(); }
    } catch (e) {
      setOutcome({ status: 0, body: { message: "The request did not complete: " + String(e) } });
    }
  }

  if (!set) return <>{board.name || <span className="text-slate-600 dark:text-slate-400">no name</span>}</>;
  const pending = Boolean(outcome && outcome.pending);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {board.name ? <strong>{board.name}</strong> : <span className="text-slate-600 dark:text-slate-400">no name</span>}
        {!editing && <Button tone="quiet" onClick={() => { setText(board.name || ""); setOutcome(null); setEditing(true); }} aria-label={`${board.name ? "Rename" : "Name"} ${board.serial}`}>{board.name ? "Rename" : "Name it"}</Button>}
        {!editing && board.name && clear && <Button tone="quiet" disabled={pending} onClick={() => send(writeJson(clear.path, { serial: board.serial }))} aria-label={`Remove the name of ${board.serial}`}>Remove name</Button>}
      </div>
      {editing && (
        <form onSubmit={(e) => { e.preventDefault(); send(writeJson(set.path, { serial: board.serial, name: text.trim() })); }} className="space-y-2">
          <Field label="Name" hint="Kept against this board's serial, so it follows the board to any port. 1 to 64 characters: a-z, 0-9, dot, underscore, hyphen.">
            <input className={inputClass} type="text" value={text} onChange={(e) => setText(e.target.value.toLowerCase())} aria-label={`Name for ${board.serial}`} autoComplete="off" spellCheck="false" />
          </Field>
          <SlugNote value={text} />
          <div className="flex gap-2">
            <Button type="submit" disabled={pending || !PROJECT_NAME.test(text.trim())}>Save name</Button>
            <Button tone="quiet" onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </form>
      )}
      {outcome && !outcome.pending && !outcome.ok && <ResultView outcome={outcome} pendingText="Working…" />}
    </div>
  );
}

// One card per board, with everything the service knows about it. Nothing is hidden behind "ready": the state is
// what sysfs says, and a flash will say exactly why it refuses if it does.
export default function Devices({ boards, boardsAt, onFlash, commands, onNamed, onDone }) {
  if (boards === null) return <Card title="Devices"><p className="text-sm text-slate-600 dark:text-slate-400">Waiting for the first report from the service.</p></Card>;
  if (boards.length === 0) {
    return (
      <Card title="Devices">
        <p className="text-sm">No board is attached. <span className="text-slate-600 dark:text-slate-400">As of {clock(boardsAt)}.</span></p>
      </Card>
    );
  }
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {boards.map((b) => (
        <Card key={`${b.serial}-${b.mode}-${b.sysfs}`} title={b.name || b.product || b.id} aside={<Chip tone={MODE_TONE[b.mode] || "slate"}>{b.mode}</Chip>}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            <dt className="text-slate-600 dark:text-slate-400">Name</dt>
            <dd><NameEditor board={b} commands={commands} onNamed={onNamed} onDone={onDone} /></dd>
            <dt className="text-slate-600 dark:text-slate-400">Product</dt>
            <dd>{b.product}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Serial</dt>
            <dd className="break-all font-mono">{b.serial}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Profile</dt>
            <dd>{b.profile}</dd>
            <dt className="text-slate-600 dark:text-slate-400">State</dt>
            <dd>{STATE_TEXT[b.state] || b.state}</dd>
            <dt className="text-slate-600 dark:text-slate-400">USB id</dt>
            <dd className="font-mono">{b.id}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Port</dt>
            <dd className="font-mono">{b.sysfs}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Enumeration</dt>
            <dd className="font-mono">{b.enum}</dd>
            <dt className="text-slate-600 dark:text-slate-400">Reported</dt>
            <dd>{clock(boardsAt)}</dd>
          </dl>
          <div className="mt-3">
            <Button tone="quiet" onClick={() => onFlash(b)}>Flash this board…</Button>
          </div>
        </Card>
      ))}
    </div>
  );
}

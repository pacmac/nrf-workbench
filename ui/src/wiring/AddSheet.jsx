import { useEffect, useRef, useState } from "react";
import { Button, Field, inputClass } from "../ui.jsx";
import {
  INTENTS, describe, firstOpen, holders, intentLabel, isAnswered, isComplete, sharedPins, short, visibleQuestions, withAnswer,
} from "./model.js";

// The add / change sheet (docs/specs/wiring.md, "The UI"). One sheet, not a wizard: each answered question collapses to
// one line you can change, the next open question is shown, and when everything is answered the finished sentence is
// the connection. Only what is possible is offered; the rest stays visible, greyed, and says who has it.

// An intent that is not built yet is listed, so you can see what is coming, but cannot be chosen, and says why.
const INTENT_QUESTION = {
  id: "intent", title: "What do you want to do?", type: "choice",
  options: (ctx) => INTENTS.map((i) => (ctx.built.includes(i.value) ? i : { ...i, disabled: true, why: "Not built yet." })),
  answer: (spec) => intentLabel(spec.intent),
};

function Choice({ q, options, value, onPick }) {
  return (
    <div className="space-y-2">
      {options.map((o) => {
        const checked = value === o.value;
        return (
          <label
            key={String(o.value)}
            className={`flex items-start gap-3 rounded-lg border p-3 ${o.disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"} ${
              checked ? "border-blue-600 bg-blue-50 dark:border-blue-400 dark:bg-blue-950" : "border-slate-300 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800"}`}
          >
            <input type="radio" name={q.id} className="mt-1" checked={checked} disabled={o.disabled} onChange={() => onPick(o.value)} />
            <span className="min-w-0">
              <span className="block text-sm font-medium">{o.label}</span>
              {o.hint && <span className="block text-xs text-slate-600 dark:text-slate-400">{o.hint}</span>}
              {o.disabled && o.why && <span className="block text-xs text-amber-800 dark:text-amber-300">{o.why}</span>}
            </span>
          </label>
        );
      })}
    </div>
  );
}

function Chips({ q, options, value, onPick }) {
  const taken = options.filter((o) => o.disabled && o.why);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {options.map((o) => (
          <label key={String(o.value)} className={o.disabled ? "cursor-not-allowed" : "cursor-pointer"} title={o.disabled ? o.why : undefined}>
            <input type="radio" name={q.id} className="peer sr-only" checked={value === o.value} disabled={o.disabled} onChange={() => onPick(o.value)} />
            <span
              className={`inline-flex min-h-10 items-center rounded-lg border px-3 text-sm peer-checked:border-blue-600 peer-checked:bg-blue-600 peer-checked:text-white peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-blue-600 dark:peer-checked:border-blue-400 dark:peer-checked:bg-blue-500 dark:peer-checked:text-slate-950 ${
                o.disabled ? "border-slate-200 bg-slate-100 text-slate-400 line-through dark:border-slate-800 dark:bg-slate-900 dark:text-slate-600" : "border-slate-300 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800"}`}
            >
              {o.label}
            </span>
          </label>
        ))}
      </div>
      {taken.length > 0 && (
        <ul className="space-y-0.5 text-xs text-amber-800 dark:text-amber-300">
          {taken.map((o) => <li key={String(o.value)}>{o.why}</li>)}
        </ul>
      )}
    </div>
  );
}

function TextAnswer({ q, onDone }) {
  const [draft, setDraft] = useState("");
  const ok = q.type === "number" ? draft !== "" && Number(draft) >= 1 : draft.trim() !== "";
  function submit(event) {
    event.preventDefault();
    if (ok) onDone(q.type === "number" ? Number(draft) : draft.trim());
  }
  return (
    <form onSubmit={submit} className="flex gap-2">
      <input
        className={inputClass + (q.type === "number" ? "" : " font-mono")}
        type={q.type === "number" ? "number" : "text"}
        min={q.type === "number" ? 1 : undefined}
        step={q.type === "number" ? 1 : undefined}
        aria-label={q.title}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
      <Button type="submit" disabled={!ok}>Done</Button>
    </form>
  );
}

function OpenQuestion({ q, ctx, spec, onAnswer, onSwitchIntent }) {
  const options = q.options ? q.options(ctx, spec) : [];
  const usable = options.filter((o) => !o.disabled);
  return (
    <fieldset className="space-y-2 rounded-lg border border-blue-300 bg-white p-3 dark:border-blue-800 dark:bg-slate-900">
      <legend className="px-1 text-sm font-semibold">{q.title}</legend>
      {q.hint && <p className="text-xs text-slate-600 dark:text-slate-400">{q.hint}</p>}
      {(q.type === "text" || q.type === "number") && <TextAnswer q={q} onDone={(v) => onAnswer(q, v)} />}
      {q.type === "choice" && options.length > 0 && <Choice q={q} options={options} value={spec[q.id]} onPick={(v) => onAnswer(q, v)} />}
      {q.type === "chips" && <Chips q={q} options={options} value={spec[q.id]} onPick={(v) => onAnswer(q, v)} />}
      {(q.type === "choice" || q.type === "chips") && options.length === 0 && (
        <div className="space-y-2">
          <p className="text-sm">{q.empty}</p>
          {q.emptyAction && <Button tone="quiet" onClick={() => onSwitchIntent(q.emptyAction.intent)}>{q.emptyAction.label}</Button>}
        </div>
      )}
      {options.length > 0 && usable.length === 0 && q.type !== "chips" && (
        <p role="note" className="text-sm text-amber-800 dark:text-amber-300">Everything here is in use. Remove a connection that uses it to free it.</p>
      )}
    </fieldset>
  );
}

function Answered({ q, spec, ctx, onChange, changeable }) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg bg-slate-100 px-3 py-2 dark:bg-slate-800">
      <div className="min-w-0">
        <p className="text-xs text-slate-600 dark:text-slate-400">{q.title}</p>
        <p className="text-sm font-medium">{q.answer(spec, ctx)}</p>
      </div>
      {changeable && <Button tone="quiet" onClick={onChange} aria-label={`Change: ${q.title}`}>Change</Button>}
    </div>
  );
}

export default function AddSheet({ device, hue, base, initial, editingId, onSave, onCancel }) {
  const [spec, setSpec] = useState(initial || {});
  const [openId, setOpenId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const heading = useRef(null);
  useEffect(() => { if (heading.current) heading.current.focus(); }, []);

  const ctx = { ...base, holders: holders(base.connections, editingId), shared: sharedPins(base.connections) };
  const editing = Boolean(editingId);
  const questions = spec.intent ? [INTENT_QUESTION, ...visibleQuestions(spec)] : [INTENT_QUESTION];
  const next = spec.intent ? firstOpen(spec) : INTENT_QUESTION;
  const openNow = openId ? questions.find((q) => q.id === openId) : next;
  const complete = isComplete(spec);
  const name = `${device.model} ${short(device.serial)}`;

  function answer(q, value) {
    setSpec(q.id === "intent" ? { intent: value } : withAnswer(spec, q.id, value));
    setOpenId(null);
  }
  const switchIntent = (intent) => { setSpec({ intent }); setOpenId(null); };

  // The page's save returns an error message, or nothing when it worked. A refusal is the service's, worded by it, and the
  // sheet stays open with everything you answered so you can change one thing and try again.
  async function save() {
    setBusy(true);
    setFailure("");
    const why = await onSave(spec);
    if (why) { setFailure(why); setBusy(false); }
  }

  return (
    <section aria-label={editing ? "Change this connection" : `Connect something to ${name}`} className="space-y-3 rounded-xl border border-blue-300 bg-slate-50 p-4 dark:border-blue-800 dark:bg-slate-950">
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className={`h-3 w-3 rounded-full ${hue.dot}`} />
        <h3 ref={heading} tabIndex={-1} className="text-base font-semibold outline-none">
          {editing ? `Change this connection on ${name}` : `Connect something to ${name}`}
        </h3>
      </div>

      {questions.map((q) => {
        const isOpen = openNow && openNow.id === q.id;
        if (isOpen) return <OpenQuestion key={q.id} q={q} ctx={ctx} spec={spec} onAnswer={answer} onSwitchIntent={switchIntent} />;
        if (isAnswered(spec, q) || (q.id === "intent" && spec.intent)) {
          return <Answered key={q.id} q={q} spec={spec} ctx={ctx} onChange={() => setOpenId(q.id)} changeable={!(editing && q.id === "intent")} />;
        }
        return null;
      })}

      {complete && !openId && (
        <div className="space-y-3">
          <div className="flex gap-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
            <span aria-hidden="true" className={`w-1.5 shrink-0 rounded-full ${hue.dot}`} />
            <p className="text-sm" data-testid="sentence">{describe({ id: editingId || "new", device: device.serial, spec }, ctx).summary}</p>
          </div>
          {failure && <p role="alert" className="rounded-lg bg-red-100 p-2 text-sm text-red-900 dark:bg-red-950 dark:text-red-200">{failure}</p>}
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy} onClick={save}>{editing ? "Save changes" : "Connect"}</Button>
            <Button tone="quiet" onClick={onCancel}>Cancel</Button>
          </div>
        </div>
      )}
      {!(complete && !openId) && (
        <div><Button tone="quiet" onClick={onCancel}>Cancel</Button></div>
      )}
    </section>
  );
}

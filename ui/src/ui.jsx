import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { slugProblem } from "./format.js";

export const cx = (...parts) => parts.filter(Boolean).join(" ");

const TONES = {
  slate: "bg-slate-100 text-slate-700 ring-slate-300 dark:bg-slate-800 dark:text-slate-200 dark:ring-slate-600",
  green: "bg-emerald-100 text-emerald-900 ring-emerald-300 dark:bg-emerald-950 dark:text-emerald-200 dark:ring-emerald-700",
  amber: "bg-amber-100 text-amber-900 ring-amber-300 dark:bg-amber-950 dark:text-amber-200 dark:ring-amber-700",
  red: "bg-red-100 text-red-900 ring-red-300 dark:bg-red-950 dark:text-red-200 dark:ring-red-700",
  purple: "bg-purple-100 text-purple-900 ring-purple-300 dark:bg-purple-950 dark:text-purple-200 dark:ring-purple-700",
  blue: "bg-blue-100 text-blue-900 ring-blue-300 dark:bg-blue-950 dark:text-blue-200 dark:ring-blue-700",
};

export function Chip({ tone = "slate", children, title }) {
  return (
    <span title={title} className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", TONES[tone])}>
      {children}
    </span>
  );
}

// The class of a Result decides the colour, and a refusal is never drawn like a success.
const CLASS_TONE = { ok: "green", refused: "amber", failed: "red", suspect: "purple", usage: "slate", error: "red" };

export function ClassBadge({ cls }) {
  return <Chip tone={CLASS_TONE[cls] || "slate"}>{cls}</Chip>;
}

// A CSS animation, not a timer (the page has none: docs/specs/dashboard.md, "no polling"). It takes the colour of
// the text around it, so it needs no light and dark pair, and for a visitor who asks for reduced motion it stays a
// still ring instead of turning.
export function Spinner({ className }) {
  return (
    <span
      aria-hidden="true"
      className={cx("inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none motion-reduce:border-t-current", className)}
    />
  );
}

// A panel: a hairline and a flat fill, no shadow, no gradient (docs/specs/dashboard-redesign.md, "Tokens"). A page is a few of
// these in a column, and the heading is the only emphasis.
export function Card({ title, aside, children, className }) {
  return (
    <section className={cx("rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900", className)}>
      {(title || aside) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-[17px] font-semibold">{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

// The state of a thing, drawn so that the SHAPE says it before the colour does: a filled disc is connected or listening, a ring is
// not attached, a diamond needs attention, a square is a fault. Colour repeats it. It is a labelled image, so a screen reader hears it.
export const LAMPS = {
  connected: { label: "connected", shape: "disc", tone: "text-emerald-600 dark:text-emerald-400" },
  listening: { label: "listening", shape: "disc", tone: "text-emerald-600 dark:text-emerald-400" },
  absent: { label: "not attached", shape: "ring", tone: "text-slate-400 dark:text-slate-500" },
  attention: { label: "needs attention", shape: "diamond", tone: "text-amber-600 dark:text-amber-400" },
  fault: { label: "fault", shape: "square", tone: "text-red-600 dark:text-red-400" },
};

export function Lamp({ state, size = 14, className }) {
  const l = LAMPS[state] || LAMPS.absent;
  return (
    <svg role="img" aria-label={l.label} viewBox="0 0 16 16" width={size} height={size} className={cx("shrink-0", l.tone, className)}>
      {l.shape === "disc" && <circle cx="8" cy="8" r="6" fill="currentColor" />}
      {l.shape === "ring" && <circle cx="8" cy="8" r="5.25" fill="none" stroke="currentColor" strokeWidth="2" />}
      {l.shape === "diamond" && <path d="M8 1.5 14.5 8 8 14.5 1.5 8Z" fill="currentColor" />}
      {l.shape === "square" && <rect x="2.5" y="2.5" width="11" height="11" rx="1.5" fill="currentColor" />}
    </svg>
  );
}

// Where "go to a page" and "open a help topic" come from, so a row or a ? anywhere can navigate without every page being handed App's setters.
export const NavContext = createContext({ go: () => {}, help: () => {} });

// The top of a working page: its title, what it holds in a word or two, and a ? that opens the Help topic for it. Nothing else is said here:
// anything longer lives in Help (Peter, 2026-11-18).
export function PageHeader({ title, topic, aside, children }) {
  const nav = useContext(NavContext);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <h2 className="text-2xl font-semibold">{title}</h2>
      {children}
      <div className="ml-auto flex items-center gap-2">
        {aside}
        {topic && (
          <button type="button" onClick={() => nav.help(topic)} aria-label={`Help: ${title}`} title={`Help: ${title}`}
            className="grid h-8 w-8 place-items-center rounded-full border border-slate-300 text-sm font-semibold text-slate-700 hover:bg-slate-200 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">?</button>
        )}
      </div>
    </div>
  );
}

// A button is `type="button"` unless a caller says "submit": the browser default inside a form is submit, and in
// the flash dialog an unmarked button would start a flash.
export function Button({ tone = "primary", type = "button", className, ...props }) {
  const styles = {
    primary: "bg-blue-600 text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-400 dark:text-slate-950",
    danger: "bg-red-600 text-white hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-400 dark:text-slate-950",
    quiet: "bg-slate-100 text-slate-800 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700",
  };
  return (
    <button
      type={type}
      className={cx("min-h-10 rounded-md px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50", styles[tone], className)}
      {...props}
    />
  );
}

// Is the window wide enough for the full-height layout (docs/specs/navigation.md, "Full-height pages"): the same 768 px the shell's `md:` classes switch at. A media query's own change
// event, not a timer, so a window that is resized or rotated re-lays the page.
export function useWide() {
  const query = "(min-width: 768px)";
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setWide(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return wide;
}

// The rows that fit the space the list has, then pages. The space is measured (a ResizeObserver, no timer), so a taller window shows more rows and a shorter one pages sooner.
// `rowH` is the height of one row in px; `render(item)` is the content of a row and `keyOf(item)` its key. The list must be given a height by its parent (a flex column with `min-h-0`).
export function FitList({ items, rowH, render, keyOf, label, empty }) {
  const box = useRef(null);
  const [fit, setFit] = useState(3);
  const [page, setPage] = useState(0);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return undefined;
    const measure = () => setFit(Math.max(1, Math.floor(el.clientHeight / rowH)));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [rowH]);
  const pages = Math.max(1, Math.ceil(items.length / fit));
  const at = Math.min(page, pages - 1);
  const part = items.slice(at * fit, at * fit + fit);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ul ref={box} aria-label={label} className="min-h-0 flex-1 divide-y divide-slate-200 overflow-hidden dark:divide-slate-800">
        {items.length === 0 && <li className="py-2 text-[13px] text-slate-600 dark:text-slate-400">{empty}</li>}
        {part.map((it) => <li key={keyOf(it)} style={{ height: rowH }} className="flex items-center gap-2">{render(it)}</li>)}
      </ul>
      {items.length > fit && (
        <div className="flex h-9 shrink-0 items-center gap-1 text-xs text-slate-600 dark:text-slate-400">
          <span data-testid="pager" className="mr-auto">{at * fit + 1} to {at * fit + part.length} of {items.length}</span>
          <Button tone="quiet" className="min-h-7! px-3! py-0.5!" disabled={at === 0} onClick={() => setPage(at - 1)} aria-label={`Previous ${label}`}>‹</Button>
          <Button tone="quiet" className="min-h-7! px-3! py-0.5!" disabled={at >= pages - 1} onClick={() => setPage(at + 1)} aria-label={`Next ${label}`}>›</Button>
        </div>
      )}
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-600 dark:text-slate-400">{hint}</span>}
    </label>
  );
}

// Under a name field: says what is wrong with the name, next to the field, as soon as there is something wrong.
export function SlugNote({ value }) {
  const problem = slugProblem((value || "").trim());
  return problem ? <p role="alert" className="mt-1 text-xs text-red-700 dark:text-red-300">{problem}</p> : null;
}

export const inputClass =
  "w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-950";

// The WHOLE value, never a tail (the project's rule: the whole tool output is always returned and always shown).
export function Json({ value, className }) {
  return (
    <pre className={cx("max-h-96 overflow-auto rounded-lg bg-slate-100 p-3 text-xs leading-relaxed dark:bg-slate-950", className)}>
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Details({ summary, children }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button className="text-xs text-blue-700 underline dark:text-blue-300" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? "hide" : summary}
      </button>
      {open && <div className="mt-2">{children}</div>}
    </div>
  );
}

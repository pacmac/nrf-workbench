import { useState } from "react";

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

export function Card({ title, aside, children, className }) {
  return (
    <section className={cx("rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900", className)}>
      {(title || aside) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </section>
  );
}

export function Button({ tone = "primary", className, ...props }) {
  const styles = {
    primary: "bg-blue-600 text-white hover:bg-blue-700 dark:bg-blue-500 dark:hover:bg-blue-400 dark:text-slate-950",
    danger: "bg-red-600 text-white hover:bg-red-700 dark:bg-red-500 dark:hover:bg-red-400 dark:text-slate-950",
    quiet: "bg-slate-100 text-slate-800 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700",
  };
  return (
    <button
      className={cx("min-h-10 rounded-lg px-3 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50", styles[tone], className)}
      {...props}
    />
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

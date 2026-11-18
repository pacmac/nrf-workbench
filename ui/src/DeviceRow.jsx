import { useContext } from "react";
import { KIND_TEXT, STATE_TEXT } from "./overview.js";
import { Lamp, NavContext, cx } from "./ui.jsx";

// What one device row says, in one line under its name: what it is wired to, why it needs attention, or that it is not there.
export function deviceLine(d) {
  if (d.state === "attention") return d.why;
  if (d.state === "absent") return d.connections && d.connections.length ? `not attached, wired as ${d.connections.join(", ")}` : "not attached";
  if (d.kind === "nrf") {
    // The serial is what tells two identical boards apart, and the mode matters: a board in its bootloader is waiting for a package.
    const bits = [d.serial];
    if (d.mode === "bootloader") bits.push("in its bootloader, waiting for a package");
    if (d.connections && d.connections.length) bits.push(`wired as ${d.connections.join(", ")}`);
    return bits.join(", ");
  }
  if (d.connections && d.connections.length) return `wired as ${d.connections.join(", ")}`;
  if (d.why) return d.why;
  return d.product && d.product !== d.label ? d.product : "";
}

// One device, wherever it hangs: nRF board, ESP32 board or adapter. The lamp is the state (shape first), the row is a link to its detail.
export default function DeviceRow({ d, onOpen, open, children }) {
  const nav = useContext(NavContext);
  const line = deviceLine(d);
  return (
    <li>
      <button
        type="button"
        aria-expanded={onOpen ? Boolean(open) : undefined}
        onClick={() => (onOpen ? onOpen(d) : nav.go(d.page))}
        className={cx("flex w-full min-h-14 items-center gap-3 px-1 py-2 text-left hover:bg-slate-50 dark:hover:bg-slate-800/60", d.state === "absent" && "text-slate-600 dark:text-slate-400")}
      >
        <Lamp state={d.state} size={16} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[15px] font-medium">{d.label}</span>
            <span className="text-[13px] text-slate-600 dark:text-slate-400">{d.model && d.kind === "nrf" ? `${KIND_TEXT[d.kind]}, ${d.model}` : KIND_TEXT[d.kind]}</span>
          </span>
          {line && <span className="block text-[13px] text-slate-600 dark:text-slate-400">{line}</span>}
        </span>
        <span className={cx("shrink-0 text-[13px]", d.state === "attention" ? "font-medium text-amber-700 dark:text-amber-300" : "text-slate-600 dark:text-slate-400")}>{STATE_TEXT[d.state]}</span>
      </button>
      {open && children && <div className="border-t border-slate-200 bg-slate-50 px-3 py-3 dark:border-slate-800 dark:bg-slate-950/40">{children}</div>}
    </li>
  );
}

import { Button, Field, inputClass } from "../ui.jsx";
import { si } from "./format.js";
import { EMPTY_TRIGGER, isChanged, triggerArgs } from "./trigger.js";

// The trigger: what counts as a wake (docs/specs/ppk2-stages.md, "The wake profile"; docs/specs/meter-page.md, "Events"). Like a scope's trigger it is set before a capture, or changed during
// one, and it decides which stretches the power profiler keeps at its full 100,000 samples a second. It is NOT needed to name events, which are marked afterwards on whatever was kept.
// A blank field is left to the data, so the same page works for firmware nobody here has seen. Levels are typed in µA and times in seconds.

export function Trigger({ values, onChange, found, running, onApply, busy }) {
  const set = (key) => (e) => onChange({ ...values, [key]: e.target.value });
  const { problems } = triggerArgs(values);
  const sleepText = found && found.sleep_a ? si(found.sleep_a, "A") : null;
  const awakeText = found && found.awake_a ? si(found.awake_a, "A") : null;
  const changed = isChanged(values);
  return (
    <details className="rounded-md border border-slate-200 dark:border-slate-800" data-testid="trigger-details">
      <summary className="min-h-10 cursor-pointer px-3 py-2 text-[15px] font-medium">The trigger: what counts as a wake{changed ? " (changed)" : ""}</summary>
      <div className="space-y-3 p-3 pt-1">
        <p className="text-[13px] text-slate-600 dark:text-slate-400">
          A wake is any stretch where the board's average current over 0.2 s is above the awake level. The trigger decides which stretches are kept at full rate: it is not needed to name events, which you
          mark afterwards. Leave a field blank to find it from the data.
        </p>
        <p className="text-[13px]" aria-label="Found from the data" role="status">
          {sleepText ? `In force now: asleep at ${sleepText}, awake over ${awakeText}.` : "No sleep level yet: it is found from the first 4 seconds of a session."}
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-40"><Field label="Awake above, µA" hint="blank: twice the sleep level"><input className={inputClass} inputMode="decimal" value={values.awake} onChange={set("awake")} aria-label="Awake above, in microamps" /></Field></div>
          <div className="w-40"><Field label="Sleep level, µA" hint="blank: found"><input className={inputClass} inputMode="decimal" value={values.sleep} onChange={set("sleep")} aria-label="Sleep level, in microamps" /></Field></div>
          <div className="w-40"><Field label="Wake ends after, s" hint="blank: 0.2 s"><input className={inputClass} inputMode="decimal" value={values.hold} onChange={set("hold")} aria-label="A wake ends after this many seconds asleep" /></Field></div>
          <div className="w-40"><Field label="Sleep kept before, s" hint="blank: 0.2 s"><input className={inputClass} inputMode="decimal" value={values.lead} onChange={set("lead")} aria-label="Seconds of sleep kept before a wake" /></Field></div>
          <div className="w-40"><Field label="Longest wake, s" hint="blank: 40 s, up to 120"><input className={inputClass} inputMode="decimal" value={values.max} onChange={set("max")} aria-label="Longest wake kept whole, in seconds" /></Field></div>
        </div>
        {problems.length > 0 && <p role="note" className="text-[13px] text-amber-800 dark:text-amber-300">{problems.join(". ")}.</p>}
        <div className="flex flex-wrap gap-2">
          <Button tone="quiet" onClick={() => onChange(EMPTY_TRIGGER)} disabled={!changed}>Find everything from the data</Button>
          {running && <Button disabled={busy || problems.length > 0} onClick={onApply} aria-label="Apply the trigger to the running session">Apply to the running session</Button>}
        </div>
        {!running && <p className="text-xs text-slate-600 dark:text-slate-400">It is used by the next Watch or Record. While one runs, it can be changed here and applied from the next block.</p>}
      </div>
    </details>
  );
}

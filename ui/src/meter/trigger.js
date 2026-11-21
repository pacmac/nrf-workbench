// The trigger's fields as typed on the page, turned into the wake_* arguments of meter.watch, meter.record and meter.wake_set (docs/specs/ppk2-stages.md, "The wake profile"). Levels are typed
// in µA and sent in amps; times are seconds. A blank field is left out, which leaves it to the data. Pure, so it is tested under Node (tests/dashboard/test_meter_trigger.py).

export const EMPTY_TRIGGER = { awake: "", sleep: "", hold: "", lead: "", max: "" };

export function triggerArgs(v) {
  const args = {};
  const problems = [];
  const put = (key, text, scale, label) => {
    const t = String(text).trim();
    if (t === "") return;
    const n = Number(t);
    if (!Number.isFinite(n)) problems.push(`${label} is not a number`);
    else args[key] = n * scale;
  };
  put("wake_awake_a", v.awake, 1e-6, "Awake above");
  put("wake_sleep_a", v.sleep, 1e-6, "Sleep level");
  put("wake_hold_s", v.hold, 1, "A wake ends after");
  put("wake_lead_s", v.lead, 1, "Sleep kept before it");
  put("wake_max_s", v.max, 1, "Longest wake");
  return { args, problems };
}

export const isChanged = (v) => Object.values(v).some((x) => String(x).trim() !== "");

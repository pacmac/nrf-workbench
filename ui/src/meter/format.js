// Words for a meter result, kept out of the page so they can be read as plain functions. A number is shown with its SI prefix (1.717 mA, not 0.00171706).

const PREFIXES = [[1e9, "G"], [1e6, "M"], [1e3, "k"], [1, ""], [1e-3, "m"], [1e-6, "µ"], [1e-9, "n"], [1e-12, "p"]];

// `value` with the unit and the prefix that leaves one to three digits before the point, four significant digits.
export function si(value, unit) {
  if (value == null || !Number.isFinite(value)) return "";
  if (value === 0) return `0 ${unit || ""}`.trim();
  const abs = Math.abs(value);
  const [scale, prefix] = PREFIXES.find(([s]) => abs >= s) || PREFIXES[PREFIXES.length - 1];
  const text = Number((value / scale).toPrecision(4)).toString();
  return `${text} ${prefix}${unit || ""}`.trim();
}

// The sentence over a result: what was asked, in the meter's own terms.
export function headline(command, d) {
  if (!d) return "";
  const unit = d.unit || "";
  if (command === "meter.show") {
    const mode = d.function ? `set to ${d.function}` : "its setting is unknown";
    const range = d.range != null ? `, range ${si(d.range, unit)}${d.autorange ? " (auto)" : ""}` : "";
    return `${String(d.identity).split(",").join(", ")}: ${mode}${range}`;      // the meter's comma-joined id has no spaces, so it would not wrap
  }
  if (command === "ppk2.show" || command === "ppk2.mode" || command === "ppk2.supply") return `Power profiler ${d.serial}: ${unitSentence(d)}`;
  if (command === "meter.read") return d.overload ? `Overload on the ${si(d.range, unit)} range` : (unit ? si(d.value, unit) : `${d.value} (unit not known for ${d.function})`);
  if (command === "meter.sample") {
    if (d.mean == null) return `${d.n} readings, all overload`;
    return `${d.n} readings over ${d.duration_s} s: mean ${si(d.mean, unit)}, from ${si(d.min, unit)} to ${si(d.max, unit)}`;
  }
  return "";
}

// A polyline for a chart `width` by `height`: the readings in order along x by their arrival time, y scaled to their own range. Overloads are left out.
// {line, lo, hi, axisLo, axisHi}: lo and hi are the readings' own lowest and highest, axisLo and axisHi the ends of the vertical axis (zero is always on it),
// all as text. null when there is nothing to draw. An all-zero series is drawn in the middle.
export function series(points, width, height, unit = "") {
  const good = (points || []).filter((p) => Math.abs(p.value) < 9.9e37);
  if (good.length < 2) return null;
  const lo = Math.min(...good.map((p) => p.value));
  const hi = Math.max(...good.map((p) => p.value));
  // The axis always includes zero. A chart scaled to its own range draws a 0.03% wobble (6.599 to 6.601 mA) as a mountain, which says
  // something the readings do not: with zero on the axis a steady current is a flat line and a real spike stands out.
  const axisLo = Math.min(0, lo);
  const axisHi = Math.max(0, hi);
  const t0 = good[0].t_s;
  const span = good[good.length - 1].t_s - t0;
  const pad = 4;
  const y = (v) => (axisHi === axisLo ? height / 2 : height - pad - ((v - axisLo) / (axisHi - axisLo)) * (height - 2 * pad));
  // x is the arrival time. Readings that all arrived inside the clock's resolution (a fast meter setting) share one time, and a line with
  // no width says nothing, so they are spread evenly in the order they came.
  const x = (p, i) => ((span > 0 ? (p.t_s - t0) / span : i / (good.length - 1)) * (width - 2 * pad) + pad);
  const line = good.map((p, i) => `${x(p, i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  return { line, lo: si(lo, unit), hi: si(hi, unit), axisLo: si(axisLo, unit), axisHi: si(axisHi, unit) };
}

// Seconds as a clock: 252 -> "4:12", 3725 -> "1:02:05". Event-driven: it is shown from a reading's own time, never from a ticking timer.
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

// What the power profiler is set to, from {mode, vdd_mv, supply}: "source mode, output on at 4000 mV". The unit does not say whether its output is on: "off" and "on" are only what this
// service last switched it to, and it forgets that when it restarts. Used by the idle card and, from the session's facts, while a session holds the unit and it cannot be asked.
export function unitSentence(d) {
  const mode = d.mode === "source" ? "source mode" : d.mode === "ampere" ? "ampere mode" : "mode not known";
  const output = d.mode === "ampere" ? "" : d.supply === "on" ? `, output on at ${d.vdd_mv} mV` : d.supply === "off" ? `, output off (${d.vdd_mv} mV when on)`
    : `, output state unknown (${d.vdd_mv} mV when on)`;
  return `${mode}${output}`;
}

// A duration for a person: 90 -> "1 min 30 s", 3600 -> "1 h", 43200 -> "12 h".
export function words(seconds) {
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  return `${Math.floor(s / 3600)} h${Math.floor((s % 3600) / 60) ? ` ${Math.floor((s % 3600) / 60)} min` : ""}`;
}

// A short time for a person: 0.4 -> "0.4 s", 12 -> "12 s", 90 -> "1 min 30 s".
export const lapse = (s) => (s < 10 ? `${Number(s.toFixed(s < 1 ? 2 : 1))} s` : words(s));

// Charge from coulombs (1 mAh = 3.6 C): in mAh from 0.1 mAh, µAh from 0.01 µAh, nAh below; "—" for nothing to show.
export function chargeText(coulombs) {
  if (coulombs === null || coulombs === undefined || !Number.isFinite(coulombs)) return "—";
  const mah = coulombs / 3.6;
  if (Math.abs(mah) >= 0.1) return `${Number(mah.toPrecision(3))} mAh`;
  if (Math.abs(mah) >= 1e-5) return `${Number((mah * 1000).toPrecision(3))} µAh`;
  return `${Number((mah * 1e6).toPrecision(3))} nAh`;
}

// Energy from coulombs and volts (1 mWh = 3.6 J, and joules = coulombs x volts): mWh from 0.1 mWh, µWh from 0.01 µWh, nWh below.
export function energyText(coulombs, volts) {
  if (!Number.isFinite(coulombs) || !(volts > 0)) return "—";
  const mwh = (coulombs * volts) / 3.6;
  if (Math.abs(mwh) >= 0.1) return `${Number(mwh.toPrecision(3))} mWh`;
  if (Math.abs(mwh) >= 1e-5) return `${Number((mwh * 1000).toPrecision(3))} µWh`;
  return `${Number((mwh * 1e6).toPrecision(3))} nWh`;
}

// How long a battery of `capacityMah` lasts at an average (the whole recording's, or one wake/sleep cycle's: cycle.js) of `meanAmps`: hours, or days from 48 h. "" when either is missing or the draw is zero.
export function batteryLife(meanAmps, capacityMah) {
  if (!(meanAmps > 0) || !(capacityMah > 0)) return "";
  const hours = capacityMah / (meanAmps * 1000);
  if (hours < 48) return `${Number(hours.toPrecision(3))} hours`;
  const days = hours / 24;
  return days >= 365 * 2 ? `${Number((days / 365).toPrecision(3))} years` : `${Number(days.toPrecision(3))} days`;
}

// The chart of a session's envelope: a band from each bucket's lowest to its highest reading and a line through the means. Zero is always on the vertical axis
// (a steady current is a flat band, a real spike stands out). x is time. Returns null when there is nothing to draw.
// `marks` are times to draw a vertical line at (the starts of the wakes found); `marks` in the answer are their x positions.
// `spans` are {t0, t1} stretches to shade (the wakes found); `shades` in the answer are {x, w}, at least 2 wide so a short wake can be seen.
export function bandChart(buckets, width, height, unit = "", marks = [], spans = []) {
  if (!buckets || buckets.length === 0) return null;
  const lo = Math.min(...buckets.map((b) => b.min));
  const hi = Math.max(...buckets.map((b) => b.max));
  const axisLo = Math.min(0, lo);
  const axisHi = Math.max(0, hi);
  const pad = 4;
  const t0 = buckets[0].t0;
  const span = buckets[buckets.length - 1].t1 - t0;
  const y = (v) => (axisHi === axisLo ? height / 2 : height - pad - ((v - axisLo) / (axisHi - axisLo)) * (height - 2 * pad));
  const x = (t, i) => ((span > 0 ? (t - t0) / span : buckets.length > 1 ? i / (buckets.length - 1) : 0.5) * (width - 2 * pad) + pad);
  const mid = (b, i) => x((b.t0 + b.t1) / 2, i);
  const top = buckets.map((b, i) => `${mid(b, i).toFixed(1)},${y(b.max).toFixed(1)}`);
  const bottom = buckets.map((b, i) => `${mid(b, i).toFixed(1)},${y(b.min).toFixed(1)}`).reverse();
  const mean = buckets.map((b, i) => `${mid(b, i).toFixed(1)},${y(b.mean).toFixed(1)}`).join(" ");
  const at = marks.filter((t) => t >= t0 && t <= t0 + span).map((t) => Number(x(t, 0).toFixed(1)));
  const shades = span > 0 ? spans.filter((s) => s.t1 >= t0 && s.t0 <= t0 + span).map((s) => {
    const a = x(Math.max(s.t0, t0), 0);
    const b = x(Math.min(s.t1, t0 + span), 0);
    return { x: Number(a.toFixed(1)), w: Number(Math.max(2, b - a).toFixed(1)) };
  }) : [];
  return { band: [...top, ...bottom].join(" "), mean, marks: at, shades, axisLo: si(axisLo, unit), axisHi: si(axisHi, unit), lo: si(lo, unit), hi: si(hi, unit), span };
}

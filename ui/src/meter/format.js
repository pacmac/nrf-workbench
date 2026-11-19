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

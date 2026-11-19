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

// Seconds as a clock: 252 -> "4:12", 3725 -> "1:02:05". Event-driven: it is shown from a reading's own time, never from a ticking timer.
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const two = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

// A duration for a person: 90 -> "1 min 30 s", 3600 -> "1 h", 43200 -> "12 h".
export function words(seconds) {
  const s = Math.round(seconds);
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ""}`;
  return `${Math.floor(s / 3600)} h${Math.floor((s % 3600) / 60) ? ` ${Math.floor((s % 3600) / 60)} min` : ""}`;
}

// How long a battery of `capacityMah` lasts at an average of `meanAmps`: hours, or days from 48 h. "" when either is missing or the draw is zero.
export function batteryLife(meanAmps, capacityMah) {
  if (!(meanAmps > 0) || !(capacityMah > 0)) return "";
  const hours = capacityMah / (meanAmps * 1000);
  if (hours < 48) return `${Number(hours.toPrecision(3))} hours`;
  const days = hours / 24;
  return days >= 365 * 2 ? `${Number((days / 365).toPrecision(3))} years` : `${Number(days.toPrecision(3))} days`;
}

// The chart of a session's envelope: a band from each bucket's lowest to its highest reading and a line through the means. Zero is always on the vertical axis
// (a steady current is a flat band, a real spike stands out). x is time. Returns null when there is nothing to draw.
export function bandChart(buckets, width, height, unit = "") {
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
  return { band: [...top, ...bottom].join(" "), mean, axisLo: si(axisLo, unit), axisHi: si(axisHi, unit), lo: si(lo, unit), hi: si(hi, unit), span };
}

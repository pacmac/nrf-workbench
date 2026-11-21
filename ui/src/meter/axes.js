// The scope's axes and window maths (docs/specs/meter-page.md, "Live and replay"): the numbers a scope shows beside its trace, how a window zooms and pans, and what is under the pointer.
// Pure functions, so they are tested under Node (tests/dashboard/test_meter_axes.py) and the component only draws.

import { si } from "./format.js";

// A step of 1, 2 or 5 times a power of ten that gives about `count` intervals across `span`.
export function niceStep(span, count) {
  if (!(span > 0) || !(count > 0)) return 0;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw * (1 - 1e-12));
}

// Round-numbered values across [lo, hi], each with its label. `step` is the spacing used.
export function niceTicks(lo, hi, count = 5) {
  const step = niceStep(hi - lo, count);
  if (!step) return { step: 0, values: [] };
  const values = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) values.push(Number(v.toPrecision(12)));
  return { step, values };
}

// The labelled ticks of the vertical axis. `lo` and `hi` are the axis's own ends (amps, say); `y(v)` is the chart's position for a value. Linear: round numbers. Log: each power of ten
// that is in range, and 2 and 5 times it when there are fewer than three powers, so a narrow log axis still has numbers on it.
export function yTicks(lo, hi, y, unit, { log = false } = {}) {
  if (!(hi > lo)) return [];
  let values = [];
  if (log && lo > 0) {
    const a = Math.floor(Math.log10(lo) + 1e-9);
    const b = Math.ceil(Math.log10(hi) - 1e-9);
    const mults = b - a < 3 ? [1, 2, 5] : [1];
    for (let e = a; e <= b; e++) for (const m of mults) values.push(Number((m * 10 ** e).toPrecision(12)));
    values = values.filter((v) => v >= lo * (1 - 1e-9) && v <= hi * (1 + 1e-9));
  } else {
    values = niceTicks(lo, hi, 4).values;
  }
  return values.map((value) => ({ value, y: y(value), label: si(value, unit) }));
}

// Time labels. A scope's time is read from where the recording began: m:ss for minutes, h:mm:ss for hours, seconds with decimals for a few seconds, ms under a second.
export function clockLabel(t, span) {
  const sign = t < 0 ? "-" : "";
  const a = Math.abs(t);
  if (span < 1) return `${sign}${Number((a * 1000).toPrecision(3))} ms`;
  if (span < 60) return `${sign}${Number(a.toFixed(span < 10 ? 2 : 1))} s`;
  const whole = Math.round(a);
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h > 0 ? `${sign}${h}:${pad(m)}:${pad(s)}` : `${sign}${m}:${pad(s)}`;
}

// A duration in words a person reads: "1.5 ms", "320 ms", "4.2 s", "1 min 40 s", "2 h 05 min".
export function durationText(s) {
  if (!Number.isFinite(s)) return "";
  if (s < 1) return `${Number((s * 1000).toPrecision(3))} ms`;
  if (s < 60) return `${Number(s.toPrecision(3))} s`;
  const whole = Math.round(s);
  if (whole < 3600) return `${Math.floor(whole / 60)} min ${String(whole % 60).padStart(2, "0")} s`;
  return `${Math.floor(whole / 3600)} h ${String(Math.floor((whole % 3600) / 60)).padStart(2, "0")} min`;
}

// ---- the window: {t0, t1} inside bounds {t0, t1} ----------------------------------------------------

export const MIN_SPAN_S = 1e-5;                      // one sample of the profiler: no zooming past what exists

// `win` held inside `bounds`, keeping its length when it can and never shorter than `floor` seconds.
export function clampWindow(win, bounds, floor = MIN_SPAN_S) {
  const whole = bounds.t1 - bounds.t0;
  let len = Math.max(floor, win.t1 - win.t0);
  if (len >= whole) return { t0: bounds.t0, t1: bounds.t1 };
  let t0 = win.t0;
  if (t0 < bounds.t0) t0 = bounds.t0;
  if (t0 + len > bounds.t1) t0 = bounds.t1 - len;
  return { t0, t1: t0 + len };
}

// Zoom by `factor` (2 halves the span, 0.5 doubles it) about the point a fraction `anchor` (0 to 1) across the window, so what is under the pointer stays under it.
export function zoomWindow(win, bounds, factor, anchor = 0.5, floor = MIN_SPAN_S) {
  const len = win.t1 - win.t0;
  const next = len / factor;
  const at = win.t0 + len * anchor;
  return clampWindow({ t0: at - next * anchor, t1: at + next * (1 - anchor) }, bounds, floor);
}

// Move by a fraction of the window's own length (positive is later).
export function panWindow(win, bounds, fraction, floor = MIN_SPAN_S) {
  const len = win.t1 - win.t0;
  return clampWindow({ t0: win.t0 + len * fraction, t1: win.t1 + len * fraction }, bounds, floor);
}

export const isWhole = (win, bounds) => win.t0 <= bounds.t0 + 1e-9 && win.t1 >= bounds.t1 - 1e-9;

// The lowest, highest, average and charge (mean x seconds) of the stretch `view`, from buckets in time order: each bucket counts for the part of it inside the stretch. null for a stretch with no bucket.
export function windowStats(buckets, view) {
  let lowest = Infinity;
  let highest = -Infinity;
  let seconds = 0;
  let charge = 0;
  for (const b of buckets || []) {
    const lo = Math.max(b.t0, view.t0);
    const hi = Math.min(b.t1, view.t1);
    if (!(hi > lo)) continue;
    if (b.min < lowest) lowest = b.min;
    if (b.max > highest) highest = b.max;
    seconds += hi - lo;
    charge += b.mean * (hi - lo);
  }
  return seconds > 0 ? { lowest, highest, mean: charge / seconds, charge, seconds } : null;
}

// The bucket under time `t` (the last that starts at or before it), or null outside them all. Buckets are in time order.
export function bucketAt(buckets, t) {
  if (!buckets || buckets.length === 0 || t < buckets[0].t0 || t > buckets[buckets.length - 1].t1) return null;
  let a = 0;
  let b = buckets.length - 1;
  while (a < b) {
    const m = (a + b + 1) >> 1;
    if (buckets[m].t0 <= t) a = m;
    else b = m - 1;
  }
  return buckets[a];
}

// What the pointer reads: the time and the three values of the bucket under it, in words.
export function readout(buckets, t, unit, span) {
  const b = bucketAt(buckets, t);
  if (!b) return null;
  return { t: clockLabel(t, span), lowest: si(b.min, unit), mean: si(b.mean, unit), highest: si(b.max, unit), width: durationText(b.t1 - b.t0) };
}

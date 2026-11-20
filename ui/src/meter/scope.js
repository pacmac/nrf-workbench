// The scope trace of one captured wake (docs/specs/ppk2-stages.md, "The scope view"): the exact envelope the Pi returns for a stretch of the wake, drawn as a band from the lowest
// to the highest sample of each point with the mean as a line, on a linear axis (zero at the bottom, the radio's shape) or a log axis (µA asleep and mA awake in one picture).
// Times are seconds from the wake's start, so the same stage is at the same place in every wake.

import { si } from "./format.js";

const PAD = 4;

// 0.0123 -> "12.3 ms", 1.5 -> "1.5 s": a stretch shorter than a second is read in milliseconds.
export function timeLabel(t, span) {
  if (Math.abs(span) < 1) return `${Number((t * 1000).toPrecision(3))} ms`;
  return `${Number(t.toPrecision(3))} s`;
}

// Round-numbered times to label: a step of 1, 2 or 5 times a power of ten that gives about `count` labels across [t0, t1].
export function ticks(t0, t1, count = 5) {
  const span = t1 - t0;
  if (!(span > 0)) return [];
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const out = [];
  for (let t = Math.ceil(t0 / step - 1e-9) * step; t <= t1 + step * 1e-9; t += step) out.push(Number(t.toPrecision(12)));
  return out;
}

export function scopeChart(buckets, width, height, { log = false, unit = "A" } = {}) {
  if (!buckets || buckets.length === 0) return null;
  const t0 = buckets[0].t0;
  const t1 = buckets[buckets.length - 1].t1;
  const span = t1 - t0;
  const lo = Math.min(...buckets.map((b) => b.min));
  const hi = Math.max(...buckets.map((b) => b.max));
  const positive = buckets.map((b) => b.min).filter((v) => v > 0);
  const useLog = log && positive.length > 0 && hi > 0;                       // a log axis needs something above zero to stand on
  const floor = useLog ? Math.min(...positive) : 0;
  const top = useLog ? (hi > floor ? hi : floor * 10) : Math.max(0, hi);
  const axisLo = useLog ? floor : Math.min(0, lo);
  const inner = height - 2 * PAD;
  const y = (v) => {
    if (useLog) {
      const f = (Math.log10(Math.min(Math.max(v, floor), top)) - Math.log10(floor)) / (Math.log10(top) - Math.log10(floor));
      return height - PAD - f * inner;
    }
    return top === axisLo ? height / 2 : height - PAD - ((v - axisLo) / (top - axisLo)) * inner;
  };
  const x = (t) => PAD + (span > 0 ? (t - t0) / span : 0.5) * (width - 2 * PAD);
  const mid = (b) => x((b.t0 + b.t1) / 2);
  const upper = buckets.map((b) => `${mid(b).toFixed(1)},${y(b.max).toFixed(1)}`);
  const lower = buckets.map((b) => `${mid(b).toFixed(1)},${y(b.min).toFixed(1)}`).reverse();
  return {
    band: [...upper, ...lower].join(" "),
    mean: buckets.map((b) => `${mid(b).toFixed(1)},${y(b.mean).toFixed(1)}`).join(" "),
    t0, t1, span, log: useLog,
    zero: t0 <= 0 && t1 >= 0 ? Number(x(0).toFixed(1)) : null,              // where the wake starts, when it is in view
    ticks: ticks(t0, t1).map((t) => ({ t, x: Number(x(t).toFixed(1)), label: timeLabel(t, span) })),
    bottom: si(axisLo, unit),
    top: si(top, unit),
    // the time at a horizontal position (in the chart's own units), for a drag to be turned back into a stretch
    timeAt: (px) => t0 + ((Math.min(Math.max(px, PAD), width - PAD) - PAD) / (width - 2 * PAD)) * span,
  };
}

// The geometry of the scope trace (docs/specs/meter-page.md, "Live and replay"): where each bucket, each tick and each marker falls in the chart's own units. A chart is W wide and H high;
// the plot is inside a pad of PAD on every side. `view` is the stretch shown, {t0, t1} in seconds on the recording's (or the session's) clock. Pure, so it is tested under Node.

import { clockLabel, niceTicks, yTicks } from "./axes.js";
import { si } from "./format.js";

export const W = 960;
export const H = 260;
export const PAD = 6;

export function plot(buckets, view, { log = false, unit = "A", width = W, height = H } = {}) {
  if (!buckets || buckets.length === 0 || !(view.t1 > view.t0)) return null;
  let lo = Infinity;
  let hi = -Infinity;
  let floor = Infinity;
  for (const b of buckets) {
    if (b.min < lo) lo = b.min;
    if (b.max > hi) hi = b.max;
    if (b.min > 0 && b.min < floor) floor = b.min;
  }
  const useLog = log && floor < Infinity && hi > 0;                             // a log axis needs something above zero to stand on
  const top = useLog ? (hi > floor ? hi : floor * 10) : Math.max(0, hi);
  const bottom = useLog ? floor : Math.min(0, lo);
  const inner = height - 2 * PAD;
  const span = view.t1 - view.t0;
  const y = (v) => {
    if (useLog) {
      const f = (Math.log10(Math.min(Math.max(v, bottom), top)) - Math.log10(bottom)) / (Math.log10(top) - Math.log10(bottom));
      return height - PAD - f * inner;
    }
    return top === bottom ? height / 2 : height - PAD - ((v - bottom) / (top - bottom)) * inner;
  };
  const x = (t) => PAD + ((t - view.t0) / span) * (width - 2 * PAD);
  const inView = (b) => b.t1 > view.t0 && b.t0 < view.t1;
  const shown = buckets.filter(inView);
  const mid = (b) => x((Math.max(b.t0, view.t0) + Math.min(b.t1, view.t1)) / 2);
  const upper = shown.map((b) => `${mid(b).toFixed(1)},${y(b.max).toFixed(1)}`);
  const lower = shown.map((b) => `${mid(b).toFixed(1)},${y(b.min).toFixed(1)}`).reverse();
  const xt = niceTicks(view.t0, view.t1, 6);
  return {
    band: [...upper, ...lower].join(" "),
    mean: shown.map((b) => `${mid(b).toFixed(1)},${y(b.mean).toFixed(1)}`).join(" "),
    log: useLog, bottom, top, span,
    xTicks: xt.values.map((t) => ({ t, x: Number(x(t).toFixed(1)), label: clockLabel(t, span) })),
    yTicks: yTicks(bottom, top, (v) => Number(y(v).toFixed(1)), unit, { log: useLog }),
    // the chart's own units for a time, held inside the plot, so a marker or a shaded stretch never leaves it
    at: (t) => Number(Math.min(Math.max(x(t), PAD), width - PAD).toFixed(1)),
    // the time at a horizontal position in the chart's own units
    timeAt: (px) => view.t0 + ((Math.min(Math.max(px, PAD), width - PAD) - PAD) / (width - 2 * PAD)) * span,
    bottomText: si(bottom, unit), topText: si(top, unit),
  };
}

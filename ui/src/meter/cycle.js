// The wake/sleep cycle of a recording, found in its envelope (docs/specs/meter-page.md, "Battery life"). A device that sleeps, wakes and sleeps again is rated by the mean
// current over WHOLE cycles, not over a window that may hold half of one. The rules are in the spec; each is derived there.
//
// `buckets` is Envelope.snapshot(): {t0, t1, min, max, mean, n, k}, oldest first, equally wide except the last (still filling).
// Returns {found: false, reason, wakes} or {found: true, wakes, cycles, starts, mean, charge, duration, period, shortest, longest, regular, sleep, peak, level}.

// A wake must at least double the sleep current: noise on a steady load is a few per cent, a radio burst is a thousand times.
export const WAKE_FACTOR = 2;

function median(values) {
  const s = values.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export function findCycle(buckets) {
  if (!buckets || buckets.length < 3) return { found: false, reason: "short", wakes: 0 };
  const sleep = median(buckets.map((b) => b.mean));
  const peak = Math.max(...buckets.map((b) => b.max));
  if (!(sleep > 0)) return { found: false, reason: "no_sleep", wakes: 0 };
  if (peak < WAKE_FACTOR * sleep) return { found: false, reason: "steady", wakes: 0, sleep, peak };
  const level = sleep + (peak - sleep) / 2;

  // Runs of buckets whose highest sample is above the level: the highest, not the mean, so a burst shorter than a bucket counts.
  const runs = [];
  buckets.forEach((b, i) => {
    if (b.max > level) {
      const last = runs[runs.length - 1];
      if (last && last.i1 === i - 1) last.i1 = i;
      else runs.push({ i0: i, i1: i });
    }
  });

  // One wake may be several bursts (a CPU wake, then the radio): runs closer than half the longest gap are one wake.
  const gaps = runs.slice(1).map((r, j) => buckets[r.i0].t0 - buckets[runs[j].i1].t1);
  const split = gaps.length ? Math.max(...gaps) / 2 : 0;
  const wakes = [];
  runs.forEach((r, j) => {
    if (j > 0 && gaps[j - 1] < split) wakes[wakes.length - 1].i1 = r.i1;
    else wakes.push({ i0: r.i0, i1: r.i1 });
  });
  if (wakes.length < 2) return { found: false, reason: "wakes", wakes: wakes.length, sleep, peak, level };

  // The mean from the start of the first wake to the start of the last: each bucket's mean times the time to the next bucket, so it is exact for what was sampled.
  const a = wakes[0].i0;
  const b = wakes[wakes.length - 1].i0;
  let charge = 0;
  for (let i = a; i < b; i++) charge += buckets[i].mean * (buckets[i + 1].t0 - buckets[i].t0);
  const duration = buckets[b].t0 - buckets[a].t0;
  const starts = wakes.map((w) => buckets[w.i0].t0);
  const between = starts.slice(1).map((t, j) => t - starts[j]);
  let stride = 0;
  for (let i = 0; i + 1 < buckets.length; i++) stride = Math.max(stride, buckets[i + 1].t0 - buckets[i].t0);
  const shortest = Math.min(...between);
  const longest = Math.max(...between);
  const cycles = wakes.length - 1;

  // The table (docs/specs/meter-page.md, "The cycle table"): a wake is its buckets, from the start of the first to the start of the bucket after the last, so its time is placed
  // to one bucket at each end and its charge is exact for what was sampled. The last wake starts at b, outside the span, so only the first `cycles` wakes count.
  const next = (i) => (i + 1 < buckets.length ? buckets[i + 1].t0 : buckets[i].t1);
  const spans = wakes.map((w) => ({ t0: buckets[w.i0].t0, t1: next(w.i1), buckets: w.i1 - w.i0 + 1 }));
  const inside = wakes.slice(0, cycles);
  const parts = inside.map((w) => {
    let q = 0;
    let top = -Infinity;
    for (let i = w.i0; i <= w.i1; i++) {
      q += buckets[i].mean * (next(i) - buckets[i].t0);
      top = Math.max(top, buckets[i].max);
    }
    return { time: next(w.i1) - buckets[w.i0].t0, charge: q, peak: top, buckets: w.i1 - w.i0 + 1 };
  });
  const wakeTime = parts.reduce((s, p) => s + p.time, 0);
  const wakeCharge = parts.reduce((s, p) => s + p.charge, 0);
  const sleepTime = duration - wakeTime;
  const sleepCharge = charge - wakeCharge;
  const phases = {
    wake: {
      time: wakeTime / cycles, charge: wakeCharge / cycles, mean: wakeCharge / wakeTime, peak: Math.max(...parts.map((p) => p.peak)),
      shortest: Math.min(...parts.map((p) => p.time)), longest: Math.max(...parts.map((p) => p.time)),
      widest: Math.max(...parts.map((p) => p.buckets)),           // 1: every wake filled a single bucket, so none is resolved
    },
    sleep: { time: sleepTime / cycles, charge: sleepCharge / cycles, mean: sleepTime > 0 ? sleepCharge / sleepTime : null },
  };
  return {
    found: true, wakes: wakes.length, cycles, starts, charge, duration, mean: charge / duration, period: duration / cycles,
    shortest, longest, regular: longest - shortest <= 2 * stride,         // each end of a gap is placed to one bucket
    sleep, peak, level, stride, spans, phases,
  };
}

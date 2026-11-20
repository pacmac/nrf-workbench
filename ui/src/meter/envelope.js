// A whole session's readings in bounded memory: the same algorithm as nrf_workbench/instruments/envelope.py (docs/specs/meter-page.md, "The envelope"), so what the
// page draws from the live feed equals what the server holds. tests/dashboard/test_meter_envelope.py runs both on the same readings and compares every bucket.
//
// A list of buckets {t0, t1, min, max, sum, n}. One reading per bucket to begin with; at MAX_BUCKETS neighbours are merged in pairs and the width doubles. A merge keeps
// the lowest, the highest, the count and the sum exactly, so a short spike stays the edge of its band however long the session runs.

export const MAX_BUCKETS = 2000;

export class Envelope {
  constructor(max = MAX_BUCKETS) {
    if (!Number.isInteger(max) || max < 2) throw new Error("an envelope needs room for at least 2 buckets");
    this.max = max;
    this.width = 1;
    this.buckets = [];
    this.cur = null;
  }

  // One point: a multimeter reading (n 1), or a power profiler block standing for n samples of mean `value`, lowest lo, highest hi. `width` counts points (k), not samples.
  add(t, value, lo = null, hi = null, n = 1) {
    const low = lo === null || lo === undefined ? value : lo;
    const high = hi === null || hi === undefined ? value : hi;
    const c = this.cur;
    if (c === null) {
      this.cur = { t0: t, t1: t, min: low, max: high, sum: value * n, n, k: 1 };
    } else {
      c.t1 = t;
      c.min = Math.min(c.min, low);
      c.max = Math.max(c.max, high);
      c.sum += value * n;
      c.n += n;
      c.k += 1;
    }
    if (this.cur.k >= this.width) {
      this.buckets.push(this.cur);
      this.cur = null;
      if (this.buckets.length >= this.max) this.merge();
    }
  }

  merge() {
    const b = this.buckets;
    const merged = [];
    for (let i = 0; i + 1 < b.length; i += 2) {
      const x = b[i];
      const y = b[i + 1];
      merged.push({ t0: x.t0, t1: y.t1, min: Math.min(x.min, y.min), max: Math.max(x.max, y.max), sum: x.sum + y.sum, n: x.n + y.n, k: x.k + y.k });
    }
    if (b.length % 2) merged.push(b[b.length - 1]);          // an odd one out stays and is merged on the next round
    this.buckets = merged;
    this.width *= 2;
  }

  // Each bucket as {t0, t1, min, max, mean, n}, the one still filling last.
  snapshot() {
    const all = this.cur !== null ? [...this.buckets, this.cur] : this.buckets;
    return all.map((b) => ({ t0: b.t0, t1: b.t1, min: b.min, max: b.max, mean: b.sum / b.n, n: b.n, k: b.k }));
  }

  // Start from a snapshot the server sent (meter.profile): its buckets already carry their mean, so the sum is mean x n.
  static from(buckets, max = MAX_BUCKETS) {
    const e = new Envelope(max);
    const points = (b) => (b.k === undefined ? b.n : b.k);       // an older server sent no k: then a bucket's n was its points
    e.buckets = buckets.map((b) => ({ t0: b.t0, t1: b.t1, min: b.min, max: b.max, sum: b.mean * b.n, n: b.n, k: points(b) }));
    // The server's width is not sent: it is the most points a bucket holds, which is what its next bucket will hold.
    e.width = buckets.reduce((w, b) => Math.max(w, points(b)), 1);
    // The last bucket of a snapshot may still be filling (fewer points than the width): carry on filling it.
    const last = e.buckets[e.buckets.length - 1];
    if (last && last.k < e.width) e.cur = e.buckets.pop();
    return e;
  }
}

// readings, min, max, mean and, for a current, the charge: coulombs by the trapezoid rule over consecutive valid readings (mAh = coulombs / 3.6).
export class Stats {
  constructor() {
    this.n = 0;
    this.min = null;
    this.max = null;
    this.sum = 0;
    this.charge = 0;
    this.overloads = 0;
    this.last = null;
  }

  // A point (n 1, the charge by the trapezoid rule) or a block of n samples dt seconds long (the charge is its mean times dt).
  add(t, value, lo = null, hi = null, n = 1, dt = null) {
    const low = lo === null || lo === undefined ? value : lo;
    const high = hi === null || hi === undefined ? value : hi;
    this.n += n;
    this.min = this.min === null ? low : Math.min(this.min, low);
    this.max = this.max === null ? high : Math.max(this.max, high);
    this.sum += value * n;
    if (dt !== null && dt !== undefined) this.charge += value * dt;
    else if (this.last !== null) this.charge += ((value + this.last[1]) / 2) * (t - this.last[0]);
    this.last = [t, value];
  }

  overload() {
    this.overloads += 1;
  }

  // Start from the server's numbers (a profile's stats), and carry on from the last reading it counted.
  static from(s, last) {
    const x = new Stats();
    x.n = s.readings || 0;
    x.min = s.min === undefined ? null : s.min;
    x.max = s.max === undefined ? null : s.max;
    x.sum = (s.mean || 0) * x.n;
    x.charge = s.charge_c || 0;
    x.overloads = s.overloads || 0;
    x.last = last || null;
    return x;
  }

  toObject(current) {
    const out = { readings: this.n, overloads: this.overloads, min: this.min, max: this.max, mean: this.n ? this.sum / this.n : null };
    if (current) {
      out.charge_c = this.charge;
      out.charge_mah = this.charge / 3.6;
    }
    return out;
  }
}

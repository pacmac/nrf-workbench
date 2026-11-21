"""One row per wake of a recording (docs/specs/power-workbench.md, "Commands", `meter.wake_table`).

A wake is found by the rule the live session used, because this REPLAYS the recording's 0.2 s rows through the same `capture.Capturer` the session fed (with the wake profile the
sidecar says, a level found from the data being found again as it was then): the table, `meter.captures` and stop-after-wakes cannot disagree about what a wake is. The rows have no
samples to keep, so the Capturer is fed empty blocks and only counts: a capture's `t0` is the index of its first row.

A wake has two lengths: `awake_s`, the first block over the awake level to the last, and `capture_s`, which adds the lead-in block and the tail block. Its charge, mean and peak are those
of the awake blocks. Whether each wake is an outlier is a modified z-score of its awake length and its charge across the recording's whole wakes.
"""
from . import capture

Z_FLAG = 3.5                      # Iglewicz and Hoaglin's cut-off for a modified z-score: the published value, not tuned to this bench
Z_CONSTANT = 0.6745               # the 0.75 quantile of the standard normal: makes MAD / Z_CONSTANT an estimate of the standard deviation
MIN_WAKES = 4                     # a median and a spread of fewer than four values is not a spread
SCALE_FLOOR = 0.01                # the scale is at least 1 % of the median: wakes identical to the last digit give a MAD of 0, and noise of a part in a thousand must not become an outlier
COULOMBS_PER_MAH = 3.6


def _median(values):
    s = sorted(values)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def robust_z(values):
    """The modified z-score of each value against the median of all of them, or None with fewer than MIN_WAKES values. The scale is MAD / 0.6745, floored at SCALE_FLOOR of the median."""
    if len(values) < MIN_WAKES:
        return None
    med = _median(values)
    mad = _median([abs(v - med) for v in values])
    scale = max(mad / Z_CONSTANT, SCALE_FLOOR * abs(med))
    if scale == 0:
        return [0.0] * len(values)
    return [(v - med) / scale for v in values]


def _captures(trace, profile):
    """The captures a session with `profile` would have closed over this trace's rows, in order."""
    found = []
    cap = capture.Capturer(profile, keep=1)
    cap.on_close = found.append
    for i in range(trace.rows):
        cap.feed(b"", float(i), trace.means[i], trace.highs[i], 0)
    return found, cap


def build(trace, wake, saved=()):
    """{profile, wakes, summary} for a Trace. `wake` is the sidecar's wake profile (fields it leaves out are found from the data, as they were live); `saved` is
    [(n, t0, t1)] of the full-rate wakes the recording kept, so a row says whether it can be zoomed into."""
    profile = capture.WakeProfile(**dict((k, wake.get(k)) for k in ("sleep_a", "awake_a", "hold_s", "lead_s", "max_s")))
    caps, cap = _captures(trace, profile)
    rows = []
    for c in caps:
        i0 = int(c.t0)
        first, end = i0 + c.lead_blocks, i0 + c.blocks - c.tail_blocks
        secs = sum(trace.widths[i] for i in range(first, end))
        charge = sum(trace.means[i] * trace.widths[i] for i in range(first, end))
        start = trace.ends[first] - trace.widths[first]
        began = trace.ends[i0] - trace.widths[i0]                # where the capture's own first block begins: what a saved wake's t0 is on the same clock
        kept = next((n for n, t0, _t1 in saved if abs(t0 - began) < capture.BLOCK_S / 2.0), None)
        rows.append({"n": c.n, "start_s": start, "wall": trace.wall0 + start, "awake_s": c.awake_s, "capture_s": c.capture_s,
                     "charge_mah": charge / COULOMBS_PER_MAH, "mean_a": charge / secs if secs > 0 else None, "peak_a": max(trace.highs[first:end]),
                     "resolution": "full" if kept is not None else "0.2s", "capture": kept, "partial": c.partial, "truncated": c.truncated,
                     "outlier": None, "z": None})
    whole = [r for r in rows if not r["partial"] and not r["truncated"]]       # a wake cut at either end says nothing about how long or how large a whole one is
    za = robust_z([r["awake_s"] for r in whole])
    zc = robust_z([r["charge_mah"] for r in whole])
    if za is not None:
        for r, a, q in zip(whole, za, zc):
            r["z"] = {"awake_s": a, "charge_mah": q}
            r["outlier"] = abs(a) > Z_FLAG or abs(q) > Z_FLAG
    summary = {"wakes": len(rows), "whole": len(whole), "partial": sum(1 for r in rows if r["partial"]), "truncated": sum(1 for r in rows if r["truncated"]),
               "outliers": sum(1 for r in rows if r["outlier"]),
               "median_awake_s": _median([r["awake_s"] for r in whole]) if whole else None,
               "median_charge_mah": _median([r["charge_mah"] for r in whole]) if whole else None}
    return {"profile": profile.to_dict(), "wakes": rows, "summary": summary, "sleep_a": cap.sleep_a(), "awake_a": cap.awake_a()}

"""A recording's wake/sleep cycle and its battery life (docs/specs/power-workbench.md, "Commands", `meter.life`).

A device that sleeps, wakes and sleeps again is rated by the mean current over WHOLE cycles, from the start of its first wake to the start of its last, not over a window that may hold half of
one. What a wake is comes from `waketable.build`, which replays the recording through the same `capture.Capturer` the live session used, so this, `meter.wake_table`, `meter.captures` and
stop-after-wakes cannot disagree about when a wake starts or how long it is awake. Only whole wakes count: one cut by either end of the recording says nothing about a cycle.

The span is the first whole wake's start to the last's: `cycles` wakes' worth of time (one fewer than there are wakes), each cycle being a wake and the sleep after it. In it the wakes' awake
periods and charges are the Capturer's (the awake blocks), and the sleep is what is left, so the two phases add up to the span exactly.
"""
from . import capture, waketable

COULOMBS_PER_MAH = waketable.COULOMBS_PER_MAH
REGULAR_S = 2 * capture.BLOCK_S              # each end of a gap between starts is placed to one 0.2 s row, so two gaps can differ by two rows and the wakes still be as regular as they can be seen
MIN_WAKES = 2                                # a cycle is between two wakes


def _charge(trace, a, b):
    """Coulombs the trace's rows carry between a and b seconds. a and b are wakes' starts, and a wake starts at the start of a row, so both ends fall on row boundaries and every row is
    counted whole or not at all; the boundaries are compared to half a row, which separates a row from its neighbour and absorbs the float error of adding widths up."""
    half = capture.BLOCK_S / 2.0
    return sum(trace.means[i] * trace.widths[i] for i in range(trace.rows) if trace.ends[i] - trace.widths[i] >= a - half and trace.ends[i] <= b + half)


def life(trace, table, capacity_mah=None):
    """The cycle of a Trace from `table`, the answer of waketable.build for it: {found, reason, wakes, ...}. `capacity_mah` (above 0) gives the life in hours and days at the mean over whole cycles."""
    whole = [r for r in table["wakes"] if not r["partial"] and not r["truncated"]]
    out = {"found": False, "reason": None, "wakes": len(whole), "cycles": 0, "starts": [r["start_s"] for r in whole], "period_s": None, "duration_s": None, "mean_a": None,
           "charge_mah": None, "shortest_s": None, "longest_s": None, "regular": None, "sleep_a": table["sleep_a"], "awake_a": table["awake_a"], "peak_a": None,
           "phases": None, "capacity_mah": capacity_mah, "life_h": None, "life_days": None}
    if table["sleep_a"] is None:
        out["reason"] = "no_sleep"
        return out
    if len(whole) < MIN_WAKES:
        out["reason"] = "wakes"
        return out
    cycles = len(whole) - 1
    first, last = whole[0]["start_s"], whole[-1]["start_s"]
    duration = last - first
    gaps = [b["start_s"] - a["start_s"] for a, b in zip(whole, whole[1:])]
    charge = _charge(trace, first, last)
    inside = whole[:cycles]                                              # the last wake starts at the end of the span: its time and charge are the next cycle's
    wake_time = sum(r["awake_s"] for r in inside)
    wake_charge = sum(r["charge_mah"] for r in inside) * COULOMBS_PER_MAH
    sleep_time = duration - wake_time
    sleep_charge = charge - wake_charge
    mean = charge / duration
    out.update({
        "found": True, "reason": None, "cycles": cycles, "period_s": duration / cycles, "duration_s": duration, "mean_a": mean, "charge_mah": charge / COULOMBS_PER_MAH,
        "shortest_s": min(gaps), "longest_s": max(gaps), "regular": max(gaps) - min(gaps) <= REGULAR_S + 1e-9, "peak_a": max(r["peak_a"] for r in whole),
        "phases": {
            "wake": {"time_s": wake_time / cycles, "mean_a": wake_charge / wake_time if wake_time > 0 else None, "peak_a": max(r["peak_a"] for r in inside),
                     "charge_mah": wake_charge / COULOMBS_PER_MAH / cycles, "shortest_s": min(r["awake_s"] for r in inside), "longest_s": max(r["awake_s"] for r in inside)},
            "sleep": {"time_s": sleep_time / cycles, "mean_a": sleep_charge / sleep_time if sleep_time > 0 else None, "charge_mah": sleep_charge / COULOMBS_PER_MAH / cycles}}})
    if capacity_mah is not None and mean > 0:
        out["life_h"] = capacity_mah / (mean * 1000.0)                    # mAh over mA
        out["life_days"] = out["life_h"] / 24.0
    return out

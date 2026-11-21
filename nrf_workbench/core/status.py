"""The one mapping from a Result to a CLI exit code and an HTTP status (docs/specs/core.md).

Both come from here and nowhere else, so the CLI and the API cannot disagree about a refusal.

`CODES` is the CLOSED list of result codes with the class each belongs to. A new code is a spec change:
a test reads the table in core.md and fails if this list and that table differ in either direction, and
a second test fails if any code the package emits is missing here.
"""
from .result import EXIT, FAILED, OK, REFUSED, SUSPECT

CODES = {
    "ok": OK,
    # refused: nothing was attempted and nothing changed
    "lock_held": REFUSED, "lock_void": REFUSED, "node_absent": REFUSED, "node_two": REFUSED,
    "node_not_char": REFUSED, "unrecognised": REFUSED, "port_held": REFUSED, "serial_mismatch": REFUSED,
    "serial_unverifiable": REFUSED, "two_candidates": REFUSED, "protected": REFUSED,
    "bad_package": REFUSED, "wrong_softdevice": REFUSED, "firmware_unknown": REFUSED,
    "checksum_mismatch": REFUSED, "length_required": REFUSED, "store_full": REFUSED, "in_use": REFUSED,
    "bad_project": REFUSED, "project_unknown": REFUSED, "project_not_empty": REFUSED, "macro_unknown": REFUSED,
    "not_in_bootloader": REFUSED, "no_bound": REFUSED, "capability_missing": REFUSED,
    "service_not_running": REFUSED, "link_absent": REFUSED, "not_a_declared_link": REFUSED,
    "baud_refused": REFUSED, "link_rx_only": REFUSED, "voltage_unverified": REFUSED,
    "dtr_rts_unsafe": REFUSED, "no_return_path": REFUSED, "touch_pending": REFUSED,
    "port_in_use": REFUSED, "port_privileged": REFUSED, "token_missing": REFUSED,
    "pin_reserved": REFUSED, "pin_not_on_header": REFUSED, "pin_taken": REFUSED, "pin_unknown": REFUSED,
    "voltage_unsafe": REFUSED, "adapter_absent": REFUSED, "adapter_taken": REFUSED, "name_taken": REFUSED,
    "intent_not_built": REFUSED, "connection_unknown": REFUSED, "target_unknown": REFUSED,
    "i2c_pins_unclaimed": REFUSED, "bad_manifest": REFUSED, "project_conflict": REFUSED, "project_applied": REFUSED,
    "project_not_applied": REFUSED, "pins_not_controllable": REFUSED, "meter_function": REFUSED,
    "meter_busy": REFUSED, "meter_idle": REFUSED, "recording_unknown": REFUSED, "capture_unknown": REFUSED, "stage_unknown": REFUSED, "event_unknown": REFUSED, "ppk2_mode": REFUSED, "supply_unconfirmed": REFUSED, "profile_not_candidate": REFUSED,
    "not_a_profiler_recording": REFUSED,
    # failed: it was attempted and went wrong
    "bad_wake_profile": FAILED, "no_reply": FAILED, "touch_failed": FAILED, "bootloader_not_reached": FAILED, "flash_failed": FAILED,
    "run_node_not_returned": FAILED, "write_failed": FAILED, "pins_not_set": FAILED,
    "meter_unreachable": FAILED, "ppk2_unreachable": FAILED,
    # suspect: it finished but the evidence says do not trust it
    "flash_suspect": SUSPECT, "meter_overload": SUSPECT,
}

# Raised only by the CLI or at service start, never by a running service answering a request: they have
# an exit code and no HTTP status (core.md).
CLI_ONLY = frozenset(["service_not_running", "port_in_use", "port_privileged", "token_missing"])

# A refused result is 409 (state, holder, lock, serial, protected, wiring, in use) unless it is one of these.
REFUSED_HTTP = {
    "bad_package": 422, "wrong_softdevice": 422, "no_bound": 422, "baud_refused": 422,
    "not_a_declared_link": 422, "checksum_mismatch": 422, "bad_project": 422,
    "firmware_unknown": 404, "project_unknown": 404, "macro_unknown": 404, "pin_unknown": 404,
    "target_unknown": 404, "bad_manifest": 422, "recording_unknown": 404, "capture_unknown": 404, "stage_unknown": 404, "event_unknown": 404,
    "pin_reserved": 422, "pin_not_on_header": 422, "voltage_unsafe": 422, "intent_not_built": 422, "connection_unknown": 404,
    "length_required": 411, "not_a_profiler_recording": 422,
    "store_full": 507,
}

USAGE_EXIT = 2          # a bad argument is the argument parser's business and never a Result
USAGE_HTTP = 400


def exit_code(result):
    return EXIT[result.cls]


def http_status(result):
    """The HTTP status for a Result, or None for a CLI-only code (it never travels over HTTP)."""
    if result.code in CLI_ONLY:
        return None
    if result.cls == OK:
        return 200
    if result.cls == REFUSED:
        return REFUSED_HTTP.get(result.code, 409)
    return 500          # failed and suspect: the HTTP status is 500, and the body's `ok` is false

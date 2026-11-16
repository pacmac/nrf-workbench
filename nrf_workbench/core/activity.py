"""The activity log (docs/specs/core.md): a record of what every caller did to the bench.

For an AI that flashes hardware this is the page a human most needs. It is a bounded stream of its own
(a flood of live-state events must not push it out of retention): `retain` and `max_age_s` are REQUIRED
and have no default (core.events.Bus).

An entry is a SUMMARY, not the whole Result: who, which command, its arguments (secrets redacted), the
class and code, the message and the duration. The whole tool output stays in the Result the caller
received; logging every flash's full output would be the unbounded growth the retention rule exists to
prevent. A refusal is recorded exactly like a success, and so is a bad argument (class `usage`).
"""
from .events import Bus

REDACTED = "***"
# An argument whose NAME contains one of these is never recorded. Matched on the name, case-insensitively,
# so `token_file` and `Authorization` are both caught.
SECRET_WORDS = ("token", "password", "secret", "key", "authorization", "credential")


def redact(args):
    out = {}
    for name, value in (args or {}).items():
        secret = any(word in str(name).lower() for word in SECRET_WORDS)
        out[name] = REDACTED if secret else value
    return out


class ActivityLog(object):
    TYPE = "command.result"

    def __init__(self, retain, max_age_s, **clock):
        self.bus = Bus(retain, max_age_s, **clock)

    def record(self, caller, command, args, result, duration_s):
        """One command that produced a Result, of any class."""
        return self.bus.publish(self.TYPE, "core", {
            "caller": caller, "command": command, "args": redact(args), "ok": result.ok,
            "class": result.cls, "code": result.code, "message": result.message, "duration_s": duration_s})

    def record_usage(self, caller, command, args, message, duration_s):
        """A command refused for a bad argument: there is no Result, and it is still something a caller did."""
        return self.bus.publish(self.TYPE, "core", {
            "caller": caller, "command": command, "args": redact(args), "ok": False,
            "class": "usage", "code": "usage", "message": message, "duration_s": duration_s})

    def record_error(self, caller, command, args, message, duration_s):
        """A command whose handler raised: no Result, and the caller was told it failed. Recorded as an
        `error`, the class the dashboard can show apart from a refusal."""
        return self.bus.publish(self.TYPE, "core", {
            "caller": caller, "command": command, "args": redact(args), "ok": False,
            "class": "error", "code": "internal", "message": message, "duration_s": duration_s})

    def since(self, cursor=0, epoch=None):
        return self.bus.since(cursor, epoch, types=(self.TYPE,))

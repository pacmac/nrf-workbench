"""The activity log (docs/specs/core.md): a record of what every caller did to the bench.

For an AI that flashes hardware this is the page a human most needs. It is a bounded stream of its own
(a flood of live-state events must not push it out of retention): `retain` and `max_age_s` are REQUIRED
and have no default (core.events.Bus).

An entry is a SUMMARY, not the whole Result: who, which command, its arguments (secrets redacted), the
class and code, the message and the duration. The whole tool output stays in the Result the caller
received; logging every flash's full output would be the unbounded growth the retention rule exists to
prevent. A refusal is recorded exactly like a success, and so is a bad argument (class `usage`).
"""
import itertools

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
    STARTED = "command.started"

    def __init__(self, retain, max_age_s, **clock):
        self.bus = Bus(retain, max_age_s, **clock)
        self._runs = itertools.count(1)             # next() on a count is atomic: one id per started command

    def begin(self, caller, command, args):
        """A command is about to run. Returns its `run` id, which the `command.result` that ends it must carry
        (docs/specs/core.md): a consumer that has seen a start and no result knows it is still running."""
        run = next(self._runs)
        self.bus.publish(self.STARTED, "core", {"run": run, "caller": caller, "command": command,
                                                "args": redact(args)})
        return run

    def _result(self, caller, command, args, ok, cls, code, message, duration_s, run):
        payload = {"caller": caller, "command": command, "args": redact(args), "ok": ok,
                   "class": cls, "code": code, "message": message, "duration_s": duration_s}
        if run is not None:                         # only a command that was announced has a run to end
            payload["run"] = run
        return self.bus.publish(self.TYPE, "core", payload)

    def record(self, caller, command, args, result, duration_s, run=None):
        """One command that produced a Result, of any class."""
        return self._result(caller, command, args, result.ok, result.cls, result.code, result.message, duration_s, run)

    def record_usage(self, caller, command, args, message, duration_s, run=None):
        """A command refused for a bad argument: there is no Result, and it is still something a caller did."""
        return self._result(caller, command, args, False, "usage", "usage", message, duration_s, run)

    def record_error(self, caller, command, args, message, duration_s, run=None):
        """A command whose handler raised: no Result, and the caller was told it failed. Recorded as an
        `error`, the class the dashboard can show apart from a refusal."""
        return self._result(caller, command, args, False, "error", "internal", message, duration_s, run)

    def since(self, cursor=0, epoch=None):
        return self.bus.since(cursor, epoch, types=(self.TYPE,))

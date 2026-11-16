"""The one shape every command returns (docs/specs/core.md, "results and refusals").

A Result is never raised for an expected outcome. Its class is one of four, and they are not
interchangeable:

  ok       it was done and judged good
  refused  NOTHING was attempted and nothing changed (a guard said no)
  failed   it was attempted and went wrong
  suspect  it finished but the evidence says do not trust it

`evidence` carries what was found (pids, paths, ids, durations) and the WHOLE output of any
external tool, never a tail.
"""
import json

OK = "ok"
REFUSED = "refused"
FAILED = "failed"
SUSPECT = "suspect"

# CLI exit codes derived from the class (core.md): 2 is left for usage errors, which are the
# argument parser's business and never a Result.
EXIT = {OK: 0, REFUSED: 1, FAILED: 3, SUSPECT: 4}


class Result(object):
    __slots__ = ("cls", "code", "message", "data", "evidence")

    def __init__(self, cls, code, message="", data=None, evidence=None):
        if cls not in EXIT:
            raise ValueError("unknown result class %r" % (cls,))
        self.cls = cls
        self.code = code
        self.message = message
        self.data = data if data is not None else {}
        self.evidence = evidence if evidence is not None else {}

    @property
    def ok(self):
        return self.cls == OK

    @classmethod
    def success(cls, data=None, message="", evidence=None):
        return cls(OK, "ok", message, data, evidence)

    @classmethod
    def refused(cls, code, message, evidence=None):
        return cls(REFUSED, code, message, None, evidence)

    @classmethod
    def failed(cls, code, message, evidence=None, data=None):
        return cls(FAILED, code, message, data, evidence)

    def exit_code(self):
        return EXIT[self.cls]

    def to_dict(self):
        return {"ok": self.ok, "class": self.cls, "code": self.code, "message": self.message,
                "data": self.data, "evidence": self.evidence}

    def to_json(self):
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def __repr__(self):
        return "Result(%s, %s)" % (self.cls, self.code)

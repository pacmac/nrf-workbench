"""Browser sessions (docs/specs/dashboard.md, "how it writes"): the store behind the login cookie.

A browser enters the token ONCE; the server answers with a session id in an HttpOnly cookie that page script
cannot read, and a separate CSRF value that script CAN read and must send as a header on every write. The two
are independent random values (256 bits each from the OS source), so a page that learns the CSRF value learns
nothing about the session id, and a stolen id alone cannot write.

Like every retained thing here the store has REQUIRED bounds and no default: `max_sessions` and `max_age_s`.
Beyond the count the OLDEST session is evicted; an expired session is gone the moment it is looked at. The
values are the composition root's, derived there, beside the code that sets them.

Held in memory: a restart logs everyone out, which is the safe direction.
"""
import secrets
import threading
import time
import urllib.parse

from .events import BoundError, _positive_number


def same_origin(headers):
    """A request with no Origin is not a browser (a CLI, a script) and is allowed; one with an Origin must
    name THIS host, or another website could drive the service through the visitor's browser. One
    definition, shared by the cookie check (api) and the login (dashboard)."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = headers.get("host")
    return bool(host) and urllib.parse.urlsplit(origin).netloc.lower() == host.lower()


class Session(object):
    __slots__ = ("sid", "csrf", "created")

    def __init__(self, sid, csrf, created):
        self.sid, self.csrf, self.created = sid, csrf, created


class SessionStore(object):
    def __init__(self, max_sessions, max_age_s, clock=time.monotonic):
        if not isinstance(max_sessions, int) or isinstance(max_sessions, bool) or max_sessions < 1:
            raise BoundError("a session store needs `max_sessions`, a count of at least 1, and has no default")
        if not _positive_number(max_age_s):
            raise BoundError("a session store needs `max_age_s`, a positive number of seconds, and has no default")
        self.max_sessions, self.max_age_s, self._clock = max_sessions, max_age_s, clock
        self._sessions = {}
        self._lock = threading.Lock()

    def _expire(self, now):
        for sid in [s for s, v in self._sessions.items() if now - v.created > self.max_age_s]:
            del self._sessions[sid]

    def create(self):
        """A new session. Evicts the oldest if the store is full."""
        now = self._clock()
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(32), now)
        with self._lock:
            self._expire(now)
            while len(self._sessions) >= self.max_sessions:
                oldest = min(self._sessions.values(), key=lambda v: v.created)
                del self._sessions[oldest.sid]
            self._sessions[session.sid] = session
        return session

    def get(self, sid):
        """The live session with this id, or None (unknown, destroyed, or expired)."""
        if not isinstance(sid, str) or not sid:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(sid)
            if session is None:
                return None
            if now - session.created > self.max_age_s:
                del self._sessions[sid]
                return None
            return session

    def destroy(self, sid):
        with self._lock:
            return self._sessions.pop(sid, None) is not None

    def __len__(self):
        with self._lock:
            return len(self._sessions)

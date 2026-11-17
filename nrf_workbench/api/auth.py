"""Who may call what (docs/specs/api.md, "who may reach it"; docs/specs/dashboard.md, "how it writes").

The service flashes firmware and power-cycles hardware, so it is not open by default. Three modes:

  token      every request needs the token (the default for any address that leaves this machine);
  open-read  a request that only reads is open; every write needs the token;
  open       no token at all, on any address: the operator's statement that this network is trusted. Only a
             browser request from another site is refused (see `authorise`).

With NO token configured the service is loopback-only (core.config.check_listen refuses anything
else), and every request is allowed, as a local tool is. The token is sent as a bearer header and is
compared in constant time; it is never logged and never echoed.

A BROWSER cannot send a bearer header on a page load, and putting the token where script can read it hands
it to any script on the page. So a browser logs in once (dashboard `POST /login`), and from then on holds
only a session cookie that script cannot read (HttpOnly). A request authorised by that cookie must ALSO:
  * carry an `Origin` that is this host if it carries one at all (another site cannot ride the cookie), and
  * for anything that is not a read, send the session's CSRF value in `X-CSRF-Token`.
The websocket handshake goes through the same check, so the cookie works there and a foreign page cannot.
"""
import hmac

from ..core.sessions import same_origin as origin_allowed

ACCESS_MODES = ("token", "open-read", "open")
COOKIE_NAME = "nrfwb_session"
CSRF_HEADER = "x-csrf-token"


def cookie_value(headers, name=COOKIE_NAME):
    for part in (headers.get("cookie") or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:                  # "name" with no "=" is not a cookie
            return value
    return None


class Auth(object):
    def __init__(self, token=None, access="token", sessions=None):
        if access not in ACCESS_MODES:
            raise ValueError("access must be one of: %s" % ", ".join(ACCESS_MODES))
        self.token = token or None
        self.access = access
        self.sessions = sessions

    def token_matches(self, candidate):
        """Constant-time comparison, for the bearer header and for the login form."""
        if self.token is None or not isinstance(candidate, str) or not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"), self.token.encode("utf-8"))

    def authorise(self, headers, effect):
        """(allowed, caller). `caller` is a label for the activity record, never the token."""
        if self.access == "open":
            # No token is asked of anybody, on any address: the operator said this network is trusted. The ONE check
            # kept is the browser's: a request that carries an Origin must name this host, or any web page the
            # operator happens to visit could drive the bench (flash, reset) through their browser. A script, the CLI
            # and the MCP send no Origin and pass. (Residual risk, in api.md: anything else on the LAN may call it.)
            return (True, "open") if origin_allowed(headers) else (False, None)
        if self.token is None:
            return True, "local"
        if self.access == "open-read" and effect == "read":
            return True, "anonymous"
        presented = self._presented(headers)
        if presented is not None and self.token_matches(presented):
            return True, "token"
        if self._session_ok(headers, effect):
            return True, "dashboard"
        return False, None

    def _session_ok(self, headers, effect):
        if self.sessions is None:
            return False
        sid = cookie_value(headers)
        session = self.sessions.get(sid) if sid else None
        if session is None:
            return False
        if not origin_allowed(headers):
            return False
        if effect != "read":
            sent = headers.get(CSRF_HEADER)
            if not sent or not hmac.compare_digest(sent.encode("utf-8"), session.csrf.encode("utf-8")):
                return False
        return True

    @staticmethod
    def _presented(headers):
        value = headers.get("authorization")
        if not value:
            return None
        scheme, _, credential = value.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            return None
        return credential.strip()

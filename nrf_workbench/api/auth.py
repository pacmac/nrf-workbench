"""Who may call what (docs/specs/api.md, "who may reach it").

The service flashes firmware and power-cycles hardware, so it is not open by default. Two modes, and
**there is no mode with an unauthenticated write**:

  token      every request needs the token (the default for any address that leaves this machine);
  open-read  a request that only reads is open; every write needs the token.

With NO token configured the service is loopback-only (core.config.check_listen refuses anything
else), and every request is allowed, as a local tool is. The token is sent as a bearer header and is
compared in constant time; it is never logged and never echoed.
"""
import hmac

ACCESS_MODES = ("token", "open-read")


class Auth(object):
    def __init__(self, token=None, access="token"):
        if access not in ACCESS_MODES:
            raise ValueError("access must be one of: %s" % ", ".join(ACCESS_MODES))
        self.token = token or None
        self.access = access

    def authorise(self, headers, effect):
        """(allowed, caller). `caller` is a label for the activity record, never the token."""
        if self.token is None:
            return True, "local"
        if self.access == "open-read" and effect == "read":
            return True, "anonymous"
        if self._presented(headers) is not None and self._matches(self._presented(headers)):
            return True, "token"
        return False, None

    @staticmethod
    def _presented(headers):
        value = headers.get("authorization")
        if not value:
            return None
        scheme, _, credential = value.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            return None
        return credential.strip()

    def _matches(self, presented):
        return hmac.compare_digest(presented.encode("utf-8"), self.token.encode("utf-8"))

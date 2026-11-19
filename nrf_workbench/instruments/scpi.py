"""A raw-socket SCPI line, asked and answered (docs/specs/meter.md).

One connection is one conversation: connect, ask, read each answer, close. The only thing that can be sent is a QUERY (a line ending in `?`), so
nothing here can write a setting to an instrument; `ask` refuses anything else before it reaches the socket.
"""
import socket
import time

SCPI_PORT = 5025        # the Siglent raw-socket port, measured open on the SDM3045X (2026-11-19)
# A give-up ceiling for one query, not a wait: the meter answers a query it knows in 1 to 7 ms (measured); a busy 4-core Pi may need 50 ms to
# schedule us, and 5 times that (0.25 s) is where something is wrong. It is also the connect ceiling. The call returns when the answer arrives.
CEILING_S = 0.25


class ScpiError(Exception):
    pass


class Unreachable(ScpiError):
    """The connection was not made."""


class Silent(ScpiError):
    """The meter took the line and gave no answer inside the ceiling (an unknown query is silent, not an error line)."""

    def __init__(self, query, ceiling):
        ScpiError.__init__(self, "no answer to %s within %.2f s" % (query, ceiling))
        self.query, self.ceiling = query, ceiling


class Connection(object):
    def __init__(self, host, port=SCPI_PORT, ceiling=CEILING_S):
        try:
            self._sock = socket.create_connection((host, port), timeout=ceiling)
        except OSError as exc:
            raise Unreachable("%s:%d did not accept a connection within %.2f s: %s" % (host, port, ceiling, exc))
        self._buf = b""
        self.broken = False

    def close(self):
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def ask(self, query, ceiling=CEILING_S, silent_ok=False):
        """The one line the instrument answers with, or None when it was silent and `silent_ok` (a query the instrument may not know).
        A silence that is NOT ok marks the connection broken: a late answer would be taken for the next query's."""
        if not isinstance(query, str) or not query.endswith("?") or "\n" in query or "\r" in query:
            raise ValueError("only a single-line query (ending in ?) may be sent to an instrument, got %r" % (query,))
        if self.broken:
            raise ScpiError("the connection is broken by an earlier silence: open a new one")
        try:
            self._sock.sendall((query + "\n").encode("ascii"))
        except OSError as exc:
            raise Unreachable("the instrument closed the connection: %s" % exc)
        deadline = time.monotonic() + ceiling
        while b"\n" not in self._buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return self._silent(query, ceiling, silent_ok)
            self._sock.settimeout(left)
            try:
                got = self._sock.recv(4096)
            except socket.timeout:
                return self._silent(query, ceiling, silent_ok)
            except OSError as exc:
                raise Unreachable("the instrument closed the connection: %s" % exc)
            if not got:
                raise Unreachable("the instrument closed the connection")
            self._buf += got
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("ascii", "replace").strip()

    def _silent(self, query, ceiling, silent_ok):
        if silent_ok:
            return None
        self.broken = True
        raise Silent(query, ceiling)

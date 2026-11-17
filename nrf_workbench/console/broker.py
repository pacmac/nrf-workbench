"""The broker: one thread that owns every declared adapter (docs/specs/console.md, "the broker" and "slice 1").

It blocks in `select` with NO TIMEOUT on the kernel's uevent socket, a wake pipe and every open port, exactly like the
device monitor: nothing is waited for by the clock. An adapter that appears is opened and one that vanishes is closed
on the kernel's word, and the change is published. Bytes read are split into lines and published as `console.line`
with the byte offset where each line starts, into a bounded bus (the caller supplies it: a retained stream has its
bound configured, never defaulted). A state change is published as `console.link`.

It opens only links that are usable, and only through `port.Port`. It never touches a node that is not a declared
link's adapter. A host with no uevents says so and re-checks when asked (`refresh`).
"""
import collections
import os
import select
import threading
import time

from ..devices.monitor import open_kernel_uevents
from . import links as linkmod
from .port import PortError, PortGone, PortHeld, write_bound_s

# A line longer than this is cut and marked partial. 1 024 bytes is a memory budget, not a measurement: 20 000
# retained lines at that size is 20 MB, far under the Pi's RAM, and no firmware's real line length has been measured.
LINE_CAP_BYTES = 1024

# A request handed to the broker's own thread (open, close, reopen) is a few system calls, milliseconds. This is only the
# give-up for the case where the loop is dead, so a command fails with a reason instead of waiting for ever.
CALL_CEILING_S = 5.0

ABSENT, OPEN, HELD, ERROR, UNUSABLE = "absent", "open", "held", "error", "unusable"


class LinkState(object):
    def __init__(self, link):
        self.link = link
        self.state, self.reason, self.node = UNUSABLE, "", None
        self.port = None
        self.buf = bytearray()
        self.read_total = 0               # bytes read from the adapter since it was opened by this broker
        self.committed = 0                # the offset just after the last line published
        self.lines = 0
        self.last_byte_wall = None
        self.replies = 0                  # reply lines seen, so a sender can wait for the next one
        self.last_reply = None
        self.cond = threading.Condition()
        self.write_lock = threading.Lock()

    def snapshot(self):
        d = self.link.to_dict()
        d.update({"state": self.state, "reason": self.reason, "node": self.node, "bytes": self.read_total,
                  "offset": self.committed, "lines": self.lines, "last_byte_at": self.last_byte_wall})
        return d


class Broker(object):
    def __init__(self, links, resolve, open_port, bus, open_uevents=open_kernel_uevents, now=time.time):
        """`resolve(link)` is `links.resolve` bound to the trees; `open_port(node, baud)` is `port.Port`;
        `bus` is a core.events.Bus; `open_uevents()` returns the kernel's uevent socket (OSError if none)."""
        self._states = dict((l.name, LinkState(l)) for l in links if l.name)
        self._unnamed = [l for l in links if not l.name]
        self._resolve, self._open_port, self._bus, self._open_uevents, self._now = resolve, open_port, bus, open_uevents, now
        # `_lock` guards the link states and the port set, and NOTHING is called out while it is held: a state change is
        # queued in `_outbox` and published after the lock is released (`_flush`). A subscriber takes locks of its own and
        # may ask the broker for state, so publishing under `_lock` is a lock-order inversion waiting for the wrong day
        # (a test found it as a hang).
        self._lock = threading.RLock()
        self._outbox = []
        self._calls = collections.deque()              # requests for the loop thread: ports are opened and closed there only
        self._thread = None
        self._sock = None
        self._wake_r = self._wake_w = None
        self._stopping = False
        self.live = False
        self.reason = ""

    # -- lifecycle ----------------------------------------------------------------------------------
    def start(self):
        if self._thread is not None:
            raise RuntimeError("the broker is already started")
        try:
            self._sock = self._open_uevents()
            self._sock.setblocking(False)
            self.live = True
        except OSError as exc:
            self._sock, self.reason = None, str(exc)
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        self._refresh()                                # the first level, before the thread watches for changes
        self._thread = threading.Thread(target=self._run, name="console-broker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping = True
        self._wake()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            for st in self._states.values():
                self._close(st, ABSENT, "the service is stopping", publish=False)
        for fd in (self._wake_r, self._wake_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if self._sock is not None:
            self._sock.close()
        self._thread = self._sock = self._wake_r = self._wake_w = None
        self.live = False

    def _wake(self):
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"x")
            except OSError:
                pass

    # -- the loop -----------------------------------------------------------------------------------
    def _run(self):
        while not self._stopping:
            with self._lock:
                ports = [(st, st.port) for st in self._states.values() if st.port is not None]
            watch = [self._wake_r] + ([self._sock] if self._sock is not None else []) + [p.fileno() for _s, p in ports]
            ready, _, _ = select.select(watch, [], [])               # no timeout, on purpose
            if self._wake_r in ready:
                self._drain(self._wake_r)
                while self._calls:
                    self._calls.popleft()()
                if self._stopping:
                    return
            if self._sock is not None and self._sock in ready:
                self._drain_sock()
                self._refresh()
            for st, port in ports:
                if st.port is not port:
                    continue                                  # closed by a request that ran just now
                if port.fileno() in ready:
                    self._read(st, port)

    def _drain(self, fd):
        try:
            while os.read(fd, 4096):
                pass
        except (BlockingIOError, OSError):
            pass

    def _drain_sock(self):
        try:
            while self._sock.recv(65536):
                pass
        except (BlockingIOError, OSError):
            pass

    def _read(self, st, port):
        try:
            data = port.read()
        except PortGone as exc:
            with self._lock:
                self._close(st, ABSENT, str(exc))
            self._flush()
            return
        self._feed(st, data)             # the byte buffer is this thread's alone: no lock, so nothing is held while publishing

    # -- states -------------------------------------------------------------------------------------
    def _set(self, st, state, reason="", node=None, publish=True):
        """Record a state change. Call it with `_lock` held; the event goes out at the next `_flush`."""
        changed = (st.state, st.reason, st.node) != (state, reason, node)
        st.state, st.reason, st.node = state, reason, node
        if changed and publish:
            self._outbox.append(("console.link", {"link": st.link.name, "state": state, "reason": reason, "node": node}))

    def _flush(self):
        """Publish what `_set` queued, with no lock held."""
        with self._lock:
            out, self._outbox = self._outbox, []
        for kind, payload in out:
            self._bus.publish(kind, "console", payload)

    def _close(self, st, state, reason, publish=True):
        if st.port is not None:
            st.port.close()
            st.port = None
        self._set(st, state, reason, None, publish)

    def _call(self, fn):
        """Run `fn` on the broker's thread and return its result. Before the thread exists, or on it, run it here."""
        if self._thread is None or threading.current_thread() is self._thread:
            return fn()
        done, box = threading.Event(), []

        def run():
            try:
                box.append((True, fn()))
            except BaseException as exc:                      # noqa: BLE001 - handed back to the caller
                box.append((False, exc))
            done.set()

        self._calls.append(run)
        self._wake()
        if not done.wait(CALL_CEILING_S):
            raise RuntimeError("the console broker did not answer: its thread is not running")
        ok, value = box[0]
        if not ok:
            raise value
        return value

    def refresh(self):
        """Look at every link once, on the broker's thread. Called on demand (`console.reopen`, and a host with no
        uevents); the kernel's word and the start call `_refresh` directly."""
        return self._call(self._refresh)

    def _refresh(self):
        """Open an adapter that is there and not open, close one that has gone."""
        with self._lock:
            for st in self._states.values():
                if not st.link.usable:
                    self._set(st, UNUSABLE, "; ".join(p.message for p in st.link.problems))
                    continue
                found, why = self._resolve(st.link)
                if found is None:
                    if st.port is not None or st.state != ABSENT or st.reason != why:
                        self._close(st, ABSENT, why)
                    continue
                if st.port is not None and st.node == found["node"]:
                    continue
                if st.port is not None:
                    self._close(st, ABSENT, "the adapter's node changed")
                try:
                    st.port = self._open_port(found["node"], st.link.baud)
                except PortHeld as exc:
                    self._set(st, HELD, str(exc), found["node"])
                    continue
                except (PortError, ValueError) as exc:
                    self._set(st, ERROR, str(exc), found["node"])
                    continue
                st.buf = bytearray()
                self._set(st, OPEN, "", found["node"])
        self._flush()

    def reopen(self, name):
        """Close and reopen one link's adapter. (state dict, or None for an unknown link)"""
        def work():
            with self._lock:
                st = self._states.get(name)
                if st is None:
                    return None
                if st.port is not None:
                    self._close(st, ABSENT, "reopened on request")
            self._refresh()
            return self.state(name)
        return self._call(work)

    # -- bytes to lines -----------------------------------------------------------------------------
    def _feed(self, st, data):
        st.read_total += len(data)
        st.last_byte_wall = self._now()
        st.buf.extend(data)
        marker = st.link.reply_marker
        while True:
            idx = st.buf.find(b"\n")
            if idx < 0 and len(st.buf) < LINE_CAP_BYTES:
                return
            partial = idx < 0 or idx >= LINE_CAP_BYTES
            cut = LINE_CAP_BYTES if partial else idx
            raw = bytes(st.buf[:cut])
            used = cut if partial else idx + 1
            del st.buf[:used]
            text = raw.rstrip(b"\r").decode("utf-8", errors="replace") if not partial else raw.decode("utf-8", errors="replace")
            reply = bool(marker) and text.startswith(marker)
            payload = {"link": st.link.name, "offset": st.committed, "bytes": used, "text": text, "reply": reply}
            if partial:
                payload["partial"] = True
            self._bus.publish("console.line", "console", payload)
            st.committed += used
            st.lines += 1
            if reply:
                with st.cond:
                    st.replies += 1
                    st.last_reply = text
                    st.cond.notify_all()

    # -- what a command needs -----------------------------------------------------------------------
    def state(self, name):
        with self._lock:
            st = self._states.get(name)
            return st.snapshot() if st is not None else None

    def states(self):
        with self._lock:
            return [st.snapshot() for st in self._states.values()] + [dict(l.to_dict(), state=UNUSABLE, reason="no name") for l in self._unnamed]

    def since(self, name, cursor):
        """({lines, next, lost}, None) or (None, 'unknown'). The lines are those retained at or after the byte offset."""
        with self._lock:
            st = self._states.get(name)
            if st is None:
                return None
            committed = st.committed
        page = self._bus.since(0, None, types=("console.line",))
        mine = [e.payload for e in page.events if e.payload["link"] == name]
        first = mine[0]["offset"] if mine else committed
        lines = [dict(p, wall=e.wall) for e, p in ((e, e.payload) for e in page.events if e.payload["link"] == name) if p["offset"] >= cursor]
        return {"lines": lines, "next": committed, "lost": max(0, first - cursor)}

    def send(self, name, text, bound_s=None):
        """Write `text` and the link's line ending. Returns {"sent": n, "reply": text or None}. Raises PortGone or
        PortError. The caller has already checked the link may transmit; this only moves bytes. With `bound_s` and a
        declared reply marker it waits for the next reply line (an event, not a clock; `bound_s` is only the give-up)."""
        with self._lock:
            st = self._states.get(name)
            port = st.port if st is not None else None
        if port is None:
            raise PortGone("the link is not open")
        data = (text + st.link.eol).encode("utf-8")
        with st.cond:
            seen = st.replies
        with st.write_lock:
            port.write_all(data, write_bound_s(len(data), st.link.baud))
        waited = bound_s is not None and bool(st.link.reply_marker)
        reply = None
        if waited:
            with st.cond:
                if st.cond.wait_for(lambda: st.replies > seen, timeout=bound_s):
                    reply = st.last_reply
        return {"sent": len(data), "waited": waited, "reply": reply}

"""Wait for a change in what is attached, on EVENTS, never a clock.

The wait is level first, then watch, then level again: the state is read, the kernel's uevent
socket is watched, and the state is read once more (a change between the first look and the watch
would otherwise raise no event). An event only means "look again": the level is re-read every time
and never inferred from the message. The ceiling is the caller's bound, applied to select(); there
is no sleep and no poll. A host that delivers no uevents (a container often does not) is reported
as such, with the one level check that could be made, never as a pass.
"""
import select
import socket
import time

NETLINK_KOBJECT_UEVENT = 15


class WaitResult(object):
    __slots__ = ("reached", "elapsed_s", "source", "note")

    def __init__(self, reached, elapsed_s, source, note=""):
        self.reached = reached
        self.elapsed_s = elapsed_s
        self.source = source
        self.note = note


class UeventWatcher(object):
    """The kernel's own uevent socket. Needs no udev."""

    source = "kernel uevent"

    def __init__(self):
        self.sock = None
        self.reason = ""

    def open(self):
        """Open the socket BEFORE the action that will raise the event, so none is missed."""
        try:
            self.sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
            self.sock.bind((0, 1))            # group 1: the kernel's own messages
        except OSError as exc:
            self.sock = None
            self.reason = str(exc)

    def wait(self, predicate, bound_s):
        start = time.monotonic()
        if predicate():                                              # level first
            return WaitResult(True, time.monotonic() - start, self.source)
        if self.sock is None:
            return WaitResult(False, time.monotonic() - start, "none",
                              "no uevent source on this host (%s); one level check was made" % self.reason)
        deadline = start + bound_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return WaitResult(predicate(), time.monotonic() - start, self.source,
                                  "bound reached" if not predicate() else "")
            ready, _, _ = select.select([self.sock], [], [], remaining)
            if ready:
                try:
                    self.sock.recv(65536)                            # the content is not trusted
                except OSError:
                    pass
            if predicate():                                          # level again, every time
                return WaitResult(True, time.monotonic() - start, self.source)

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None

"""Publish what is attached, as it changes (`devices.changed`), with no polling and no timer.

The thread blocks in select() on two things with NO TIMEOUT: the kernel's uevent socket, and a wake pipe.
Nothing is ever waited for by the clock: it sleeps until the kernel says something changed or `stop()`
writes a byte. So it needs no bound (there is nothing to derive one from).

The order is level first, then watch (devices/events.py): the socket is opened BEFORE the first read, so a
change between the two cannot be missed, and an event only means "look again": the level is re-read after
every event and a `devices.changed` is published only when it DIFFERS from the last one published. A burst
of uevents (one re-enumeration raises many) therefore becomes one event per distinct state.

If this host delivers no uevents (a container often does not), that is published as
`devices.live_unavailable` with the reason, after the one snapshot that could be taken: the dashboard says
the list is not live, instead of showing a stale one as current. A failing read is published as
`devices.read_failed`, never swallowed, and the monitor keeps running.
"""
import os
import select
import socket
import threading

from .events import NETLINK_KOBJECT_UEVENT


def open_kernel_uevents():
    """The kernel's own uevent socket (needs no udev). OSError if this host has none."""
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    try:
        sock.bind((0, 1))                     # group 1: the kernel's own messages
    except OSError:
        sock.close()
        raise
    return sock


def usb_changes(before, after):
    """[{change: plugged|unplugged, slot, id, product, serial}] between two USB levels, by slot, id and serial."""
    key = lambda d: (d["slot"], d["id"], d["serial"])        # noqa: E731
    old, new = dict((key(d), d) for d in before), dict((key(d), d) for d in after)
    return ([dict(new[k], change="plugged") for k in sorted(new, key=str) if k not in old]
            + [dict(old[k], change="unplugged") for k in sorted(old, key=str) if k not in new])


class DeviceMonitor(object):
    def __init__(self, read_state, publish, open_source=open_kernel_uevents, read_usb=None):
        """`read_state()` returns the current list of boards (JSON-able); `publish(type, area, payload)`
        is a bus's publish. `read_usb()`, when given, returns every USB device attached as a list of {slot, id, product,
        serial}: a change in it is published too, with `usb` (the level) and `usb_changes` (what was plugged and unplugged), so
        a page that lists adapters and ESP32 boards, which are not boards, updates without being asked."""
        self._read_state, self._publish, self._open_source = read_state, publish, open_source
        self._read_usb, self._last_usb = read_usb, None
        self._thread = None
        self._sock = None
        self._wake_r = self._wake_w = None
        self._last = None
        self.live = False
        self.reason = ""

    def start(self):
        """Open the source, take the first level, publish it, then watch. Returns once the snapshot is out."""
        if self._thread is not None or self._wake_r is not None:
            raise RuntimeError("the monitor is already started")
        try:
            self._sock = self._open_source()
        except OSError as exc:
            self._sock, self.reason = None, str(exc)
        self._last = self._read_state()
        payload = {"boards": self._last, "reason": "start"}
        if self._read_usb is not None:
            self._last_usb = self._read_usb()
            payload["usb"] = self._last_usb
        self._publish("devices.changed", "devices", payload)
        if self._sock is None:
            self._publish("devices.live_unavailable", "devices", {"reason": self.reason})
            return
        self._sock.setblocking(False)
        self._wake_r, self._wake_w = os.pipe()
        self.live = True
        self._thread = threading.Thread(target=self._run, name="device-monitor", daemon=True)
        self._thread.start()

    def _run(self):
        while True:
            ready, _, _ = select.select([self._sock, self._wake_r], [], [])       # no timeout, on purpose
            if self._wake_r in ready:
                return
            if self._drain_closed():
                self.live, self.reason = False, "the uevent source closed"
                self._publish("devices.live_unavailable", "devices", {"reason": self.reason})
                return
            try:
                state = self._read_state()
                usb = self._read_usb() if self._read_usb is not None else None
            except Exception as exc:                       # noqa: BLE001 - published, not swallowed
                self._publish("devices.read_failed", "devices", {"error": repr(exc)})
                continue
            if state != self._last or usb != self._last_usb:
                payload = {"boards": state, "reason": "uevent"}
                if usb is not None:
                    payload["usb"] = usb
                    payload["usb_changes"] = usb_changes(self._last_usb or [], usb)
                self._last, self._last_usb = state, usb
                self._publish("devices.changed", "devices", payload)

    def _drain_closed(self):
        """Read everything waiting (the content is not trusted). True if the source is gone."""
        while True:
            try:
                data = self._sock.recv(65536)
            except BlockingIOError:
                return False
            except OSError:
                return True
            if not data:
                return True

    def stop(self):
        """Wake the thread and wait for it. Safe to call twice, or when it never started."""
        if self._wake_w is not None:
            try:
                os.write(self._wake_w, b"x")
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join()
        for fd in (self._wake_r, self._wake_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        if self._sock is not None:
            self._sock.close()
        self._thread, self._sock, self._wake_r, self._wake_w, self.live = None, None, None, None, False

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

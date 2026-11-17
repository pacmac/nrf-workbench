"""The ONE place a serial port is opened for reading and writing (docs/specs/console.md, "safety").

It uses plain `termios`, not a library that asserts modem lines. The descriptor is opened `O_NOCTTY | O_NONBLOCK`,
locked (`TIOCEXCL`, which stops other users but not root, plus an advisory `flock`, which stops every cooperating
process, root included), made raw 8N1 with `CLOCAL | CREAD`, and **`HUPCL` cleared so that closing the port does not
drop DTR**. This code never sets DTR or RTS. (The kernel may raise DTR when a port is opened; that is harmless only
where DTR does not reach a reset or boot pin, which is why a link must declare `dtr_rts_reach_dut` and one that says
true is refused before it gets here.)

Only the rates the kernel names a constant for are accepted, and 1200 is not one of them: opening a port at 1200 baud
is the bootloader touch, which is `devices/entry.py`'s and nobody else's.
"""
import errno
import fcntl
import os
import select
import termios
import time

SPEEDS = {9600: termios.B9600, 19200: termios.B19200, 38400: termios.B38400, 57600: termios.B57600,
          115200: termios.B115200, 230400: termios.B230400, 460800: termios.B460800, 921600: termios.B921600}

# One read takes at most this much. 4 096 bytes is a page; at the fastest rate (921 600 baud, about 92 kB/s) that is
# 44 ms of line, so a burst is drained in a few reads and the broker never holds the descriptor for long.
READ_BYTES = 4096


class PortError(Exception):
    """The port could not be opened or has gone."""


class PortHeld(PortError):
    """Another process holds the port."""


class PortGone(PortError):
    """The adapter was unplugged (or hung up) while open."""


def write_bound_s(n_bytes, baud):
    """A give-up ceiling for writing `n_bytes`. Sending a byte takes 10 bit times (start, 8 data, stop), so the line
    needs n x 10 / baud seconds. The bound is twice that, for scheduling and USB latency, plus 0.1 s so a one-byte
    write is not given a bound shorter than a single USB frame's jitter. It only ends a write that cannot finish."""
    return 2.0 * n_bytes * 10.0 / baud + 0.1


class Port(object):
    def __init__(self, node, baud):
        if baud not in SPEEDS:
            raise ValueError("%r is not a rate this module opens a port at" % (baud,))
        self.node, self.baud = node, baud
        try:
            fd = os.open(node, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in (errno.EBUSY, errno.EACCES):
                raise PortHeld("%s is held by another process (%s)" % (node, exc.strerror))
            raise PortError("cannot open %s: %s" % (node, exc.strerror))
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise PortHeld("%s is locked by another process" % node)
            fcntl.ioctl(fd, termios.TIOCEXCL)
            iflag, oflag, cflag, lflag, _is, _os, cc = termios.tcgetattr(fd)
            cflag = (cflag & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.HUPCL
                               | getattr(termios, "CRTSCTS", 0))) | termios.CS8 | termios.CREAD | termios.CLOCAL
            cc[termios.VMIN], cc[termios.VTIME] = 0, 0
            termios.tcsetattr(fd, termios.TCSANOW, [0, 0, cflag, 0, SPEEDS[baud], SPEEDS[baud], cc])
            termios.tcflush(fd, termios.TCIFLUSH)            # what was queued before this open is not this log
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd

    def fileno(self):
        return self._fd

    def read(self):
        """Call this ONLY when `select` has said the descriptor is readable. With VMIN 0 a terminal read returns zero
        bytes both when nothing is waiting and when the line hung up, so after a readable wake zero bytes means the
        adapter is gone (PortGone). Otherwise: the bytes waiting, at most READ_BYTES."""
        try:
            data = os.read(self._fd, READ_BYTES)
        except BlockingIOError:
            return b""
        except OSError as exc:
            raise PortGone("the port went away (%s)" % exc.strerror)
        if data == b"":
            raise PortGone("the port hung up")
        return data

    def write_all(self, data, bound_s):
        """Write every byte or raise: PortGone if the adapter went, PortError if `bound_s` passes first."""
        view, deadline = memoryview(data), time.monotonic() + bound_s
        while view:
            try:
                n = os.write(self._fd, view)
                view = view[n:]
            except BlockingIOError:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise PortError("the write did not finish within %.2f s" % bound_s)
                select.select([], [self._fd], [], left)
            except OSError as exc:
                raise PortGone("the port went away (%s)" % exc.strerror)

    def close(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.close(fd)                                  # releases the lock too
            except OSError:
                pass

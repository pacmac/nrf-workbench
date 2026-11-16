"""Host-wide single-instance lock: one thing at a time may touch a board on this host.

flock, not a recorded pid: the kernel releases it on exit OR death, and a pid file is what went
stale when three instances raced it (the prior art, docs/specs/programmer.md). The text written in
the file is only for OTHER processes to read. flock binds to the inode, so a lock file deleted or
replaced mid-run would hand the next process its own lock and two instances would both believe they
were alone: that is detected and reported as void, never papered over.

The exclusion is OS-level, so a console command and a service request cannot collide even though
they are different processes. A second request is refused at once, naming the holder; it is not
queued, because a queue is an unbounded wait.
"""
import fcntl
import os

DEFAULT_PATH = "/run/lock/nrf-workbench.lock"   # configurable; whether /run/lock exists everywhere is unknown


class LockHeld(Exception):
    def __init__(self, holder):
        Exception.__init__(self, holder)
        self.holder = holder


class LockVoid(Exception):
    pass


class HostLock(object):
    def __init__(self, path=DEFAULT_PATH):
        self.path = path
        self._fh = None
        self._ident = None

    def acquire(self, what="working"):
        parent = os.path.dirname(self.path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            fh.seek(0)
            holder = fh.read().strip() or "another instance"
            fh.close()
            raise LockHeld(holder)
        st = os.fstat(fh.fileno())
        self._fh, self._ident = fh, (st.st_dev, st.st_ino)
        self.publish(what)
        return self

    def publish(self, what):
        """Write what we are doing, and re-check that the lock file is still the one we locked."""
        try:
            st = os.stat(self.path)
            if (st.st_dev, st.st_ino) != self._ident:
                raise OSError("replaced")
        except OSError:
            raise LockVoid("the lock file was replaced: exclusion is void. Nothing was done.")
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write("pid %d %s\n" % (os.getpid(), what))
        self._fh.flush()

    def release(self):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()

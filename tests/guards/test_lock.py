#!/usr/bin/env python3
"""Tests for the host-wide lock. Run:  python3 tests/guards/test_lock.py"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.guards.lock import HostLock, LockHeld, LockVoid  # noqa: E402


class Lock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="nrfwb-lock-")
        self.path = os.path.join(self.dir, "x.lock")

    def test_second_instance_is_refused_and_told_who(self):
        first = HostLock(self.path).acquire("flashing")
        self.addCleanup(first.release)
        with self.assertRaises(LockHeld) as cm:
            HostLock(self.path).acquire("another")
        self.assertIn("flashing", cm.exception.holder)
        self.assertIn("pid %d" % os.getpid(), cm.exception.holder)

    def test_released_lock_can_be_taken_again(self):
        HostLock(self.path).acquire("a").release()
        HostLock(self.path).acquire("b").release()

    def test_a_replaced_lock_file_is_reported_void(self):
        lock = HostLock(self.path).acquire("working")
        self.addCleanup(lock.release)
        os.remove(self.path)                       # deleted mid-run: flock binds to the inode
        open(self.path, "w").close()
        with self.assertRaises(LockVoid) as cm:
            lock.publish("still working")
        self.assertIn("void", str(cm.exception))

    def test_it_is_a_context_manager(self):
        with HostLock(self.path).acquire("ctx"):
            with self.assertRaises(LockHeld):
                HostLock(self.path).acquire("inner")
        HostLock(self.path).acquire("after").release()


if __name__ == "__main__":
    unittest.main(verbosity=2)

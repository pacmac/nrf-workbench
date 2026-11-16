"""Fake sysfs, /dev and /proc trees for the tests, so the identity and entry code run with no hardware.

A character device is made with mknod, which needs privilege; where that is not permitted the test
is SKIPPED with the reason, never faked into a pass.
"""
import os
import shutil
import stat
import tempfile
import unittest

APP_ID = ("239a", "8029")
BOOT_ID = ("239a", "0029")
SERIAL = "TESTSERIAL0001"


class Tree(object):
    """A temporary root holding sys/, dev/ and proc/."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="nrfwb-fake-")
        self.sysfs = os.path.join(self.root, "sys")
        self.dev = os.path.join(self.root, "dev")
        self.proc = os.path.join(self.root, "proc")
        for d in (os.path.join(self.sysfs, "bus", "usb", "devices"), self.dev, self.proc):
            os.makedirs(d)
        self._devnum = 4                      # the kernel gives every (re-)enumeration a NEW number

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    # --- USB devices as sysfs shows them -----------------------------------------------------
    def usb(self, name, ids, product, serial=SERIAL, manufacturer="RAKwireless", tty=None, majmin=None,
            devnum=None):
        d = os.path.join(self.sysfs, "bus", "usb", "devices", name)
        os.makedirs(d, exist_ok=True)
        if devnum != "":                      # "" simulates a host that does not expose it
            self._devnum += 1
            with open(os.path.join(d, "busnum"), "w") as fh:
                fh.write("1\n")
            with open(os.path.join(d, "devnum"), "w") as fh:
                fh.write("%s\n" % (devnum or self._devnum))
        for fname, val in (("idVendor", ids[0]), ("idProduct", ids[1]), ("product", product),
                           ("manufacturer", manufacturer)):
            with open(os.path.join(d, fname), "w") as fh:
                fh.write(val + "\n")
        if serial is not None:
            with open(os.path.join(d, "serial"), "w") as fh:
                fh.write(serial + "\n")
        if tty:
            t = os.path.join(d, name + ":1.0", "tty", tty)
            os.makedirs(t, exist_ok=True)
            with open(os.path.join(t, "dev"), "w") as fh:
                fh.write(majmin + "\n")
        return d

    def unplug(self, name):
        shutil.rmtree(os.path.join(self.sysfs, "bus", "usb", "devices", name), ignore_errors=True)

    def app_board(self, name="1-3", tty="ttyACM0", majmin="166:0", serial=SERIAL, devnum=None):
        return self.usb(name, APP_ID, "WisCore RAK4631 Board", serial=serial, tty=tty, majmin=majmin, devnum=devnum)

    def bootloader_board(self, name="1-3", tty="ttyACM0", majmin="166:0", serial=SERIAL):
        return self.usb(name, BOOT_ID, "WisBlock RAK4631", serial=serial, tty=tty, majmin=majmin)

    # --- nodes -----------------------------------------------------------------------------
    def node(self, name, major, minor):
        path = os.path.join(self.dev, name)
        try:
            os.mknod(path, 0o600 | stat.S_IFCHR, os.makedev(major, minor))
        except (PermissionError, OSError) as exc:
            raise unittest.SkipTest("cannot create a character device here (%s)" % exc)
        return path

    def hold(self, pid, node_path, cmdline):
        """Make /proc/<pid>/fd/3 a link to the node, as a process holding it open would have."""
        fd = os.path.join(self.proc, str(pid), "fd")
        os.makedirs(fd, exist_ok=True)
        os.symlink(node_path, os.path.join(fd, "3"))
        with open(os.path.join(self.proc, str(pid), "cmdline"), "wb") as fh:
            fh.write(cmdline.replace(" ", "\0").encode())


def make_dfu_zip(path, with_manifest=True):
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json" if with_manifest else "notes.txt", "{}")
    return path

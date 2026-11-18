"""One I2C transaction on /dev/i2c-N, with the kernel's own ioctl and no library (docs/specs/i2c.md).

`I2C_RDWR` hands the kernel a write and a read as ONE transaction: the kernel holds the bus for both and puts a repeated start between
them, so no other master or process can come between the register pointer and the data. The device not acknowledging is an
`Nak`, a result and not an error; anything else the kernel says is an `OSError` and propagates.

`buses()` only lists: it reads `/dev` and sysfs and opens nothing.
"""
import ctypes
import errno
import fcntl
import glob
import os
import re
import struct
import time

I2C_TIMEOUT = 0x0702                # in units of 10 ms
I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
ADDR_FIRST, ADDR_LAST = 0x08, 0x77  # 0x00 to 0x07 and 0x78 to 0x7f are reserved by the I2C specification
MAX_BYTES = 256                     # each way: 512 bytes x 9 bits / 100 kHz = 46 ms, comfortably one short transaction
# 200 ms = 4 x the 46 ms the longest allowed transfer takes at 100 kHz: a ceiling that catches a slave stretching the clock for
# ever, not a wait (the call returns when the transfer is done).
TIMEOUT_TICKS = 20
CONFIG_LINE = "dtparam=i2c_arm=on"


class Nak(Exception):
    """The device did not acknowledge. `part` is `write`, `read`, or `transaction` when there were both: the kernel reports one
    error for the whole transaction and does not say which message was refused, and this does not guess."""

    def __init__(self, address, part):
        Exception.__init__(self, "address 0x%02x did not acknowledge the %s (nothing there, a device asleep or still booting, or a "
                           "stuck bus: this driver reports all three the same way)" % (address, part))
        self.address, self.part = address, part


class _Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16), ("len", ctypes.c_uint16), ("buf", ctypes.c_void_p)]


class _Data(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(_Msg)), ("nmsgs", ctypes.c_uint32)]


def parse_hex(text):
    """bytes from "0a ff", "0x0a,0xff" or "0aff". ValueError for anything else, and for more than MAX_BYTES."""
    cleaned = re.sub(r"0x|[\s,:]", "", (text or "").lower())
    if len(cleaned) % 2 or not re.fullmatch(r"[0-9a-f]*", cleaned):
        raise ValueError("write is hexadecimal bytes such as '0a ff'")
    data = bytes.fromhex(cleaned)
    if len(data) > MAX_BYTES:
        raise ValueError("write is at most %d bytes" % MAX_BYTES)
    return data


def buses(devroot="/dev", sysfs="/sys", proc="/proc"):
    """[{bus, node, name, clock_hz}] for every /dev/i2c-N. `clock_hz` is what the device tree says (None if it does not)."""
    out = []
    for node in sorted(glob.glob(os.path.join(devroot, "i2c-*"))):
        m = re.fullmatch(r"i2c-(\d+)", os.path.basename(node))
        if not m:
            continue
        n = int(m.group(1))
        name = _read(os.path.join(sysfs, "class", "i2c-dev", "i2c-%d" % n, "name"))      # the layout the Pi showed, 2026-11-18
        out.append({"bus": n, "node": node, "name": name, "clock_hz": _clock(sysfs, n)})
    return out


def _read(path):
    try:
        with open(path, "r") as fh:
            return fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _clock(sysfs, n):
    """The bus clock from the adapter's device-tree node (a big-endian 32-bit number), or None."""
    path = os.path.join(sysfs, "class", "i2c-dev", "i2c-%d" % n, "device", "of_node", "clock-frequency")
    try:
        with open(path, "rb") as fh:
            raw = fh.read(4)
    except OSError:
        return None
    return struct.unpack(">I", raw)[0] if len(raw) == 4 else None


def transfer(node, address, write=b"", read=0):
    """(bytes read, duration_s). One transaction: the write, then the read after a repeated start. Raises Nak."""
    if not write and not read:
        raise ValueError("a transfer writes, reads, or both")
    fd = os.open(node, os.O_RDWR)
    try:
        try:
            fcntl.ioctl(fd, I2C_TIMEOUT, TIMEOUT_TICKS)
        except OSError:
            pass                                  # an adapter that will not take a timeout still transfers; the kernel's default stands
        wbuf = ctypes.create_string_buffer(bytes(write), max(len(write), 1))
        rbuf = ctypes.create_string_buffer(max(read, 1))
        msgs = (_Msg * 2)()
        n = 0
        if write:
            msgs[n].addr, msgs[n].flags, msgs[n].len, msgs[n].buf = address, 0, len(write), ctypes.cast(wbuf, ctypes.c_void_p)
            n += 1
        if read:
            msgs[n].addr, msgs[n].flags, msgs[n].len, msgs[n].buf = address, I2C_M_RD, read, ctypes.cast(rbuf, ctypes.c_void_p)
            n += 1
        data = _Data(msgs, n)
        started = time.monotonic()
        try:
            fcntl.ioctl(fd, I2C_RDWR, data)
        except OSError as exc:
            # What "nobody answered" looks like depends on the driver. MEASURED on the bench Pi (Pi 4B, kernel 6.18, bcm2835): 111 absent
            # addresses of 112 answered EIO, and the one present device (0x62) answered. So EIO is a NAK here. It can also be a stuck bus,
            # and errno cannot tell the two apart: the result says so rather than pretending.
            if exc.errno in (errno.ENXIO, errno.EREMOTEIO, errno.EIO):
                raise Nak(address, "transaction" if write and read else "write" if write else "read")
            raise
        return bytes(rbuf.raw[:read]), time.monotonic() - started
    finally:
        os.close(fd)

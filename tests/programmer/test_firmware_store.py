#!/usr/bin/env python3
"""Tests for programmer.firmware_store (docs/specs/firmware.md) against a temporary directory.

Every refusal is checked the same way: the store directory listing is identical before and after, so
"nothing was left behind" is asserted, not assumed. Bodies are in-memory streams, including one that
drops the connection and one that records what the store looks like while the body is still arriving.
Run:  python3 tests/programmer/test_firmware_store.py
"""
import errno
import fcntl
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

from nrf_workbench.programmer import firmware_store  # noqa: E402
from nrf_workbench.programmer.firmware_store import Store  # noqa: E402


def pkg(seed=0, image_bytes=1152, softdevice=(182,), manifest=True):
    """A DFU package as bytes. `seed` makes different images hash differently."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if manifest:
            z.writestr("manifest.json", json.dumps({"manifest": {"application": {
                "bin_file": "firmware.bin", "dat_file": "firmware.dat",
                "init_packet_data": {"softdevice_req": list(softdevice), "device_type": 82}}}}))
        z.writestr("firmware.bin", bytes([seed]) * image_bytes)
        z.writestr("firmware.dat", b"\0" * 14)
    return buf.getvalue()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Dropping(io.BytesIO):
    """A connection that dies after `after` bytes."""

    def __init__(self, data, after):
        io.BytesIO.__init__(self, data)
        self.after = after

    def read(self, n=-1):
        if self.tell() >= self.after:
            raise ConnectionResetError("connection reset by peer")
        return io.BytesIO.read(self, min(n, self.after - self.tell()))


class Counting(io.BytesIO):
    def __init__(self, data):
        io.BytesIO.__init__(self, data)
        self.reads = 0

    def read(self, n=-1):
        self.reads += 1
        return io.BytesIO.read(self, n)


class Peeking(io.BytesIO):
    """Records the store directory every time the store asks for more of the body."""

    def __init__(self, data, root):
        io.BytesIO.__init__(self, data)
        self.root, self.seen = root, []

    def read(self, n=-1):
        self.seen.append(sorted(os.listdir(self.root)))
        return io.BytesIO.read(self, min(n, 256))      # small reads, so the store loops


class Clock(object):
    def __init__(self):
        self.t = 1700000000

    def __call__(self):
        self.t += 60
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.parent = self.tmp.name
        self.root = os.path.join(self.parent, "store")
        self.in_use = {}
        self.store = Store(self.root, in_use_by=lambda s: self.in_use.get(s, []), now=Clock())

    def listing(self):
        return sorted(os.listdir(self.root))

    def put(self, data, **kw):
        return self.store.put(io.BytesIO(data), len(data), **kw)

    def plant(self, sha_hex, record=True, image=True):
        """An entry made directly, for ids that cannot be made by hashing."""
        if record:
            with open(os.path.join(self.root, sha_hex + ".json"), "w") as f:
                json.dump({"sha256": sha_hex, "bytes": 1, "name": None, "uploaded_at": "2000-01-01T00:00:00Z"}, f)
        if image:
            with open(os.path.join(self.root, sha_hex + ".zip"), "wb") as f:
                f.write(b"x")


class Put(Base):
    def test_a_valid_package_is_stored_once_with_its_facts(self):
        data = pkg()
        r = self.put(data, name="parking", uploaded_by="bench")
        self.assertTrue(r.ok, r.to_dict())
        d = r.data
        self.assertEqual((d["sha256"], d["bytes"], d["new"]), (sha(data), len(data), True))
        self.assertEqual((d["image_bytes"], d["softdevice_req"], d["packets"]), (1152, [182], 3))
        self.assertEqual((d["name"], d["uploaded_by"]), ("parking", "bench"))
        self.assertEqual(self.listing(), [sha(data) + ".json", sha(data) + ".zip"])
        with open(self.store.image_path(sha(data)), "rb") as f:
            self.assertEqual(f.read(), data)

    def test_the_same_bytes_again_add_nothing_and_change_nothing(self):
        data = pkg()
        first = self.put(data, name="first").data
        before = self.listing()
        r = self.put(data, name="a different label")
        self.assertTrue(r.ok)
        self.assertEqual((r.data["sha256"], r.data["new"]), (first["sha256"], False))
        self.assertEqual(r.data["name"], "first")                 # the record was not rewritten
        self.assertEqual(r.data["uploaded_at"], first["uploaded_at"])
        self.assertEqual(self.listing(), before)

    def test_a_package_for_another_softdevice_is_stored_the_check_is_at_the_flash(self):
        r = self.put(pkg(softdevice=(140,)))
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual(r.data["softdevice_req"], [140])

    def test_a_body_longer_than_its_length_is_read_only_to_its_length(self):
        data = pkg()
        body = io.BytesIO(data + b"JUNK")
        r = self.store.put(body, len(data))
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual(body.tell(), len(data))                  # the extra bytes were never consumed
        with open(self.store.image_path(sha(data)), "rb") as f:
            self.assertEqual(f.read(), data)

    def test_nothing_is_visible_while_the_body_is_still_arriving(self):
        data = pkg()
        body = Peeking(data, self.root)
        r = self.store.put(body, len(data))
        self.assertTrue(r.ok)
        self.assertGreater(len(body.seen), 2)                     # it really was a loop
        for snapshot in body.seen:
            self.assertEqual([n for n in snapshot if not n.startswith(".incoming-")], [], snapshot)
        self.assertIn(True, [any(n.startswith(".incoming-") for n in s) for s in body.seen])   # a temporary
        self.assertEqual(len(self.listing()), 2)                  # and the temporary is gone afterwards

    def test_names_are_data_never_paths(self):
        data = pkg()
        r = self.put(data, name="../../etc/x")
        self.assertTrue(r.ok)
        self.assertEqual(r.data["name"], "../../etc/x")
        self.assertEqual(self.listing(), [sha(data) + ".json", sha(data) + ".zip"])
        self.assertEqual(os.listdir(self.parent), ["store"])      # nothing was created outside the store

    def test_bad_arguments_are_usage_errors_not_results(self):
        data = pkg()
        before = self.listing()
        with self.assertRaises(TypeError):
            self.put(data, name=7)
        with self.assertRaises(TypeError):
            self.put(data, uploaded_by=7)
        with self.assertRaises(ValueError):
            self.put(data, sha256="abc")
        with self.assertRaises(ValueError):
            self.put(data, sha256="z" * 64)
        self.assertEqual(self.listing(), before)

    def test_a_client_checksum_in_capitals_is_accepted(self):
        data = pkg()
        self.assertTrue(self.put(data, sha256=sha(data).upper()).ok)

    def test_the_disk_filling_during_the_write_is_store_full_and_leaves_nothing(self):
        data = pkg()
        real = os.fdopen

        class Full(object):
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def write(self, b):
                raise OSError(errno.ENOSPC, "no space left on device")

        before = self.listing()
        with mock.patch.object(firmware_store.os, "fdopen", lambda fd, mode: Full(real(fd, mode))):
            r = self.put(data)
        self.assertEqual((r.cls, r.code), ("refused", "store_full"), r.to_dict())
        self.assertEqual(self.listing(), before)


class Refusals(Base):
    def refuse(self, code, body, length, **kw):
        before = self.listing()
        r = self.store.put(body, length, **kw)
        self.assertEqual((r.cls, r.code), ("refused", code), r.to_dict())
        self.assertEqual(self.listing(), before, "a refusal left something in the store")
        return r

    def test_not_a_zip(self):
        self.refuse("bad_package", io.BytesIO(b"not a zip"), 9)

    def test_a_zip_with_no_manifest(self):
        data = pkg(manifest=False)
        self.refuse("bad_package", io.BytesIO(data), len(data))

    def test_a_hex_file(self):
        data = b":020000040000FA\n:00000001FF\n"
        self.refuse("bad_package", io.BytesIO(data), len(data))

    def test_a_body_shorter_than_its_length(self):
        data = pkg()
        r = self.refuse("bad_package", io.BytesIO(data[:-5]), len(data))
        self.assertEqual(r.evidence, {"received": len(data) - 5, "expected": len(data)})

    def test_a_connection_dropped_mid_body(self):
        data = pkg()
        r = self.refuse("bad_package", Dropping(data, after=100), len(data))
        self.assertEqual(r.evidence["received"], 100)

    def test_a_wrong_checksum_from_the_client(self):
        data = pkg()
        r = self.refuse("checksum_mismatch", io.BytesIO(data), len(data), sha256="0" * 64)
        self.assertEqual((r.evidence["sent"], r.evidence["received"]), ("0" * 64, sha(data)))

    def test_the_checksum_is_checked_before_the_package(self):
        # Not a zip AND a wrong checksum: the transfer being corrupt is the first thing to say.
        self.refuse("checksum_mismatch", io.BytesIO(b"not a zip"), 9, sha256="0" * 64)

    def test_no_length_means_no_upload(self):
        body = Counting(pkg())
        self.refuse("length_required", body, None)
        self.assertEqual(body.reads, 0)

    def test_a_boolean_or_negative_length_is_no_length(self):
        self.refuse("length_required", Counting(pkg()), True)
        self.refuse("length_required", Counting(pkg()), -1)

    def test_too_big_for_the_free_space_is_refused_before_a_byte_is_read(self):
        small = Store(self.root, free_bytes=lambda: 10)
        data = pkg()
        body = Counting(data)
        before = self.listing()
        r = small.put(body, len(data))
        self.assertEqual((r.cls, r.code), ("refused", "store_full"))
        self.assertEqual(r.evidence, {"needed": len(data), "free": 10})
        self.assertEqual(body.reads, 0)
        self.assertEqual(self.listing(), before)

    def test_exactly_the_free_space_is_allowed(self):
        data = pkg()
        exact = Store(self.root, free_bytes=lambda: len(data))
        self.assertTrue(exact.put(io.BytesIO(data), len(data)).ok)

    def test_a_refusal_does_not_leak_the_store_path(self):
        r = self.refuse("bad_package", io.BytesIO(b"not a zip"), 9)
        self.assertNotIn(self.root, json.dumps(r.to_dict()))


class Resolve(Base):
    def test_a_full_id_and_an_eight_character_prefix(self):
        data = pkg()
        s = self.put(data).data["sha256"]
        self.assertEqual(self.store.resolve(s), (s, None))
        self.assertEqual(self.store.resolve(s[:8]), (s, None))

    def test_a_short_unknown_malformed_or_hostile_id_is_firmware_unknown(self):
        s = self.put(pkg()).data["sha256"]
        for bad in (s[:7], "deadbeef", "DEADBEEF", s[:8].upper(), "../" + s[:8], "", None, 12345678, s + "0"):
            sha_hex, refusal = self.store.resolve(bad)
            self.assertIsNone(sha_hex, bad)
            self.assertEqual((refusal.cls, refusal.code), ("refused", "firmware_unknown"), bad)

    def test_an_ambiguous_prefix_names_the_candidates(self):
        a, b = "aaaaaaaa" + "1" * 56, "aaaaaaaa" + "2" * 56
        self.plant(a)
        self.plant(b)
        sha_hex, refusal = self.store.resolve("aaaaaaaa")
        self.assertIsNone(sha_hex)
        self.assertEqual(refusal.code, "firmware_unknown")
        self.assertEqual(sorted(refusal.evidence["candidates"]), [a, b])
        self.assertEqual(self.store.resolve("aaaaaaaa1")[0], a)       # a longer prefix settles it

    def test_a_file_copied_in_by_hand_is_not_an_entry(self):
        data = pkg()
        with open(os.path.join(self.root, sha(data) + ".zip"), "wb") as f:
            f.write(data)
        self.assertEqual(self.store.list().data["items"], [])
        self.assertEqual(self.store.resolve(sha(data))[1].code, "firmware_unknown")
        self.assertEqual(self.store.verify(sha(data)).code, "firmware_unknown")


class Reading(Base):
    def test_list_is_newest_first(self):
        old = self.put(pkg(seed=1)).data["sha256"]
        new = self.put(pkg(seed=2)).data["sha256"]
        self.assertEqual([i["sha256"] for i in self.store.list().data["items"]], [new, old])

    def test_an_unreadable_record_is_reported_not_hidden_and_not_listed(self):
        good = self.put(pkg()).data["sha256"]
        bad = "b" * 64
        with open(os.path.join(self.root, bad + ".json"), "w") as f:
            f.write("{ not json")
        r = self.store.list()
        self.assertEqual([i["sha256"] for i in r.data["items"]], [good])
        self.assertEqual(r.evidence["unreadable"], [bad])

    def test_a_record_whose_id_is_not_its_file_name_is_unreadable(self):
        other = "c" * 64
        with open(os.path.join(self.root, other + ".json"), "w") as f:
            json.dump({"sha256": "d" * 64}, f)
        self.assertEqual(self.store.list().evidence["unreadable"], [other])

    def test_show_says_which_units_use_it(self):
        s = self.put(pkg()).data["sha256"]
        self.in_use[s] = ["unit-a"]
        r = self.store.show(s[:8])
        self.assertTrue(r.ok)
        self.assertEqual(r.data["used_by"], ["unit-a"])
        self.assertEqual(r.data["sha256"], s)


class Verify(Base):
    def test_an_intact_entry_verifies_and_gives_its_path(self):
        s = self.put(pkg()).data["sha256"]
        r = self.store.verify(s)
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual(r.data["path"], self.store.image_path(s))
        self.assertEqual(r.data["record"]["sha256"], s)

    def test_a_byte_changed_after_upload_is_refused(self):
        s = self.put(pkg()).data["sha256"]
        path = self.store.image_path(s)
        with open(path, "r+b") as f:
            f.seek(10)
            b = f.read(1)
            f.seek(10)
            f.write(bytes([b[0] ^ 0xFF]))
        r = self.store.verify(s)
        self.assertEqual((r.cls, r.code), ("refused", "bad_package"), r.to_dict())
        self.assertEqual(r.evidence["id"], s)
        self.assertNotEqual(r.evidence["actual"], s)

    def test_a_record_with_no_image_is_refused(self):
        s = self.put(pkg()).data["sha256"]
        os.unlink(self.store.image_path(s))
        self.assertEqual(self.store.verify(s).code, "bad_package")

    def test_an_image_with_a_record_that_cannot_be_read_is_refused(self):
        s = self.put(pkg()).data["sha256"]
        with open(os.path.join(self.root, s + ".json"), "w") as f:
            f.write("nope")
        # It still resolves (list reports it as unreadable rather than hiding it) but it is never flashed.
        r = self.store.verify(s)
        self.assertEqual((r.cls, r.code), ("refused", "bad_package"), r.to_dict())
        self.assertEqual(self.store.list().evidence["unreadable"], [s])


class Delete(Base):
    def test_a_delete_removes_both_files(self):
        s = self.put(pkg()).data["sha256"]
        r = self.store.delete(s[:8])
        self.assertTrue(r.ok)
        self.assertEqual((r.data["id"], self.listing()), (s, []))

    def test_a_declared_return_package_cannot_be_deleted(self):
        s = self.put(pkg()).data["sha256"]
        self.in_use[s] = ["unit-a", "unit-b"]
        r = self.store.delete(s)
        self.assertEqual((r.cls, r.code), ("refused", "in_use"))
        self.assertEqual(r.evidence["units"], ["unit-a", "unit-b"])
        self.assertEqual(len(self.listing()), 2)                      # untouched

    def test_an_unknown_id_is_firmware_unknown(self):
        self.assertEqual(self.store.delete("deadbeef").code, "firmware_unknown")

    def test_an_image_left_without_a_record_is_not_listed_and_a_new_upload_completes_it(self):
        data = pkg()
        s = self.put(data).data["sha256"]
        os.unlink(os.path.join(self.root, s + ".json"))               # a crash between the two files
        self.assertEqual(self.store.list().data["items"], [])
        r = self.put(data)
        self.assertTrue(r.ok)
        self.assertTrue(r.data["new"])
        self.assertEqual(len(self.listing()), 2)
        self.assertTrue(self.store.verify(s).ok)


class Stale(Base):
    def test_an_abandoned_temporary_is_removed_at_start(self):
        path = os.path.join(self.root, ".incoming-abandoned.part")
        with open(path, "wb") as f:
            f.write(b"half a file")
        Store(self.root)
        self.assertFalse(os.path.exists(path))

    def test_a_temporary_still_being_written_is_left_alone(self):
        path = os.path.join(self.root, ".incoming-live.part")
        with open(path, "wb") as f:
            fcntl.flock(f, fcntl.LOCK_EX)                             # what a live writer holds
            Store(self.root)
            self.assertTrue(os.path.exists(path))
        Store(self.root)                                              # the writer is gone: now it is stale
        self.assertFalse(os.path.exists(path))

    def test_a_live_put_is_not_cleaned_away_by_a_second_store(self):
        data = pkg()
        other = []

        class Body(io.BytesIO):
            def read(inner, n=-1):
                if not other:
                    other.append(Store(self.root))                    # a second store starts mid-upload
                return io.BytesIO.read(inner, n)

        r = self.store.put(Body(data), len(data))
        self.assertTrue(r.ok, r.to_dict())

    def test_a_live_put_is_still_protected_after_the_body_is_fully_written(self):
        # The window between the write handle closing and the final move: a second store starting
        # here must not delete the file about to be moved.
        data = pkg()
        real = firmware_store.checks.dfu_package
        seen = {}

        def check(profile, path):
            Store(self.root)                                          # starts while the package is checked
            seen["still_there"] = os.path.exists(path)
            return real(profile, path)

        with mock.patch.object(firmware_store.checks, "dfu_package", check):
            r = self.put(data)
        self.assertTrue(seen["still_there"])
        self.assertTrue(r.ok, r.to_dict())


class Concurrent(Base):
    def test_two_uploads_of_the_same_bytes_end_as_one_entry(self):
        data = pkg()
        results = []

        def go():
            results.append(self.store.put(io.BytesIO(data), len(data)))

        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r.ok for r in results), [r.to_dict() for r in results])
        self.assertEqual(self.listing(), [sha(data) + ".json", sha(data) + ".zip"])
        self.assertTrue(self.store.verify(sha(data)).ok)


if __name__ == "__main__":
    unittest.main(verbosity=1)

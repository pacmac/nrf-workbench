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
    # A FIXED timestamp: a zip made with the current time hashes differently every two seconds, which made a
    # test that depended on the first hex characters of the hash pass or fail by the clock.
    def entry(name):
        return zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if manifest:
            z.writestr(entry("manifest.json"), json.dumps({"manifest": {"application": {
                "bin_file": "firmware.bin", "dat_file": "firmware.dat",
                "init_packet_data": {"softdevice_req": list(softdevice), "device_type": 82}}}}))
        z.writestr(entry("firmware.bin"), bytes([seed]) * image_bytes)
        z.writestr(entry("firmware.dat"), b"\0" * 14)
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
        self.pdir = os.path.join(self.root, "unsorted")          # the default project's folder

    def listing(self):
        """Everything in the store, relative to it, folders included, so "a refusal left nothing behind" also
        means no project folder was left behind."""
        out = []
        for dirpath, dirs, files in os.walk(self.root):
            for n in dirs + files:
                out.append(os.path.relpath(os.path.join(dirpath, n), self.root))
        return sorted(out)

    def entry_files(self, sha_hex, project="unsorted"):
        """What one stored package looks like in a listing."""
        return sorted([project, "%s/%s.json" % (project, sha_hex), "%s/%s.zip" % (project, sha_hex)])

    def put(self, data, **kw):
        return self.store.put(io.BytesIO(data), len(data), **kw)

    def plant(self, sha_hex, record=True, image=True, project="unsorted"):
        """An entry made directly, for ids that cannot be made by hashing."""
        folder = os.path.join(self.root, project)
        os.makedirs(folder, exist_ok=True)
        if record:
            with open(os.path.join(folder, sha_hex + ".json"), "w") as f:
                json.dump({"sha256": sha_hex, "bytes": 1, "name": None, "uploaded_at": "2000-01-01T00:00:00Z"}, f)
        if image:
            with open(os.path.join(folder, sha_hex + ".zip"), "wb") as f:
                f.write(b"x")


class Put(Base):
    def test_a_valid_package_is_stored_once_with_its_facts(self):
        data = pkg()
        r = self.put(data, name="parking", uploaded_by="bench")
        self.assertTrue(r.ok, r.to_dict())
        d = r.data
        self.assertEqual((d["sha256"], d["bytes"], d["new"]), (sha(data), len(data), True))
        self.assertEqual((d["image_bytes"], d["softdevice_req"], d["packets"]), (1152, [182], 3))
        self.assertEqual((d["name"], d["uploaded_by"], d["project"]), ("parking", "bench", "unsorted"))
        self.assertEqual(self.listing(), self.entry_files(sha(data)))
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
        self.assertEqual(self.listing(), self.entry_files(sha(data)))   # and the temporary is gone afterwards

    def test_names_are_data_never_paths(self):
        data = pkg()
        r = self.put(data, name="../../etc/x")
        self.assertTrue(r.ok)
        self.assertEqual(r.data["name"], "../../etc/x")
        self.assertEqual(self.listing(), self.entry_files(sha(data)))
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
        letters = "abcdef12" + "0" * 56             # an id that certainly has letters, so its capitals differ
        self.plant(letters)
        self.assertEqual(self.store.resolve("abcdef12"), (letters, None))
        for bad in (s[:7], "deadbeef", "DEADBEEF", "ABCDEF12", "../" + s[:8], "", None, 12345678, s + "0"):
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
        with open(os.path.join(self.pdir, bad + ".json"), "w") as f:
            f.write("{ not json")
        r = self.store.list()
        self.assertEqual([i["sha256"] for i in r.data["items"]], [good])
        self.assertEqual(r.evidence["unreadable"], [bad])

    def test_a_record_whose_id_is_not_its_file_name_is_unreadable(self):
        other = "c" * 64
        os.makedirs(self.pdir)
        with open(os.path.join(self.pdir, other + ".json"), "w") as f:
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
        with open(os.path.join(self.pdir, s + ".json"), "w") as f:
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
        self.assertEqual((r.data["id"], self.listing()), (s, ["unsorted"]))     # the project folder stays, empty

    def test_a_declared_return_package_cannot_be_deleted(self):
        s = self.put(pkg()).data["sha256"]
        self.in_use[s] = ["unit-a", "unit-b"]
        r = self.store.delete(s)
        self.assertEqual((r.cls, r.code), ("refused", "in_use"))
        self.assertEqual(r.evidence["units"], ["unit-a", "unit-b"])
        self.assertEqual(self.listing(), self.entry_files(s))         # untouched

    def test_an_unknown_id_is_firmware_unknown(self):
        self.assertEqual(self.store.delete("deadbeef").code, "firmware_unknown")

    def test_an_image_left_without_a_record_is_not_listed_and_a_new_upload_completes_it(self):
        data = pkg()
        s = self.put(data).data["sha256"]
        os.unlink(os.path.join(self.pdir, s + ".json"))               # a crash between the two files
        self.assertEqual(self.store.list().data["items"], [])
        r = self.put(data)
        self.assertTrue(r.ok)
        self.assertTrue(r.data["new"])
        self.assertEqual(self.listing(), self.entry_files(s))
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
        self.assertEqual(self.listing(), self.entry_files(sha(data)))
        self.assertTrue(self.store.verify(sha(data)).ok)


GOOD_PROJECTS = ["a", "mt-alarm", "v1.2_x", "9lives", "testpartner", "a" * 64]
BAD_PROJECTS = ["", ".", "..", "../x", "x/..", "a/b", "/abs", "A", "Mt-Alarm", "-x", "_x", ".hidden", "a b",
                "a" * 65, "é", "a\x00b", "a\nb", "a\\b", "~root", "unsorted/"]


class ProjectRule(Base):
    """The one rule that lets a client string become a folder name (docs/specs/firmware.md, "projects")."""

    def test_the_rule(self):
        for name in GOOD_PROJECTS:
            self.assertIsNone(firmware_store.check_project(name), name)
        for name in BAD_PROJECTS:
            r = firmware_store.check_project(name)
            self.assertEqual((r.cls, r.code), ("refused", "bad_project"), repr(name))

    def test_a_non_string_is_a_usage_error_not_a_result(self):
        for bad in (7, b"a", ["a"], True):
            with self.assertRaises(TypeError):
                firmware_store.check_project(bad)
            with self.assertRaises(TypeError):
                self.put(pkg(), project=bad)

    def test_a_bad_name_costs_no_upload_and_touches_nothing(self):
        data = pkg()
        for name in BAD_PROJECTS:
            body, before = Counting(data), self.listing()
            r = self.store.put(body, len(data), project=name)
            self.assertEqual((r.cls, r.code), ("refused", "bad_project"), repr(name))
            self.assertEqual(body.reads, 0, "the body was read for a bad name: %r" % name)
            self.assertEqual(self.listing(), before, repr(name))
            self.assertEqual(os.listdir(self.parent), ["store"], "something was made outside the store")

    def test_a_good_name_is_a_real_folder_and_the_record_does_not_repeat_it(self):
        data = pkg()
        r = self.put(data, project="mt-alarm")
        self.assertEqual((r.data["project"], r.data["new"]), ("mt-alarm", True))
        self.assertEqual(self.listing(), self.entry_files(sha(data), "mt-alarm"))
        with open(os.path.join(self.root, "mt-alarm", sha(data) + ".json")) as f:
            self.assertNotIn("project", json.load(f), "the folder is the one source of truth")
        self.assertEqual(os.stat(os.path.join(self.root, "mt-alarm")).st_mode & 0o777, 0o700)

    def test_a_link_or_a_file_named_like_a_project_is_refused_and_never_written_through(self):
        outside = os.path.join(self.parent, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.root, "escape"))
        with open(os.path.join(self.root, "afile"), "w") as f:
            f.write("x")
        self.assertEqual(self.store.projects(), [], "a link is not a project")
        data = pkg()
        self.assertTrue(self.put(data, project="mt-alarm").ok)                # something real to move
        s = sha(data)
        for name in ("escape", "afile"):
            for r in (self.put(pkg(seed=9), project=name), self.store.move(s, name), self.store.project_new(name)):
                self.assertEqual((r.cls, r.code), ("refused", "bad_project"), name)
        self.assertEqual(os.listdir(outside), [], "something was written through the link")
        self.assertEqual(self.store.list().data["items"][0]["project"], "mt-alarm")


class Projects(Base):
    def test_a_put_with_no_project_goes_to_unsorted(self):
        r = self.put(pkg())
        self.assertEqual(r.data["project"], "unsorted")

    def test_list_names_each_packages_project_and_every_project_empty_ones_too(self):
        a = self.put(pkg(seed=1), project="alpha").data["sha256"]
        b = self.put(pkg(seed=2), project="beta").data["sha256"]
        self.assertTrue(self.store.project_new("empty").ok)
        data = self.store.list().data
        self.assertEqual(data["projects"], ["alpha", "beta", "empty"])
        self.assertEqual(dict((i["sha256"], i["project"]) for i in data["items"]), {a: "alpha", b: "beta"})

    def test_the_same_bytes_into_another_project_are_not_copied_and_say_where_they_are(self):
        data = pkg()
        first = self.put(data, project="alpha").data
        before = self.listing()
        r = self.put(data, project="beta")
        self.assertTrue(r.ok)
        self.assertEqual((r.data["new"], r.data["project"], r.data["sha256"]), (False, "alpha", first["sha256"]))
        self.assertIn("already stored in project alpha", r.message)
        self.assertEqual(self.listing(), before, "no second copy, and no folder for the project that was asked for")

    def test_show_and_verify_name_the_project_and_the_path_is_inside_it(self):
        s = self.put(pkg(), project="alpha").data["sha256"]
        self.assertEqual(self.store.show(s).data["project"], "alpha")
        v = self.store.verify(s)
        self.assertEqual(v.data["project"], "alpha")
        self.assertEqual(v.data["path"], os.path.join(self.root, "alpha", s + ".zip"))
        self.assertEqual(self.store.image_path(s), v.data["path"])

    def test_an_ambiguous_prefix_across_projects_names_the_candidates(self):
        a, b = "aaaaaaaa" + "1" * 56, "aaaaaaaa" + "2" * 56
        self.plant(a, project="x")
        self.plant(b, project="y")
        sha_hex, refusal = self.store.resolve("aaaaaaaa")
        self.assertIsNone(sha_hex)
        self.assertEqual(sorted(refusal.evidence["candidates"]), [a, b])
        self.assertEqual(self.store.resolve("aaaaaaaa2")[0], b)

    def test_the_same_hash_copied_into_two_projects_by_hand_is_one_entry_the_first_name_wins(self):
        s = "e" * 64
        self.plant(s, project="y")
        self.plant(s, project="x")
        items = self.store.list().data["items"]
        self.assertEqual([(i["sha256"], i["project"]) for i in items], [(s, "x")])

    def test_a_folder_that_breaks_the_rule_is_not_a_project(self):
        os.makedirs(os.path.join(self.root, "Not A Project"))
        os.makedirs(os.path.join(self.root, ".hidden"))
        self.assertEqual(self.store.projects(), [])


class Move(Base):
    def one(self, project="a"):
        data = pkg()
        return sha(data), self.put(data, project=project)

    def test_a_move_files_the_package_under_another_project_and_the_id_stays(self):
        s, _ = self.one("a")
        r = self.store.move(s[:8], "b")
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual((r.data["id"], r.data["from"], r.data["project"]), (s, "a", "b"))
        self.assertEqual(self.listing(), sorted(self.entry_files(s, "b") + ["a"]))      # "a" is left, empty
        v = self.store.verify(s)
        self.assertTrue(v.ok, v.to_dict())
        self.assertEqual(self.store.list().data["items"][0]["project"], "b")

    def test_moving_to_its_own_project_changes_nothing(self):
        s, _ = self.one("a")
        before = self.listing()
        r = self.store.move(s, "a")
        self.assertTrue(r.ok)
        self.assertEqual(r.message, "already in that project")
        self.assertEqual(self.listing(), before)

    def test_a_declared_return_package_cannot_be_moved(self):
        s, _ = self.one("a")
        self.in_use[s] = ["unit-a"]
        before = self.listing()
        r = self.store.move(s, "b")
        self.assertEqual((r.cls, r.code), ("refused", "in_use"))
        self.assertEqual(r.evidence["units"], ["unit-a"])
        self.assertEqual(self.listing(), before, "a refused move changed something")

    def test_every_refusal_leaves_the_store_as_it_was(self):
        s, _ = self.one("a")
        before = self.listing()
        for ident, project, code in (("deadbeef", "b", "firmware_unknown"), (s, "../x", "bad_project"),
                                     ("zz", "b", "firmware_unknown")):
            r = self.store.move(ident, project)
            self.assertEqual((r.cls, r.code), ("refused", code), (ident, project))
            self.assertEqual(self.listing(), before)
        os.unlink(os.path.join(self.root, "a", s + ".zip"))
        before = self.listing()
        r = self.store.move(s, "b")
        self.assertEqual((r.cls, r.code), ("refused", "bad_package"))
        self.assertEqual(self.listing(), before, "a move with no image made a folder or moved a record")

    def test_a_crash_after_the_link_leaves_the_old_entry_whole_and_a_retry_finishes(self):
        s, _ = self.one("a")
        real = os.replace

        def boom(src, dst, *a, **k):
            if str(dst).endswith(".json") and os.sep + "b" + os.sep in str(dst):
                raise OSError("power cut")
            return real(src, dst, *a, **k)

        with mock.patch.object(firmware_store.os, "replace", boom):
            with self.assertRaises(OSError):
                self.store.move(s, "b")
        self.assertEqual(self.store.list().data["items"][0]["project"], "a", "the old entry is still the listed one")
        self.assertTrue(self.store.verify(s).ok)
        self.assertTrue(os.path.exists(os.path.join(self.root, "b", s + ".zip")), "the link was made")
        self.assertEqual(self.store.list().evidence["unreadable"], [])
        r = self.store.move(s, "b")                                   # the link a crash left is the same file
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual(self.listing(), sorted(self.entry_files(s, "b") + ["a"]))

    def test_a_crash_after_the_record_moved_leaves_the_new_entry_whole(self):
        s, _ = self.one("a")
        real = os.unlink

        def boom(path, *a, **k):
            if str(path).endswith(os.path.join("a", s + ".zip")):
                raise OSError("power cut")
            return real(path, *a, **k)

        with mock.patch.object(firmware_store.os, "unlink", boom):
            with self.assertRaises(OSError):
                self.store.move(s, "b")
        item = self.store.list().data["items"][0]
        self.assertEqual(item["project"], "b")
        self.assertTrue(self.store.verify(s).ok)
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", s + ".zip")), "an unlisted image is left")
        self.assertEqual(len(self.store.list().data["items"]), 1)


class ProjectFolders(Base):
    def test_new_makes_an_empty_folder_and_says_so_only_the_first_time(self):
        r = self.store.project_new("alpha")
        self.assertEqual((r.ok, r.data["new"]), (True, True))
        self.assertEqual(self.store.project_new("alpha").data["new"], False)
        self.assertEqual((self.listing(), self.store.projects()), (["alpha"], ["alpha"]))

    def test_new_refuses_a_bad_name(self):
        for name in BAD_PROJECTS:
            self.assertEqual(self.store.project_new(name).code, "bad_project", repr(name))
        self.assertEqual(self.listing(), [])

    def test_delete_removes_an_empty_project(self):
        self.store.project_new("alpha")
        r = self.store.project_delete("alpha")
        self.assertTrue(r.ok, r.to_dict())
        self.assertEqual(self.listing(), [])

    def test_delete_refuses_a_project_that_holds_packages_and_loses_nothing(self):
        data = pkg()
        self.put(data, project="alpha")
        self.put(pkg(seed=2), project="alpha")
        before = self.listing()
        r = self.store.project_delete("alpha")
        self.assertEqual((r.cls, r.code, r.evidence["packages"]), ("refused", "project_not_empty", 2))
        self.assertEqual(self.listing(), before)
        self.assertTrue(self.store.verify(sha(data)).ok)

    def test_delete_refuses_a_folder_with_files_that_are_not_packages_and_names_them(self):
        self.store.project_new("alpha")
        with open(os.path.join(self.root, "alpha", "notes.txt"), "w") as f:
            f.write("mine")
        r = self.store.project_delete("alpha")
        self.assertEqual((r.cls, r.code), ("refused", "project_not_empty"))
        self.assertEqual(r.evidence["files"], ["notes.txt"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "alpha", "notes.txt")))

    def test_delete_of_an_unknown_or_badly_named_project(self):
        self.assertEqual(self.store.project_delete("nothere").code, "project_unknown")
        self.assertEqual(self.store.project_delete("../store").code, "bad_project")
        self.assertEqual(self.store.project_delete(".").code, "bad_project")


class Filing(unittest.TestCase):
    """A store from before project folders: entries sit directly in the store folder (docs/specs/firmware.md)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "store")
        os.makedirs(self.root)

    def flat(self, seed, record=True, image=True, where=None):
        data = pkg(seed=seed)
        s = sha(data)
        where = where or self.root
        os.makedirs(where, exist_ok=True)
        if image:
            with open(os.path.join(where, s + ".zip"), "wb") as f:
                f.write(data)
        if record:
            with open(os.path.join(where, s + ".json"), "w") as f:
                json.dump({"sha256": s, "bytes": len(data), "name": "old%d" % seed, "uploaded_at": "2026-11-16T10:00:00Z",
                           "image_bytes": 1152, "softdevice_req": [182], "packets": 3}, f)
        return s

    def names(self):
        return sorted(n for n in os.listdir(self.root))

    def test_flat_entries_are_filed_into_unsorted_when_the_store_opens(self):
        a, b = self.flat(1), self.flat(2)
        store = Store(self.root)
        self.assertEqual(store.filed, 2)
        self.assertEqual(self.names(), ["unsorted"], "nothing is left loose in the store")
        self.assertEqual(sorted(os.listdir(os.path.join(self.root, "unsorted"))),
                         sorted([a + ".json", a + ".zip", b + ".json", b + ".zip"]))
        listing = store.list()
        self.assertEqual(sorted(i["sha256"] for i in listing.data["items"]), sorted([a, b]))
        self.assertEqual(set(i["project"] for i in listing.data["items"]), {"unsorted"})
        self.assertEqual(listing.evidence["filed_on_open"], 2)
        self.assertTrue(store.verify(a).ok)

    def test_a_second_open_finds_nothing_to_file(self):
        self.flat(1)
        Store(self.root)
        again = Store(self.root)
        self.assertEqual(again.filed, 0)
        self.assertNotIn("filed_on_open", again.list().evidence)

    def test_a_pair_a_crash_left_half_moved_is_finished(self):
        a = self.flat(1, image=False, where=os.path.join(self.root, "unsorted"))       # record moved, image not
        with open(os.path.join(self.root, a + ".zip"), "wb") as f:
            f.write(pkg(seed=1))
        b = self.flat(2)                                                               # image linked, record not
        os.makedirs(os.path.join(self.root, "unsorted"), exist_ok=True)
        os.link(os.path.join(self.root, b + ".zip"), os.path.join(self.root, "unsorted", b + ".zip"))
        store = Store(self.root)
        self.assertEqual(self.names(), ["unsorted"])
        self.assertTrue(store.verify(a).ok)
        self.assertTrue(store.verify(b).ok)
        self.assertEqual(len(store.list().data["items"]), 2)

    def test_a_record_with_no_image_is_still_filed_and_still_refused_at_the_flash(self):
        a = self.flat(1, image=False)
        store = Store(self.root)
        self.assertEqual(store.filed, 1)
        self.assertEqual(store.verify(a).code, "bad_package")

    def test_an_image_copied_in_by_hand_with_no_record_is_left_alone(self):
        a = self.flat(1, record=False)
        store = Store(self.root)
        self.assertEqual(store.filed, 0)
        self.assertEqual(self.names(), [a + ".zip"])
        self.assertEqual(store.list().data["items"], [])


class ConcurrentProjects(Base):
    def test_the_same_bytes_into_different_projects_at_once_end_as_one_entry_in_one_project(self):
        data = pkg()
        results = []

        def go(project):
            results.append(self.store.put(io.BytesIO(data), len(data), project=project))

        threads = [threading.Thread(target=go, args=(p,)) for p in ("a", "b", "c", "d", "e", "f")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r.ok for r in results), [r.to_dict() for r in results])
        winners = [r for r in results if r.data["new"]]
        self.assertEqual(len(winners), 1, "exactly one upload made the entry")
        where = winners[0].data["project"]
        self.assertEqual(set(r.data["project"] for r in results), {where}, "the others were told where it is")
        self.assertEqual(self.listing(), self.entry_files(sha(data), where), "one copy, one folder")


if __name__ == "__main__":
    unittest.main(verbosity=1)

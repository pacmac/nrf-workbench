#!/usr/bin/env python3
"""The real registered commands behind real HTTP: upload, list, flash by id and flash in one call.

The board, the touch and the uploader are the fakes the programmer tests use; everything else is the
production path: the registry, the generated routes, the streamed raw body, the store, flash_firmware.
Run:  python3 tests/api/test_integration.py
"""
import hashlib
import os
import sys
import unittest

from starlette.testclient import TestClient

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "programmer"))
sys.path.insert(0, os.path.join(ROOT, "tests", "devices"))

from fakes import SERIAL  # noqa: E402
from nrf_workbench.api.app import build_app  # noqa: E402
from nrf_workbench.api.auth import Auth  # noqa: E402
from test_commands import ALL, Base as CommandsBase  # noqa: E402

FLASH = {"serial": SERIAL, "enter_bound_s": "5", "flash_bound_s": "60"}


class Http(CommandsBase):
    def setUp(self):
        CommandsBase.setUp(self)
        self.app = build_app(self.reg, self.host, Auth(None, "token"), ALL)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.t.app_board()

    def items(self):
        return [i["sha256"] for i in self.client.get("/api/firmware/list").json()["data"]["items"]]

    def chunked(self):
        def gen():
            for i in range(0, len(self.data), 700):
                yield self.data[i:i + 700]
        return gen()


class Store(Http):
    def test_upload_list_show_delete_over_http(self):
        r = self.client.post("/api/firmware/put", params={"name": "parking"}, content=self.data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["data"]["sha256"], r.json()["data"]["new"]), (self.id, True))
        self.assertEqual(self.items(), [self.id])
        shown = self.client.get("/api/firmware/show", params={"id": self.id[:8]}).json()["data"]
        self.assertEqual((shown["name"], shown["used_by"]), ("parking", []))
        self.assertEqual(self.client.post("/api/firmware/delete", json={"id": self.id}).status_code, 200)
        self.assertEqual(self.items(), [])

    def test_the_same_upload_twice_is_one_entry(self):
        self.client.post("/api/firmware/put", content=self.data)
        r = self.client.post("/api/firmware/put", content=self.data)
        self.assertEqual((r.status_code, r.json()["data"]["new"]), (200, False))
        self.assertEqual(self.items(), [self.id])

    def test_the_statuses_a_caller_will_actually_see(self):
        self.assertEqual(self.client.post("/api/firmware/put", content=b"not a zip").status_code, 422)
        self.assertEqual(self.client.post("/api/firmware/put", params={"sha256": "0" * 64}, content=self.data).status_code, 422)
        self.assertEqual(self.client.post("/api/firmware/put", params={"sha256": "abc"}, content=self.data).status_code, 400)
        self.assertEqual(self.client.post("/api/firmware/put", content=self.chunked()).status_code, 411)
        self.assertEqual(self.client.get("/api/firmware/show", params={"id": "deadbeef"}).status_code, 404)
        self.assertEqual(self.items(), [], "every one of those left the store empty")

    def test_a_correct_client_checksum_is_accepted(self):
        r = self.client.post("/api/firmware/put", params={"sha256": hashlib.sha256(self.data).hexdigest()},
                             content=self.data)
        self.assertEqual(r.status_code, 200)


class Flash(Http):
    def test_flash_by_id(self):
        self.client.post("/api/firmware/put", content=self.data)
        r = self.client.post("/api/programmer/flash", json={"serial": SERIAL, "firmware": self.id,
                                                            "enter_bound_s": 5, "flash_bound_s": 60})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["data"]["entered_bootloader"], r.json()["data"]["firmware"]["id"]), (True, self.id))
        self.assertEqual((len(self.touch.calls), len(self.runner.calls)), (1, 1))

    def test_flash_in_one_call_streams_the_upload_then_flashes_from_the_store(self):
        r = self.client.post("/api/programmer/flash_stream", params=dict(FLASH, name="parking"), content=self.data)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["upload"], {"id": self.id, "new": True, "bytes": len(self.data)})
        self.assertEqual(self.items(), [self.id])
        cmd = self.runner.calls[0][0]
        self.assertEqual(cmd[cmd.index("-pkg") + 1], os.path.join(self.host.config["paths"]["firmware"], self.id + ".zip"))

    def test_a_chunked_flash_stream_is_refused_before_the_board_is_touched(self):
        r = self.client.post("/api/programmer/flash_stream", params=FLASH, content=self.chunked())
        self.assertEqual((r.status_code, r.json()["code"]), (411, "length_required"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_a_wrong_checksum_and_a_bad_package_never_touch_the_board(self):
        r = self.client.post("/api/programmer/flash_stream", params=dict(FLASH, sha256="0" * 64), content=self.data)
        self.assertEqual((r.status_code, r.json()["code"]), (422, "checksum_mismatch"))
        r = self.client.post("/api/programmer/flash_stream", params=FLASH, content=b"not a zip")
        self.assertEqual((r.status_code, r.json()["code"]), (422, "bad_package"))
        self.assertEqual((self.touch.calls, self.runner.calls), ([], []))

    def test_an_absent_board_is_a_409_and_the_upload_is_kept_for_the_retry(self):
        r = self.client.post("/api/programmer/flash_stream", params=dict(FLASH, serial="NOT-ATTACHED"), content=self.data)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "node_absent"))
        self.assertEqual(r.json()["evidence"]["upload"]["id"], self.id)
        self.assertEqual(self.items(), [self.id])
        retry = self.client.post("/api/programmer/flash", json={"serial": SERIAL, "firmware": self.id,
                                                                "enter_bound_s": 5, "flash_bound_s": 60})
        self.assertEqual(retry.status_code, 200, retry.text)

    def test_a_failed_flash_is_a_500_with_the_board_state_in_the_body(self):
        from test_flash import Runner
        self.host.hooks["runner"] = Runner(rc=1, out="Failed.\n")
        r = self.client.post("/api/programmer/flash_stream", params=FLASH, content=self.data)
        self.assertEqual((r.status_code, r.json()["code"]), (500, "flash_failed"))
        self.assertIs(r.json()["evidence"]["entered_bootloader"], True)

    def test_a_missing_or_unparsable_bound_is_a_400_and_nothing_is_stored_or_touched(self):
        r = self.client.post("/api/programmer/flash_stream", params={"serial": SERIAL}, content=self.data)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/programmer/flash_stream", params=dict(FLASH, flash_bound_s="abc"), content=self.data)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/programmer/flash_stream", params=dict(FLASH, flash_bound_s="inf"), content=self.data)
        self.assertEqual(r.status_code, 400)
        self.assertEqual((self.items(), self.touch.calls, self.runner.calls), ([], [], []))


class Discovery(Http):
    def test_devices_list_and_the_command_list(self):
        (b,) = self.client.get("/api/devices/list").json()["data"]["boards"]
        self.assertEqual((b["serial"], b["state"]), (SERIAL, "app"))
        names = sorted(c["mcp"] for c in self.client.get("/api/commands").json()["commands"])
        self.assertEqual(names, ["devices_list", "firmware_delete", "firmware_list", "firmware_put", "firmware_show",
                                 "programmer_flash", "programmer_flash_stream"])

    def test_without_an_uploader_the_flash_commands_are_absent_and_say_why(self):
        app = build_app(self.reg, self.host, Auth(None, "token"), {"sysfs", "firmware_store"})
        client = TestClient(app)
        self.addCleanup(client.close)
        self.assertEqual(client.post("/api/programmer/flash", json={}).status_code, 404)
        absent = client.get("/api/capabilities").json()["absent"]
        self.assertEqual(sorted(a["name"] for a in absent), ["programmer.flash", "programmer.flash_stream"])
        self.assertEqual(absent[0]["unmet"], ["uploader"])
        self.assertEqual(client.get("/api/firmware/list").status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=1)

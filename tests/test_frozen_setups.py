"""Setups are frozen at build time; the page can neither invent nor change them."""

import copy
import json
import pathlib
import unittest

from helpers import Folder, RunningServer, build, build_into, read_json, sample_spec

import read


def resolved_first() -> dict:
    """What build.setup_of makes of the first sample item's setup — it has no
    `run`, so where its launch would point to does not matter."""
    nowhere = pathlib.Path("/")
    base = build.LaunchBase(layers=(), root=nowhere, data=nowhere)
    return build.setup_of(sample_spec()["items"][0]["setup"], "item 1", base)


class FreezeTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_state_carries_setup_ids(self):
        state = read_json(self.folder.path / "state.json")
        self.assertEqual([item["setup"] for item in state["items"]],
                         ["item-01", None, "item-03"])

    def test_setups_file_holds_resolved_and_raw(self):
        frozen = read_json(self.folder.path / "setups.json")
        self.assertEqual(set(frozen["setups"]), {"item-01", "item-03"})
        self.assertEqual(frozen["setups"]["item-01"], resolved_first())
        self.assertEqual(frozen["setups"]["item-03"]["text"], "Short form text.")
        self.assertEqual(frozen["source"]["inline"], {
            "item-01": sample_spec()["items"][0]["setup"],
            "item-03": "Short form text.",
        })


class FrozenServerTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)
        self.server = RunningServer(self.folder.path)

    def tearDown(self):
        self.server.stop()
        self.folder.cleanup()

    def test_get_injects_resolved_setups(self):
        items = self.server.page_state()["items"]
        self.assertEqual(items[0]["setup"], {"id": "item-01", **resolved_first()})
        self.assertIsNone(items[1]["setup"])
        self.assertEqual(items[2]["setup"]["id"], "item-03")

    def test_put_ignores_client_setup(self):
        state = self.server.page_state()
        state["items"][0]["setup"]["paths"] = [{"path": "/etc", "label": "evil"}]
        state["items"][1]["setup"] = {"text": "invented", "paths": [{"path": "/etc"}]}
        state["items"][2]["setup"] = None
        status, _ = self.server.put_state(state)
        self.assertEqual(status, 200)
        saved = read_json(self.folder.path / "state.json")
        self.assertEqual([item["setup"] for item in saved["items"]],
                         ["item-01", None, "item-03"])
        self.assertEqual(self.server.reveal("/etc")[0], 403)

    def test_put_response_carries_resolved_setups(self):
        status, body = self.server.put_state(self.server.page_state())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["items"][0]["setup"]["id"], "item-01")

    def test_put_with_other_item_count_conflicts(self):
        state = self.server.page_state()
        state["items"].pop()
        status, body = self.server.put_state(state)
        self.assertEqual(status, 409)
        self.assertIn(b"3", body)
        self.assertEqual(len(read_json(self.folder.path / "state.json")["items"]), 3)

    def test_reveal_reads_frozen_setups(self):
        # The state no longer names any path — only setups.json does.
        self.assertEqual(self.server.reveal("/tmp/checklist-test-declared")[0], 404)


class HostTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)
        self.server = RunningServer(self.folder.path)

    def tearDown(self):
        self.server.stop()
        self.folder.cleanup()

    def test_loopback_hosts_with_port_pass(self):
        for host in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"):
            self.assertEqual(self.server.request("GET", "/", host=host)[0], 200, host)

    def test_foreign_host_is_refused_on_every_method(self):
        state = json.dumps(self.server.page_state()).encode("utf-8")
        before = (self.folder.path / "state.json").read_bytes()
        port = self.server.port
        for host in (f"evil.example:{port}", "127.0.0.1", f"127.0.0.1:{port + 1}",
                     f"localhost.evil.example:{port}"):
            for method, path, body in (("GET", "/", None), ("HEAD", "/", None),
                                       ("PUT", "/state", state), ("POST", "/reveal", b"{}")):
                status, answer = self.server.request(method, path, body, host=host)
                self.assertEqual(status, 403, f"{method} {host}")
                if method != "HEAD":
                    self.assertIn(b"foreign host", answer)
        self.assertEqual((self.folder.path / "state.json").read_bytes(), before)


class OldFolderTest(unittest.TestCase):
    """A folder built before setups.json existed: setup objects live in the state."""

    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)
        frozen = read_json(self.folder.path / "setups.json")["setups"]
        state = read_json(self.folder.path / "state.json")
        for item in state["items"]:
            item["setup"] = frozen.get(item["setup"])
        (self.folder.path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.folder.path / "setups.json").unlink()
        self.server = RunningServer(self.folder.path)

    def tearDown(self):
        self.server.stop()
        self.folder.cleanup()

    def test_get_serves_state_setups_unchanged(self):
        self.assertEqual(self.server.page_state()["items"][0]["setup"], resolved_first())

    def test_reveal_whitelist_from_state(self):
        self.assertEqual(self.server.reveal("/tmp/checklist-test-declared")[0], 404)
        self.assertEqual(self.server.reveal("/etc")[0], 403)

    def test_put_keeps_client_setups(self):
        state = self.server.page_state()
        state["items"][1]["setup"] = {"text": "added by the page"}
        self.assertEqual(self.server.put_state(state)[0], 200)
        saved = read_json(self.folder.path / "state.json")
        self.assertEqual(saved["items"][1]["setup"], {"text": "added by the page"})

    def test_as_spec_falls_back_to_state_setup(self):
        spec = read.as_spec(read_json(self.folder.path / "state.json"),
                            read.load_setups(self.folder.path))
        self.assertEqual(spec["items"][0]["setup"]["text"], "Start it.")


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)

    def tearDown(self):
        self.folder.cleanup()

    def test_report_survives_setup_ids(self):
        lines = read.report(self.folder.path, read.load(self.folder.path))
        self.assertTrue(any("First" in line for line in lines))

    def test_as_spec_reinlines_raw_setups(self):
        spec = read.as_spec(read.load(self.folder.path), read.load_setups(self.folder.path))
        self.assertEqual(spec["items"][0]["setup"], sample_spec()["items"][0]["setup"])
        self.assertNotIn("setup", spec["items"][1])
        self.assertEqual(spec["items"][2]["setup"], "Short form text.")

    def test_as_spec_round_trip_rebuilds_equal_state(self):
        before_state = read_json(self.folder.path / "state.json")
        before_setups = read_json(self.folder.path / "setups.json")
        spec = read.as_spec(before_state, read.load_setups(self.folder.path))
        build_into(copy.deepcopy(spec), self.folder.path)
        self.assertEqual(read_json(self.folder.path / "state.json"), before_state)
        self.assertEqual(read_json(self.folder.path / "setups.json"), before_setups)


if __name__ == "__main__":
    unittest.main()

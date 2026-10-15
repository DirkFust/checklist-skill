"""Behaviour that existed before the tests did — pinned so later slices keep it."""

import json
import unittest

from helpers import Folder, RunningServer, build, build_into, read_json, sample_spec


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def test_build_writes_template_and_state(self):
        build_into(sample_spec(), self.folder.path)
        template = (self.folder.path / "template.html").read_text(encoding="utf-8")
        self.assertIn("%%STATE%%", template)
        state = read_json(self.folder.path / "state.json")
        self.assertEqual([item["title"] for item in state["items"]],
                         ["First", "Second", "Third"])

    def test_unknown_item_key_aborts(self):
        spec = sample_spec()
        spec["items"][0]["notes"] = "typo"
        with self.assertRaises(SystemExit) as stop:
            build_into(spec, self.folder.path)
        self.assertIn("did you mean 'note'", str(stop.exception))

    def test_unknown_setup_key_aborts(self):
        spec = sample_spec()
        spec["items"][0]["setup"]["command"] = "make"
        with self.assertRaises(SystemExit):
            build.document(spec)


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)
        self.server = RunningServer(self.folder.path)

    def tearDown(self):
        self.server.stop()
        self.folder.cleanup()

    def test_put_state_round_trip(self):
        state = self.server.page_state()
        state["items"][1]["state"] = "ok"
        state["items"][1]["note"] = "fine </script> here"
        status, _ = self.server.put_state(state)
        self.assertEqual(status, 200)
        saved = read_json(self.folder.path / "state.json")
        self.assertEqual(saved["items"][1]["state"], "ok")
        self.assertEqual(saved["meta"]["folder"], str(self.folder.path.resolve()))
        self.assertEqual(self.server.page_state()["items"][1]["note"], "fine </script> here")

    def test_reveal_rejects_undeclared_path(self):
        status, body = self.server.reveal("/etc")
        self.assertEqual(status, 403)
        self.assertIn(b"not in the checklist", body)

    def test_reveal_accepts_declared_path(self):
        # Declared but missing: the whitelist let it through, the existence check
        # stops it before anything is opened.
        status, _ = self.server.reveal("/tmp/checklist-test-declared")
        self.assertEqual(status, 404)

    def test_data_serves_files_inside(self):
        (self.folder.path / "data").mkdir()
        (self.folder.path / "data" / "a.txt").write_text("hi", encoding="utf-8")
        status, body = self.server.request("GET", "/data/a.txt")
        self.assertEqual((status, body), (200, b"hi"))

    def test_data_rejects_parent_segments(self):
        (self.folder.path / "data").mkdir()
        status, _ = self.server.request("GET", "/data/../state.json")
        self.assertEqual(status, 404)

    def test_put_rejects_invalid_json(self):
        status, _ = self.server.request("PUT", "/state", b"{nope")
        self.assertEqual(status, 400)
        json.loads((self.folder.path / "state.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

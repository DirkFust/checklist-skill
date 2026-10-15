"""Test data materialized at build time, a setup's files, --clean, /reset, /peek."""

import base64
import codecs
import contextlib
import copy
import io
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import unittest

from helpers import SKILL_ROOT, Folder, RunningServer, build, build_into, read_json, serve

import read

PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def data_spec(data: dict, files=None) -> dict:
    """One item whose setup names `files` (all data names by default)."""
    return {
        "title": "Data",
        "data": data,
        "items": [{"title": "A", "setup": {"text": "x",
                                            "files": list(data) if files is None else files}},
                  {"title": "B"}],
    }


def quiet(call):
    with contextlib.redirect_stderr(io.StringIO()):
        return call()


class DataBuildTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.root = self.folder.path

    def tearDown(self):
        self.folder.cleanup()

    def built(self, data: dict) -> dict:
        """name -> bytes in data/, after checking .pristine/ holds the same."""
        build_into(data_spec(data), self.root)
        written = {}
        for path in sorted((self.root / build.DATA_DIR).rglob("*")):
            if path.is_file():
                name = path.relative_to(self.root / build.DATA_DIR).as_posix()
                written[name] = path.read_bytes()
                self.assertEqual((self.root / build.PRISTINE_DIR / name).read_bytes(),
                                 written[name], name)
        return written

    def aborts(self, data: dict, files=None) -> str:
        with self.assertRaises(SystemExit) as stop:
            build.document(data_spec(data, files), self.root)
        return str(stop.exception)

    def test_string_is_utf8_verbatim(self):
        self.assertEqual(self.built({"a.txt": "grüß\nno end"}),
                         {"a.txt": "grüß\nno end".encode("utf-8")})

    def test_subfolders(self):
        self.assertEqual(self.built({"case-1/in.txt": "x"}), {"case-1/in.txt": b"x"})

    def test_text_with_encoding_eol_bom(self):
        written = self.built({
            "le.txt": {"text": "a\nb\n", "encoding": "utf-16-le", "eol": "crlf", "bom": True},
            "bom8.txt": {"text": "x", "bom": True},
            "latin.txt": {"text": "é\n", "encoding": "latin-1"},
            "mac.txt": {"text": "a\nb", "eol": "cr"},
            "plain.txt": {"text": "a\nb", "eol": "lf"},
        })
        self.assertEqual(written["le.txt"], codecs.BOM_UTF16_LE + "a\r\nb\r\n".encode("utf-16-le"))
        self.assertEqual(written["bom8.txt"], codecs.BOM_UTF8 + b"x")
        self.assertEqual(written["latin.txt"], b"\xe9\n")
        self.assertEqual(written["mac.txt"], b"a\rb")
        self.assertEqual(written["plain.txt"], b"a\nb")

    def test_utf32_bom_by_endianness(self):
        written = self.built({"be.txt": {"text": "a", "encoding": "UTF-32-BE", "bom": True}})
        self.assertEqual(written["be.txt"], codecs.BOM_UTF32_BE + "a".encode("utf-32-be"))

    def test_ambiguous_utf16_utf32_abort(self):
        # Python's own utf-16/utf-32 put a native-order BOM in front even without
        # `bom` — so the plain names are refused either way.
        for encoding in ("utf-16", "UTF16", "utf-32"):
            for bom in (True, False):
                message = self.aborts({"a.txt": {"text": "x", "encoding": encoding, "bom": bom}})
                self.assertIn("-le", message, encoding)

    def test_bad_encodings_abort(self):
        for entry in ({"text": "x", "encoding": "klingon"},
                      {"text": "x", "encoding": "base64"},        # a codec, not a text encoding
                      {"text": "€", "encoding": "latin-1"},       # not encodable
                      {"text": "x", "encoding": "latin-1", "bom": True},
                      {"text": "x", "encoding": 8},
                      {"text": "x", "eol": "crlf\n"},
                      {"text": "x", "bom": "yes"},
                      {"text": 3}):
            self.aborts({"a.txt": entry})

    def test_copy(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            source = pathlib.Path(elsewhere) / "source.bin"
            source.write_bytes(b"\x00\x01raw")
            self.assertEqual(self.built({"c.bin": {"copy": str(source)}}), {"c.bin": b"\x00\x01raw"})

    def test_copy_source_must_exist_and_be_absolute(self):
        self.assertIn("/nowhere/at/all", self.aborts({"c": {"copy": "/nowhere/at/all"}}))
        self.aborts({"c": {"copy": "relative/file"}})
        self.aborts({"c": {"copy": "/"}})                    # a folder, not a file

    def test_base64(self):
        payload = base64.b64encode(PNG_HEADER).decode("ascii")
        self.assertEqual(self.built({"p.png": {"base64": payload}}), {"p.png": PNG_HEADER})

    def test_bad_base64_aborts(self):
        self.aborts({"p.png": {"base64": "not base64!"}})

    def test_entry_needs_exactly_one_form(self):
        for entry in ({}, {"text": "x", "copy": "/etc/hosts"}, {"copy": "/etc/hosts", "eol": "lf"},
                      {"base64": "eA==", "encoding": "utf-8"}, {"txt": "x"}, None, 3, ["x"]):
            self.aborts({"a.txt": entry})

    def test_bad_names_abort(self):
        for name in ("../x", "a//b", ".hidden", "/abs", "a/../b", "", "a/", "a b", "a\\b"):
            self.aborts({name: "x"})

    def test_file_and_folder_of_the_same_name_abort(self):
        self.aborts({"a": "x", "a/b": "y"})

    def test_names_differing_only_in_case_abort(self):
        # One file on a case-insensitive disk — the second write would land on the first.
        for data in ({"A.txt": "x", "a.txt": "y"}, {"Sub/x": "x", "sub": "y"}):
            self.assertIn("case", self.aborts(data), data)

    def metadata(self) -> dict:
        return {name: (self.root / name).read_bytes()
                for name in ("template.html", "state.json", "setups.json")}

    def test_clash_aborts_before_anything_is_written(self):
        build_into(data_spec({"a.txt": "old"}), self.root)
        before = self.metadata()
        (self.root / build.DATA_DIR / "b.txt").mkdir()
        (self.root / build.DATA_DIR / "b.txt" / "mine").write_text("x", encoding="utf-8")
        spec = data_spec({"a.txt": "new", "b.txt": "y"})
        spec["title"] = "Changed"
        with self.assertRaises(SystemExit) as stop:
            build_into(spec, self.root)
        self.assertIn("b.txt", str(stop.exception))
        self.assertEqual(self.metadata(), before)
        self.assertEqual((self.root / build.DATA_DIR / "a.txt").read_text(encoding="utf-8"), "old")

    def test_file_where_a_folder_goes_aborts(self):
        build_into(data_spec({"a.txt": "old"}), self.root)
        before = self.metadata()
        (self.root / build.DATA_DIR / "sub").write_text("a script's", encoding="utf-8")
        with self.assertRaises(SystemExit) as stop:
            build_into(data_spec({"a.txt": "old", "sub/x.txt": "y"}), self.root)
        self.assertIn("sub", str(stop.exception))
        self.assertEqual(self.metadata(), before)

    def test_file_and_folder_may_trade_places_between_builds(self):
        build_into(data_spec({"a": "x"}), self.root)
        self.assertEqual(self.built({"a/b": "y"}), {"a/b": b"y"})
        self.assertEqual(self.built({"a": "z"}), {"a": b"z"})

    def test_data_must_be_an_object(self):
        self.aborts(["a.txt"], files=[])

    def test_bad_data_leaves_folder_untouched(self):
        build_into(data_spec({"a.txt": "first"}), self.root)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with self.assertRaises(SystemExit):
            build_into(data_spec({"a.txt": "second", "b.txt": {"copy": "/nowhere"}}), self.root)
        after = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(after, before)

    def test_bad_data_creates_no_folder(self):
        with self.assertRaises(SystemExit):
            build_into(data_spec({"a.txt": {"base64": "!"}}), self.root)
        self.assertFalse(self.root.exists())

    def test_rebuild_drops_what_data_no_longer_names(self):
        build_into(data_spec({"keep.txt": "1", "sub/gone.txt": "2"}), self.root)
        stranger = self.root / build.DATA_DIR / "generated.txt"
        stranger.write_text("made by a script", encoding="utf-8")
        (self.root / build.DATA_DIR / "keep.txt").write_text("edited", encoding="utf-8")
        build_into(data_spec({"keep.txt": "1"}), self.root)
        for folder in (build.DATA_DIR, build.PRISTINE_DIR):
            self.assertFalse((self.root / folder / "sub").exists(), folder)
            self.assertEqual((self.root / folder / "keep.txt").read_text(encoding="utf-8"), "1")
        # Not the build's to remove: it never wrote that file.
        self.assertTrue(stranger.is_file())

    def test_rebuild_refuses_a_symlinked_data_file(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = pathlib.Path(elsewhere) / "precious.txt"
            outside.write_text("keep me", encoding="utf-8")
            build_into(data_spec({"a.txt": "x"}), self.root)
            for base in (build.DATA_DIR, build.PRISTINE_DIR):
                target = self.root / base / "a.txt"
                saved = target.read_bytes()
                target.unlink()
                target.symlink_to(outside)
                with self.assertRaises(SystemExit, msg=base) as stop:
                    build_into(data_spec({"a.txt": "y"}), self.root)
                self.assertIn("symlink", str(stop.exception))
                self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")
                target.unlink()
                target.write_bytes(saved)

    def test_rebuild_refuses_a_symlinked_data_folder(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            build_into(data_spec({"a.txt": "x"}), self.root)
            (self.root / build.DATA_DIR / "sub").symlink_to(elsewhere)
            with self.assertRaises(SystemExit):
                build_into(data_spec({"a.txt": "x", "sub/b.txt": "y"}), self.root)
            self.assertEqual(list(pathlib.Path(elsewhere).iterdir()), [])

    def test_rebuild_replaces_a_hard_link_instead_of_writing_through_it(self):
        with tempfile.TemporaryDirectory(dir=self.root.parent) as elsewhere:
            outside = pathlib.Path(elsewhere) / "precious.txt"
            outside.write_text("keep me", encoding="utf-8")
            build_into(data_spec({"a.txt": "x"}), self.root)
            target = self.root / build.DATA_DIR / "a.txt"
            target.unlink()
            os.link(outside, target)
            build_into(data_spec({"a.txt": "y"}), self.root)
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(target.read_text(encoding="utf-8"), "y")

    def test_merge_rewrites_data(self):
        build_into(data_spec({"a.txt": "fresh"}), self.root)
        state = read_json(self.root / "state.json")
        state["meta"]["folder"] = str(self.root)
        (self.root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (self.root / build.DATA_DIR / "a.txt").write_text("used", encoding="utf-8")
        build_into(data_spec({"a.txt": "fresh"}), self.root, rebuild=build.REBUILD_MERGE)
        self.assertEqual((self.root / build.DATA_DIR / "a.txt").read_text(encoding="utf-8"), "fresh")

    def test_spec_without_data_writes_no_data_dirs(self):
        build_into({"items": [{"title": "A"}]}, self.root)
        self.assertFalse((self.root / build.DATA_DIR).exists())
        self.assertFalse((self.root / build.PRISTINE_DIR).exists())

    def test_raw_data_in_source_and_round_trip(self):
        data = {"a.txt": "x\n", "le.txt": {"text": "y", "encoding": "utf-16-le", "bom": True}}
        build_into(data_spec(data), self.root)
        before_state = read_json(self.root / "state.json")
        before_setups = read_json(self.root / "setups.json")
        self.assertEqual(before_setups["source"]["data"], data)
        again = read.as_spec(before_state, read.load_setups(self.root))
        self.assertEqual(again["data"], data)
        build_into(copy.deepcopy(again), self.root)
        self.assertEqual(read_json(self.root / "state.json"), before_state)
        self.assertEqual(read_json(self.root / "setups.json"), before_setups)


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.root = self.folder.path.resolve()

    def tearDown(self):
        self.folder.cleanup()

    def files_of(self, data: dict, files: list, launch=None) -> dict:
        spec = data_spec(data, files)
        if launch is not None:
            spec["items"][0]["setup"]["launch"] = launch
        _, frozen = build.document(spec, self.root)
        return frozen["setups"]["item-01"]

    def test_managed_and_external(self):
        setup = self.files_of({"a.txt": "x", "le.txt": {"text": "y", "encoding": "utf-16-le"}},
                              ["a.txt", "le.txt", "~/elsewhere.txt", "/tmp/abs.txt"])
        self.assertEqual(setup["files"], [
            {"name": "a.txt", "path": str(self.root / "data" / "a.txt"), "managed": True},
            {"name": "le.txt", "path": str(self.root / "data" / "le.txt"), "managed": True,
             "encoding": "utf-16-le"},
            {"name": "~/elsewhere.txt", "path": str(pathlib.Path.home() / "elsewhere.txt"),
             "managed": False},
            {"name": "/tmp/abs.txt", "path": "/tmp/abs.txt", "managed": False},
        ])

    def test_unknown_relative_name_aborts_listing_data(self):
        with self.assertRaises(SystemExit) as stop:
            self.files_of({"a.txt": "x"}, ["b.txt"])
        self.assertIn("'b.txt'", str(stop.exception))
        self.assertIn("a.txt", str(stop.exception))

    def test_files_must_be_a_list_of_names(self):
        for files in ("a.txt", [3], [""], {"a.txt": 1}):
            with self.assertRaises(SystemExit, msg=repr(files)):
                self.files_of({"a.txt": "x"}, files)

    def test_files_without_data(self):
        spec = {"items": [{"title": "A", "setup": {"files": ["/tmp/x.txt"]}}]}
        _, frozen = build.document(spec, self.root)
        self.assertEqual(frozen["setups"]["item-01"]["files"],
                         [{"name": "/tmp/x.txt", "path": "/tmp/x.txt", "managed": False}])
        spec["items"][0]["setup"]["files"] = ["x.txt"]
        with self.assertRaises(SystemExit):
            build.document(spec, self.root)

    def test_files_placeholder_resolves(self):
        setup = self.files_of({"a.txt": "x"}, ["a.txt", "~/c d.txt"],
                              launch={"run": "open {files}"})
        self.assertEqual(setup["launch"]["run"],
                         f"open {shlex.quote(str(self.root / 'data' / 'a.txt'))} "
                         f"{shlex.quote(str(pathlib.Path.home() / 'c d.txt'))}")

    def test_setup_without_files_has_empty_list(self):
        _, frozen = build.document({"items": [{"title": "A", "setup": "text"}]}, self.root)
        self.assertEqual(frozen["setups"]["item-01"]["files"], [])


class CleanTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.root = self.folder.path
        build_into(data_spec({"a.txt": "x"}), self.root)
        (self.root / "images").mkdir()
        (self.root / "images" / "shot.png").write_bytes(b"png")

    def tearDown(self):
        self.folder.cleanup()

    def test_clean_removes_data_dirs_only(self):
        quiet(lambda: build.clean(self.root))
        self.assertFalse((self.root / build.DATA_DIR).exists())
        self.assertFalse((self.root / build.PRISTINE_DIR).exists())
        for kept in ("state.json", "setups.json", "template.html", "images/shot.png"):
            self.assertTrue((self.root / kept).is_file(), kept)

    def test_clean_twice_is_safe(self):
        quiet(lambda: build.clean(self.root))
        quiet(lambda: build.clean(self.root))

    def test_clean_from_the_command_line(self):
        done = subprocess.run([sys.executable, str(SKILL_ROOT / "build.py"), "--clean",
                               str(self.root)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse((self.root / build.DATA_DIR).exists())
        self.assertTrue((self.root / "state.json").is_file())


def server_spec() -> dict:
    """Setups with managed, external and no files, and data of every kind."""
    return {
        "title": "Served",
        "data": {
            "plain.txt": "one\ntwo\n",
            "crlf.txt": {"text": "a\nb", "eol": "crlf"},
            "le.txt": {"text": "x\ty\n", "encoding": "utf-16-le", "bom": True},
            "mixed.txt": {"base64": base64.b64encode(b"a\r\nb\nc\rd").decode("ascii")},
            "image.png": {"base64": base64.b64encode(PNG_HEADER).decode("ascii")},
            "long.txt": "".join(f"line {n}\n" for n in range(500)),
            "nul.txt": "a\x00b",
        },
        "setups": {
            "files": {"text": "x", "files": ["plain.txt", "crlf.txt", "le.txt", "mixed.txt",
                                              "image.png", "long.txt", "nul.txt",
                                              "/tmp/checklist-test-external.txt"]},
            "outside": {"text": "x", "files": ["/tmp/checklist-test-external.txt"]},
            "bare": {"text": "nothing to reset"},
        },
        "items": [{"title": "A", "setup": "files"}, {"title": "B", "setup": "outside"},
                  {"title": "C", "setup": "bare"}],
    }


class ServedFilesTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        self.root = self.folder.path
        build_into(server_spec(), self.root)
        self.server = RunningServer(self.root)

    def tearDown(self):
        self.server.stop()
        self.folder.cleanup()

    def data(self, name: str) -> pathlib.Path:
        return self.root / build.DATA_DIR / name

    def test_reset_restores_managed_files(self):
        self.data("plain.txt").write_text("scribbled", encoding="utf-8")
        self.data("le.txt").unlink()
        status, body = self.server.reset("files")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["reset"],
                         ["plain.txt", "crlf.txt", "le.txt", "mixed.txt", "image.png",
                          "long.txt", "nul.txt"])
        self.assertEqual(self.data("plain.txt").read_text(encoding="utf-8"), "one\ntwo\n")
        self.assertTrue(self.data("le.txt").read_bytes().startswith(codecs.BOM_UTF16_LE))

    def test_reset_with_nothing_managed(self):
        for setup_id in ("outside", "bare"):
            status, body = self.server.reset(setup_id)
            self.assertEqual((status, json.loads(body)), (200, {"reset": []}), setup_id)

    def test_reset_unknown_setup_is_404(self):
        self.assertEqual(self.server.reset("nope")[0], 404)

    def test_reset_guards(self):
        self.data("plain.txt").write_text("scribbled", encoding="utf-8")
        for headers in ({}, {"X-Checklist": "reveal"},
                        {"X-Checklist": "reset", "Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.server.reset("files", headers)[0], 403, headers)
        status, _ = self.server.request("POST", "/reset", b"{}", host="evil.example:1")
        self.assertEqual(status, 403)
        self.assertEqual(self.data("plain.txt").read_text(encoding="utf-8"), "scribbled")

    def test_reset_without_pristine_copy_fails_and_writes_nothing(self):
        self.data("plain.txt").write_text("scribbled", encoding="utf-8")
        (self.root / build.PRISTINE_DIR / "crlf.txt").unlink()
        status, body = self.server.reset("files")
        self.assertEqual(status, 409)
        self.assertIn(b"crlf.txt", body)
        self.assertEqual(self.data("plain.txt").read_text(encoding="utf-8"), "scribbled")

    def test_served_files_are_sandboxed(self):
        # A test file is foreign content: script in it must not run as this
        # origin, or it could POST /launch past every guard.
        (self.root / build.DATA_DIR / "x.html").write_text("<script>1</script>", encoding="utf-8")
        (self.root / "images").mkdir(exist_ok=True)
        (self.root / "images" / "a.svg").write_text("<svg/>", encoding="utf-8")
        for path in ("/data/x.html", "/images/a.svg", "/data/plain.txt", "/peek?setup=files&file=0"):
            headers = self.server.headers_of(path)
            self.assertEqual(headers.get("content-security-policy"), "sandbox", path)
            self.assertEqual(headers.get("x-content-type-options"), "nosniff", path)
        self.assertNotIn("content-security-policy", self.server.headers_of("/"))

    def outside_file(self) -> pathlib.Path:
        outside = self.root.parent / "precious.txt"
        outside.write_text("keep me", encoding="utf-8")
        return outside

    def test_reset_refuses_a_symlinked_target(self):
        outside = self.outside_file()
        self.data("plain.txt").unlink()
        self.data("plain.txt").symlink_to(outside)
        status, body = self.server.reset("files")
        self.assertEqual(status, 409, body)
        self.assertIn(b"plain.txt", body)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")

    def test_reset_refuses_a_symlinked_pristine_copy(self):
        outside = self.outside_file()
        pristine = self.root / build.PRISTINE_DIR / "plain.txt"
        pristine.unlink()
        pristine.symlink_to(outside)
        self.assertEqual(self.server.reset("files")[0], 409)
        self.assertNotEqual(self.data("plain.txt").read_text(encoding="utf-8"), "keep me")

    def test_reset_replaces_a_hard_link_instead_of_writing_through_it(self):
        outside = self.outside_file()
        self.data("plain.txt").unlink()
        os.link(outside, self.data("plain.txt"))
        self.assertEqual(self.server.reset("files")[0], 200)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")
        self.assertEqual(self.data("plain.txt").read_text(encoding="utf-8"), "one\ntwo\n")

    def test_peek_refuses_a_symlinked_managed_file(self):
        outside = self.outside_file()
        self.data("plain.txt").unlink()
        self.data("plain.txt").symlink_to(outside)
        status, body = self.server.peek("files", 0)
        self.assertEqual(status, 409, body)

    def test_files_join_the_reveal_whitelist(self):
        # 404 = passed the whitelist and found nothing: no Finder opens in a test.
        self.data("plain.txt").unlink()
        self.assertEqual(self.server.reveal(str(self.data("plain.txt").resolve()))[0], 404)
        self.assertEqual(self.server.reveal("/tmp/checklist-test-external.txt")[0], 404)
        self.assertEqual(self.server.reveal(str(self.root / "state.json"))[0], 403)

    def test_peek_plain(self):
        status, peek = self.server.peek("files", 0)
        self.assertEqual(status, 200)
        self.assertEqual(peek, {"name": "plain.txt", "size": 8, "encoding": "utf-8",
                                "bom": False, "eol": "lf", "final_newline": True,
                                "binary": False, "lines": ["one\n", "two\n"],
                                "truncated": False})

    def test_peek_line_ends(self):
        crlf = self.server.peek("files", 1)[1]
        self.assertEqual((crlf["eol"], crlf["final_newline"], crlf["lines"]),
                         ("crlf", False, ["a\r\n", "b"]))
        mixed = self.server.peek("files", 3)[1]
        self.assertEqual((mixed["eol"], mixed["lines"]), ("mixed", ["a\r\n", "b\n", "c\r", "d"]))

    def test_peek_declared_encoding_strips_bom(self):
        peek = self.server.peek("files", 2)[1]
        self.assertEqual((peek["encoding"], peek["bom"], peek["lines"]),
                         ("utf-16-le", True, ["x\ty\n"]))

    def test_peek_binary(self):
        for index in (4, 6):
            peek = self.server.peek("files", index)[1]
            self.assertEqual((peek["binary"], peek["lines"]), (True, []), index)

    def test_peek_truncates(self):
        peek = self.server.peek("files", 5)[1]
        self.assertTrue(peek["truncated"])
        self.assertEqual(len(peek["lines"]), serve.PEEK_LINES)

    def test_peek_missing_file(self):
        status, body = self.server.peek("files", 7)
        self.assertEqual(status, 404)
        self.assertIn(b"/tmp/checklist-test-external.txt", body)

    def test_peek_bad_requests(self):
        for setup_id, index in (("nope", 0), ("files", 99), ("files", -1), ("files", "x"),
                                ("bare", 0)):
            self.assertEqual(self.server.peek(setup_id, index)[0], 404, (setup_id, index))
        self.assertEqual(self.server.request("GET", "/peek")[0], 404)


class PeekTest(unittest.TestCase):
    """The pure part of /peek."""

    def test_empty_file(self):
        self.assertEqual(serve.peek("e", b"", None)["eol"], "none")
        self.assertFalse(serve.peek("e", b"", None)["final_newline"])

    def test_bom_without_declared_encoding(self):
        peek = serve.peek("b", codecs.BOM_UTF8 + b"x\n", None)
        self.assertEqual((peek["bom"], peek["lines"]), (True, ["x\n"]))

    def test_foreign_bom_is_reported(self):
        peek = serve.peek("b", codecs.BOM_UTF16_LE + "x".encode("utf-16-le"), None)
        self.assertTrue(peek["bom"])
        self.assertTrue(peek["binary"])


class TemplateTest(unittest.TestCase):
    def test_template_carries_file_chrome(self):
        table = build.strings(SKILL_ROOT / "strings" / "en.json")
        folder = Folder()
        try:
            build_into(server_spec(), folder.path)
            template = (folder.path / "template.html").read_text(encoding="utf-8")
        finally:
            folder.cleanup()
        for key in ("show_file", "hide_file", "reset_files", "reset_done", "no_final_newline"):
            self.assertIn(table[key], template, key)

    def test_german_table_matches_the_shell(self):
        shell = (SKILL_ROOT / "shell.html").read_text(encoding="utf-8")
        build.translate(shell, build.strings(SKILL_ROOT / "strings" / "de.json"), "de")


if __name__ == "__main__":
    unittest.main()

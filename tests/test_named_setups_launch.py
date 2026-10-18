"""Named setups, launch config, project config, language and rebuild protection."""

import copy
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from helpers import Folder, build, build_into, read_json, run_quietly, sample_spec, stderr_of

import read


def named_spec() -> dict:
    """Two items share a named setup, one has its own, one has none."""
    return {
        "title": "Named",
        "setups": {"app": {"label": "App", "text": "Run the app.",
                           "launch": {"run": "open -a App"}}},
        "items": [
            {"title": "A", "setup": "app"},
            {"title": "B", "setup": "app"},
            {"title": "C", "setup": {"text": "Inline."}},
            {"title": "D"},
        ],
    }


class NamedSetupTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()

    def tearDown(self):
        self.folder.cleanup()

    def document(self, spec: dict):
        return build.document(spec, self.folder.path)

    def test_string_references_named_setup(self):
        state, frozen = self.document(named_spec())
        self.assertEqual([item["setup"] for item in state["items"]],
                         ["app", "app", "item-03", None])
        self.assertEqual(set(frozen["setups"]), {"app", "item-03"})
        self.assertEqual(frozen["setups"]["app"]["text"], "Run the app.")
        self.assertEqual(frozen["source"]["setups"], named_spec()["setups"])
        self.assertEqual(frozen["source"]["inline"], {"item-03": {"text": "Inline."}})

    def test_unknown_reference_aborts_listing_known(self):
        spec = named_spec()
        spec["items"][0]["setup"] = "nope"
        with self.assertRaises(SystemExit) as stop:
            self.document(spec)
        self.assertIn("'nope'", str(stop.exception))
        self.assertIn("app", str(stop.exception))

    def test_bad_name_aborts(self):
        for name in ("App", "-app", "a b", ""):
            spec = named_spec()
            spec["setups"] = {name: {"text": "x"}}
            with self.assertRaises(SystemExit, msg=name):
                self.document(spec)

    def test_inline_id_is_no_setup_name(self):
        # `item-01` would collide with the id an inline setup gets.
        spec = named_spec()
        spec["setups"] = {"item-01": {"text": "x"}}
        with self.assertRaises(SystemExit) as stop:
            self.document(spec)
        self.assertIn("item-01", str(stop.exception))

    def test_empty_named_setup_aborts(self):
        spec = named_spec()
        spec["setups"]["app"] = {}
        with self.assertRaises(SystemExit):
            self.document(spec)

    def test_unused_named_setup_warns(self):
        spec = named_spec()
        spec["setups"]["spare"] = {"text": "Never used."}
        warning = stderr_of(lambda: self.document(spec))
        self.assertIn("spare", warning)

    def test_contiguous_setups_do_not_warn(self):
        self.assertEqual(stderr_of(lambda: self.document(named_spec())), "")

    def test_a_split_named_setup_warns_with_its_items(self):
        spec = named_spec()
        spec["items"][1], spec["items"][2] = spec["items"][2], spec["items"][1]
        warning = stderr_of(lambda: self.document(spec))
        self.assertIn("01, 03", warning)
        self.assertIn("app", warning)

    def test_equal_inline_setups_split_apart_warn(self):
        # Same content under two ids is the same setup to whoever ticks it.
        spec = named_spec()
        spec["items"][0]["setup"] = {"text": "Inline."}
        spec["items"][1]["setup"] = None
        spec["items"][3]["setup"] = {"text": "Inline."}
        warning = stderr_of(lambda: self.document(spec))
        self.assertIn("01, 03, 04", warning)

    def test_equal_inline_setups_share_the_first_id(self):
        # One id is what lets the page fold the repeat to `same as`.
        spec = named_spec()
        spec["items"][3]["setup"] = {"text": "Inline."}
        state, frozen = self.document(spec)
        self.assertEqual([item["setup"] for item in state["items"]],
                         ["app", "app", "item-03", "item-03"])
        self.assertEqual(frozen["source"]["inline"], {"item-03": {"text": "Inline."}})
        self.assertEqual([item.get("setup") for item in read.as_spec(state, frozen)["items"]],
                         ["app", "app", {"text": "Inline."}, {"text": "Inline."}])

    def test_inline_setups_written_differently_keep_their_own_ids(self):
        spec = named_spec()
        spec["items"][3]["setup"] = {"text": "Inline.", "label": "Other"}
        state, _ = self.document(spec)
        self.assertEqual(state["items"][3]["setup"], "item-04")

    def test_unused_named_setup_stays_out_of_resolved(self):
        spec = named_spec()
        spec["setups"]["spare"] = {"paths": ["/tmp/spare"]}
        (_, frozen), _ = run_quietly(lambda: self.document(spec))
        self.assertNotIn("spare", frozen["setups"])
        self.assertIn("spare", frozen["source"]["setups"])

    def test_string_is_text_without_setups(self):
        spec = {"items": [{"title": "A", "setup": "Just text."}]}
        _, frozen = self.document(spec)
        self.assertEqual(frozen["setups"]["item-01"]["text"], "Just text.")


class LaunchTest(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        # A space in the path proves {data} is quoted in commands.
        self.folder = pathlib.Path(temp).resolve() / "with space" / "checklist"
        self.data = self.folder / "data"

    def resolved(self, setup_launch=None, spec_launch=None, project_launch=None):
        """The launch the single item's setup ends up with."""
        setup = {"text": "x"}
        if setup_launch is not None:
            setup["launch"] = setup_launch
        spec = {"items": [{"title": "A", "setup": setup}]}
        if spec_launch is not None:
            spec["launch"] = spec_launch
        project = build.Project(root=pathlib.Path("/project"),
                                launch=project_launch or {}, lang=None)
        _, frozen = build.document(spec, self.folder, project=project)
        return frozen["setups"]["item-01"]["launch"]

    def test_no_run_means_no_launch(self):
        self.assertIsNone(self.resolved())
        self.assertIsNone(self.resolved({"build": "make"}))

    def test_merge_field_by_field(self):
        launch = self.resolved(
            setup_launch={"quit": None, "build": ""},
            spec_launch={"run": "spec-run", "url": "http://localhost:3000"},
            project_launch={"quit": "q", "build": "make", "run": "project-run",
                            "ready_timeout": 5})
        self.assertEqual(launch, {"cwd": "/project", "quit": None, "build": None,
                                  "run": "spec-run", "url": "http://localhost:3000",
                                  "ready_timeout": 5})

    def test_setup_can_disable_run(self):
        self.assertIsNone(self.resolved({"run": None}, spec_launch={"run": "go"}))

    def test_defaults(self):
        launch = self.resolved({"run": "go"})
        self.assertEqual(launch["cwd"], "/project")
        self.assertEqual(launch["ready_timeout"], build.DEFAULT_READY_TIMEOUT)

    def test_default_cwd_without_project_is_folder_parent(self):
        spec = {"items": [{"title": "A", "setup": {"launch": {"run": "go"}}}]}
        _, frozen = build.document(spec, self.folder)
        self.assertEqual(frozen["setups"]["item-01"]["launch"]["cwd"],
                         str(self.folder.parent))

    def test_cwd_tilde_expands(self):
        launch = self.resolved({"run": "go", "cwd": "~/dev"})
        self.assertEqual(launch["cwd"], str(pathlib.Path.home() / "dev"))

    def test_relative_cwd_aborts(self):
        with self.assertRaises(SystemExit):
            self.resolved({"run": "go", "cwd": "dev"})

    def test_bad_keys_and_types_abort(self):
        for bad in ({"start": "go"}, {"run": ["go"]}, {"ready_timeout": "5"},
                    {"ready_timeout": True}, {"ready_timeout": 0}, "go"):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                self.resolved(bad)

    def test_bad_spec_launch_aborts(self):
        # The project's launch is checked where it is loaded — ProjectConfigTest.
        with self.assertRaises(SystemExit):
            self.resolved(spec_launch={"start": "go"})

    def test_url_must_be_http(self):
        with self.assertRaises(SystemExit):
            self.resolved({"run": "go", "url": "file:///etc/passwd"})

    def test_url_needs_a_host_and_a_valid_port(self):
        # Each of these would leave ▶ waiting forever: the readiness probe
        # cannot even be asked.
        for url in ("http://localhost:99999/", "http:///x", "http://localhost:abc/",
                    "http://{data}"):
            with self.assertRaises(SystemExit, msg=url) as stop:
                self.resolved({"run": "go", "url": url})
            self.assertIn("launch.url", str(stop.exception), url)

    def test_data_placeholder_quoted_in_commands_raw_in_url(self):
        launch = self.resolved({"build": "ls {data}", "run": "open {data}/a.txt",
                                "url": "http://localhost:1/?d={data}"})
        self.assertEqual(launch["build"], f"ls '{self.data}'")
        self.assertEqual(launch["run"], f"open '{self.data}'/a.txt")
        self.assertEqual(launch["url"], f"http://localhost:1/?d={self.data}")

    def test_files_placeholder_is_empty_without_files(self):
        self.assertEqual(self.resolved({"run": "open {files}"})["run"], "open ")

    def test_files_argument_quotes_each_path(self):
        self.assertEqual(build.files_argument(["/a b", "/c"]), "'/a b' /c")

    def test_files_in_url_aborts(self):
        with self.assertRaises(SystemExit):
            self.resolved({"run": "go", "url": "http://localhost:1/{files}"})

    def test_unknown_placeholder_aborts(self):
        with self.assertRaises(SystemExit) as stop:
            self.resolved({"run": "open {file}"})
        self.assertIn("{file}", str(stop.exception))

    def test_shell_braces_are_no_placeholders(self):
        for command in ("echo ${HOME}/x", "awk '{print $1}'", "find . -exec rm {} \\;",
                        "cp a.{txt,bak} b", "{ make; make test; }", "echo ${files}"):
            self.assertEqual(self.resolved({"run": command})["run"], command)

    def test_raw_spec_launch_in_source(self):
        spec = {"launch": {"run": "go {data}"}, "items": [{"title": "A"}]}
        _, frozen = build.document(spec, self.folder)
        self.assertEqual(frozen["source"]["launch"], {"run": "go {data}"})


class ProfileTest(unittest.TestCase):
    """Named launch profiles: more than one way to start the same project."""

    PROJECT_LAUNCHES = {"diff": {"run": "open -a Diff", "build": "make app"},
                        "editor": {"run": "open -a Editor"}}

    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.folder = pathlib.Path(temp).resolve() / "checklist"
        self.project = build.Project(root=pathlib.Path("/project"),
                                     launch={"quit": "pkill App", "build": "make"},
                                     lang=None, launches=self.PROJECT_LAUNCHES)

    def document(self, setup_launch, spec_extra=None):
        spec = {"items": [{"title": "A", "setup": {"text": "x", "launch": setup_launch}}],
                **(spec_extra or {})}
        return build.document(spec, self.folder, project=self.project)

    def resolved(self, setup_launch, spec_extra=None) -> dict:
        _, frozen = self.document(setup_launch, spec_extra)
        return frozen["setups"]["item-01"]["launch"]

    def test_string_chooses_profile_over_default(self):
        launch = self.resolved("diff")
        self.assertEqual((launch["quit"], launch["build"], launch["run"]),
                         ("pkill App", "make app", "open -a Diff"))

    def test_profile_key_with_overrides(self):
        launch = self.resolved({"profile": "editor", "quit": None, "run": "open -a Ed2"})
        self.assertEqual((launch["quit"], launch["build"], launch["run"]),
                         (None, "make", "open -a Ed2"))

    def test_spec_launches_merge_over_project_per_name(self):
        launch = self.resolved("diff", {"launches": {
            "diff": {"build": ""}, "web": {"run": "npm run dev"}}})
        self.assertEqual((launch["build"], launch["run"]), (None, "open -a Diff"))
        self.assertEqual(self.resolved("web", {"launches": {"web": {"run": "npm run dev"}}})["run"],
                         "npm run dev")

    def test_spec_launch_sits_below_profile(self):
        launch = self.resolved("editor", {"launch": {"run": "default", "build": "spec"}})
        self.assertEqual((launch["build"], launch["run"]), ("spec", "open -a Editor"))

    def test_unknown_profile_aborts_listing_known(self):
        for setup_launch in ("nope", {"profile": "nope"}):
            with self.assertRaises(SystemExit) as stop:
                self.document(setup_launch)
            self.assertIn("'nope'", str(stop.exception))
            self.assertIn("diff, editor", str(stop.exception))

    def test_bad_profiles_abort(self):
        for launches in ({"Diff": {"run": "x"}}, {"diff": {"start": "x"}}, {"diff": "x"},
                         ["diff"]):
            with self.assertRaises(SystemExit, msg=repr(launches)):
                self.document(None, {"launches": launches})

    def test_profile_key_only_on_a_setup(self):
        with self.assertRaises(SystemExit):
            self.document(None, {"launch": {"profile": "diff"}})
        with self.assertRaises(SystemExit):
            self.document({"profile": 3})

    def test_unused_spec_profile_warns(self):
        warning = stderr_of(lambda: self.document("diff", {"launches": {
            "web": {"run": "npm run dev"}, "diff": {"build": "make"}}}))
        self.assertIn("web", warning)
        self.assertNotIn("diff", warning)

    def test_unused_project_profile_is_quiet(self):
        # A project config serves many checklists; each one uses only some.
        self.assertEqual(stderr_of(lambda: self.document("diff")), "")

    def test_raw_launches_in_source(self):
        launches = {"web": {"run": "npm run dev"}}
        _, frozen = self.document("web", {"launches": launches})
        self.assertEqual(frozen["source"]["launches"], launches)


class ProjectConfigTest(unittest.TestCase):
    def setUp(self):
        self.top = pathlib.Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.repo = self.top / "repo"
        self.start = self.repo / "sub" / ".manual-tests"
        self.start.mkdir(parents=True)

    def config(self, where: pathlib.Path, content: dict) -> None:
        (where / build.PROJECT_CONFIG).write_text(json.dumps(content), encoding="utf-8")

    def test_finds_nearest_config(self):
        self.config(self.repo, {"lang": "de", "launch": {"run": "go"}})
        self.config(self.repo / "sub", {"lang": "en"})
        project = build.find_project(self.start, self.top)
        self.assertEqual(project, build.Project(self.repo / "sub", {}, "en"))

    def test_walk_includes_git_dir_and_stops_there(self):
        (self.repo / ".git").mkdir()
        self.config(self.repo, {"launch": {"run": "go"}})
        self.config(self.top, {"lang": "de"})
        self.assertEqual(build.find_project(self.start, self.top),
                         build.Project(self.repo, {"run": "go"}, None))
        (self.repo / build.PROJECT_CONFIG).unlink()
        self.assertEqual(build.find_project(self.start, self.top),
                         build.Project(self.start, {}, None))

    def test_walk_stops_at_stop_dir_inclusive(self):
        self.config(self.repo, {"lang": "de"})
        self.assertEqual(build.find_project(self.start, self.repo).lang, "de")
        (self.repo / build.PROJECT_CONFIG).unlink()
        self.config(self.top, {"lang": "de"})
        self.assertIsNone(build.find_project(self.start, self.repo).lang)

    def test_outside_stop_without_a_repo_nothing_is_searched(self):
        # A build into /tmp must not pick up /private/tmp/checklist.json from
        # whoever wrote it there — its `build` would run on ▶.
        elsewhere = self.top / "home"
        elsewhere.mkdir()
        self.config(self.start, {"lang": "de"})
        self.config(self.top, {"lang": "de"})
        self.assertEqual(build.find_project(self.start, elsewhere),
                         build.Project(self.start, {}, None))

    def test_outside_stop_a_repo_bounds_the_walk(self):
        elsewhere = self.top / "home"
        elsewhere.mkdir()
        (self.repo / ".git").mkdir()
        self.config(self.top, {"lang": "fr"})
        self.assertIsNone(build.find_project(self.start, elsewhere).lang)
        self.config(self.repo, {"lang": "de"})
        self.assertEqual(build.find_project(self.start, elsewhere).lang, "de")

    def test_config_writable_by_others_is_ignored_with_a_warning(self):
        self.config(self.repo, {"lang": "de"})
        path = self.repo / build.PROJECT_CONFIG
        for mode in (0o664, 0o646):
            path.chmod(mode)
            project, warning = run_quietly(lambda: build.find_project(self.start, self.top))
            self.assertIsNone(project.lang, oct(mode))
            self.assertIn(str(path), warning)
        path.chmod(0o644)
        self.assertEqual(build.find_project(self.start, self.top).lang, "de")

    def test_config_owned_by_someone_else_is_ignored_with_a_warning(self):
        self.config(self.repo, {"lang": "de"})
        with mock.patch.object(build.os, "getuid", return_value=os.getuid() + 1):
            project, warning = run_quietly(lambda: build.find_project(self.start, self.top))
        self.assertIsNone(project.lang)
        self.assertIn(str(self.repo / build.PROJECT_CONFIG), warning)

    def test_unknown_key_aborts(self):
        self.config(self.repo, {"language": "de"})
        with self.assertRaises(SystemExit):
            build.find_project(self.start, self.top)

    def test_launches_load(self):
        self.config(self.repo, {"launches": {"diff": {"run": "go"}}})
        self.assertEqual(build.find_project(self.start, self.top).launches,
                         {"diff": {"run": "go"}})

    def test_bad_launches_abort(self):
        for launches in ({"Diff": {"run": "go"}}, {"diff": {"run": 1}}):
            self.config(self.repo, {"launches": launches})
            with self.assertRaises(SystemExit, msg=repr(launches)):
                build.find_project(self.start, self.top)

    def test_bad_launch_aborts(self):
        for launch in ({"start": "go"}, {"run": 1}):
            self.config(self.repo, {"launch": launch})
            with self.assertRaises(SystemExit, msg=repr(launch)):
                build.find_project(self.start, self.top)


class LangTest(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(build.choose_lang("de", {"lang": "fr"}, "it"), "de")
        self.assertEqual(build.choose_lang(None, {"lang": "fr"}, "it"), "fr")
        self.assertEqual(build.choose_lang(None, {}, "it"), "it")
        self.assertEqual(build.choose_lang(None, {}, None), "en")

    def test_merge_keeps_the_old_language_below_spec_above_project(self):
        self.assertEqual(build.choose_lang(None, {}, "it", previous="de"), "de")
        self.assertEqual(build.choose_lang(None, {"lang": "fr"}, "it", previous="de"), "fr")
        self.assertEqual(build.choose_lang("en", {}, "it", previous="de"), "en")

    def test_previous_lang_only_on_merge_of_a_saved_folder(self):
        folder = Folder()
        try:
            build_into(sample_spec(), folder.path)
            state = read_json(folder.path / "state.json")
            state["meta"]["lang"] = "de"
            (folder.path / "state.json").write_text(json.dumps(state), encoding="utf-8")
            # Not saved yet: a merge carries nothing over, the language neither.
            self.assertIsNone(build.previous_lang(folder.path, build.REBUILD_MERGE))
            state["meta"]["folder"] = str(folder.path)
            (folder.path / "state.json").write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(build.previous_lang(folder.path, build.REBUILD_MERGE), "de")
            self.assertIsNone(build.previous_lang(folder.path, build.REBUILD_FORCE))
        finally:
            folder.cleanup()

    def test_bad_spec_lang_aborts(self):
        for bad in (["de"], "../de", ""):
            with self.assertRaises(SystemExit, msg=repr(bad)):
                build.choose_lang(None, {"lang": bad}, None)

    def test_meta_lang_and_as_spec(self):
        folder = Folder()
        try:
            build_into(sample_spec(), folder.path)
            state = read_json(folder.path / "state.json")
            self.assertEqual(state["meta"]["lang"], "en")
            self.assertEqual(read.as_spec(state)["lang"], "en")
        finally:
            folder.cleanup()


class RoundTripTest(unittest.TestCase):
    def test_build_as_spec_build_is_identical(self):
        spec = named_spec()
        spec["lang"] = "en"
        spec["launch"] = {"build": "make", "url": "http://localhost:3000/{data}"}
        spec["launches"] = {"web": {"run": "npm run dev", "url": "http://localhost:5173"}}
        spec["items"].append({"title": "E", "setup": {"launch": {"run": "go {data}"}}})
        spec["items"].append({"title": "F", "setup": {"launch": "web"}})
        folder = Folder()
        try:
            build_into(spec, folder.path)
            before_state = read_json(folder.path / "state.json")
            before_setups = read_json(folder.path / "setups.json")
            again = read.as_spec(before_state, read.load_setups(folder.path))
            self.assertEqual(again["setups"], spec["setups"])
            self.assertEqual(again["launch"], spec["launch"])
            self.assertEqual(again["launches"], spec["launches"])
            self.assertEqual([item.get("setup") for item in again["items"]],
                             ["app", "app", {"text": "Inline."}, None,
                              {"launch": {"run": "go {data}"}}, {"launch": "web"}])
            build_into(copy.deepcopy(again), folder.path)
            self.assertEqual(read_json(folder.path / "state.json"), before_state)
            self.assertEqual(read_json(folder.path / "setups.json"), before_setups)
        finally:
            folder.cleanup()


class RebuildTest(unittest.TestCase):
    def setUp(self):
        self.folder = Folder()
        build_into(sample_spec(), self.folder.path)
        state = read_json(self.folder.path / "state.json")
        state["meta"]["folder"] = str(self.folder.path)
        state["items"][0].update(state="ok", note="worked", collapsed=True,
                                 images=["images/one.png"])
        state["items"][2].update(state="problem", note="broke")
        (self.folder.path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    def tearDown(self):
        self.folder.cleanup()

    def test_saved_folder_refuses_rebuild(self):
        before = (self.folder.path / "state.json").read_bytes()
        with self.assertRaises(SystemExit) as stop:
            build_into(sample_spec(), self.folder.path)
        self.assertIn("--merge", str(stop.exception))
        self.assertEqual((self.folder.path / "state.json").read_bytes(), before)

    def test_force_overwrites(self):
        build_into(sample_spec(), self.folder.path, rebuild=build.REBUILD_FORCE)
        state = read_json(self.folder.path / "state.json")
        self.assertEqual(state["items"][0]["state"], "open")
        self.assertNotIn("folder", state["meta"])

    def test_merge_carries_progress_by_title(self):
        spec = sample_spec()
        spec["items"] = [{"title": "New"}, spec["items"][0], spec["items"][1]]
        warning = stderr_of(lambda: build_into(spec, self.folder.path,
                                               rebuild=build.REBUILD_MERGE))
        state = read_json(self.folder.path / "state.json")
        first = state["items"][1]
        self.assertEqual((first["title"], first["state"], first["note"],
                          first["collapsed"], first["images"]),
                         ("First", "ok", "worked", True, ["images/one.png"]))
        self.assertEqual(state["items"][0]["state"], "open")
        self.assertEqual(state["meta"]["folder"], str(self.folder.path))
        self.assertIn("Third", warning)
        self.assertNotIn("First", warning)

    def test_merge_keeps_observations_the_spec_does_not_give(self):
        self.save_observations("seen it")
        build_into(sample_spec(), self.folder.path, rebuild=build.REBUILD_MERGE)
        notes = read_json(self.folder.path / "state.json")["notes"]
        self.assertEqual(notes["text"], "seen it")

    def test_merge_warns_when_spec_replaces_observations(self):
        self.save_observations("seen it")
        spec = sample_spec()
        spec["notes"] = "fresh"
        warning = stderr_of(lambda: build_into(spec, self.folder.path,
                                               rebuild=build.REBUILD_MERGE))
        self.assertEqual(read_json(self.folder.path / "state.json")["notes"]["text"], "fresh")
        self.assertIn("seen it", warning)

    def save_observations(self, text: str) -> None:
        state = read_json(self.folder.path / "state.json")
        state["notes"] = {"text": text, "images": []}
        (self.folder.path / "state.json").write_text(json.dumps(state), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()

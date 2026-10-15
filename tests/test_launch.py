"""▶: POST /launch runs a setup's build → quit → run (→ url wait → open),
GET /launch reports how far it got, and the server never leaves a run behind."""

import http.server
import os
import socket
import threading
import time
import unittest

from helpers import SKILL_ROOT, Folder, RunningServer, build, build_into, serve

# Short enough to keep the suite quick, long enough for a shell to start.
SETTLE = 0.4
GRACE = 0.5
POLL = 0.05
FINAL = {"running", "done", "failed", "idle"}
WAIT_LIMIT = 10


def launch_spec(launches: dict) -> dict:
    """One item per named setup, plus one setup with nothing to start."""
    return {
        "title": "Launch",
        "setups": {**{name: {"label": name, "launch": launch}
                      for name, launch in launches.items()},
                   "plain": {"label": "Plain", "text": "nothing to start"}},
        "items": [{"title": name, "setup": name} for name in [*launches, "plain"]],
    }


def free_port() -> int:
    """A port nobody listens on — bound once, then let go."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone_soon(pid: int) -> bool:
    """A killed grandchild may linger as a zombie until init reaps it."""
    deadline = time.monotonic() + WAIT_LIMIT
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(POLL)
    return True


class QuietHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass


class LaunchTest(unittest.TestCase):
    LAUNCHES = {}

    def setUp(self):
        self.folder = Folder()
        self.root = self.folder.path
        self.opened = []
        build_into(launch_spec(self.LAUNCHES), self.root)
        self.launcher = serve.Launcher(self.root, opener=self.opened.append, settle=SETTLE,
                                       kill_grace=GRACE, poll=POLL)
        self.server = RunningServer(self.root, self.launcher)

    def tearDown(self):
        self.server.stop()
        self.folder.cleanup()

    def settled(self, setup_id: str) -> dict:
        """The status once the launch has stopped moving."""
        deadline = time.monotonic() + WAIT_LIMIT
        while True:
            status, body = self.server.launch_status(setup_id)
            self.assertEqual(status, 200, body)
            if body["phase"] in FINAL or time.monotonic() > deadline:
                return body
            time.sleep(POLL)

    def launched(self, setup_id: str) -> dict:
        status, body = self.server.launch(setup_id)
        self.assertEqual(status, 202, body)
        return self.settled(setup_id)

    def pid_of(self, name: str) -> int:
        """A pid a run wrote into the working folder's parent (its cwd)."""
        path = self.root.parent / name
        deadline = time.monotonic() + WAIT_LIMIT
        while not path.is_file() or not path.read_text().strip():
            self.assertLess(time.monotonic(), deadline, f"{name} never written")
            time.sleep(POLL)
        return int(path.read_text())


class StepsTest(LaunchTest):
    LAUNCHES = {
        "ok": {"quit": "echo quitting; exit 4", "build": "echo building", "run": "echo started"},
        "broken": {"build": "echo first; echo compile error >&2; exit 2", "run": "echo never"},
        "crash": {"run": "echo boom; exit 3"},
        "slow": {"build": "sleep 30", "run": "true"},
        "server": {"run": "echo $$ > server.pid; exec sleep 30"},
        "chatty": {"run": "seq 1 500"},
    }

    def test_done_when_run_exits_zero(self):
        result = self.launched("ok")
        self.assertEqual(result["phase"], "done", result)
        # A failing quit is logged and passed by — the app may simply not run.
        self.assertIn("quitting", result["tail"])
        self.assertIn("started", result["tail"])
        self.assertIsNone(result["url"])

    def test_every_step_has_a_header_in_the_log(self):
        self.launched("ok")
        log = (self.root / serve.LAUNCH_LOG).read_text(encoding="utf-8")
        for step, command in (("quit", "echo quitting; exit 4"), ("build", "echo building"),
                              ("run", "echo started")):
            self.assertRegex(log, rf"\d\d:\d\d:\d\d ok {step}: {command}")

    def test_build_failure_shows_its_tail(self):
        result = self.launched("broken")
        self.assertEqual(result["phase"], "failed")
        self.assertIn("2", result["message"])
        self.assertIn("compile error", result["tail"])
        self.assertNotIn("never", "".join(result["tail"]))

    def test_run_exiting_non_zero_fails(self):
        result = self.launched("crash")
        self.assertEqual(result["phase"], "failed")
        self.assertIn("boom", result["tail"])

    def test_tail_holds_only_this_launch(self):
        self.launched("broken")
        result = self.launched("ok")
        self.assertNotIn("compile error", result["tail"])

    def test_tail_is_cut(self):
        tail = self.launched("chatty")["tail"]
        self.assertEqual(len(tail), serve.TAIL_LINES)
        self.assertEqual(tail[-1], "500")

    def test_running_without_url(self):
        result = self.launched("server")
        self.assertEqual(result["phase"], "running", result)

    def test_never_launched_is_idle(self):
        status, body = self.server.launch_status("ok")
        self.assertEqual((status, body["phase"], body["tail"]), (200, "idle", []))

    def test_second_launch_while_one_is_in_progress(self):
        self.assertEqual(self.server.launch("slow")[0], 202)
        self.assertIn(self.server.launch_status("slow")[1]["phase"], serve.IN_PROGRESS)
        self.assertEqual(self.server.launch("ok")[0], 409)
        self.assertEqual(self.server.launch("slow")[0], 409)

    def test_relaunch_ends_the_previous_run(self):
        self.assertEqual(self.launched("server")["phase"], "running")
        pid = self.pid_of("server.pid")
        self.assertEqual(self.launched("ok")["phase"], "done")
        self.assertFalse(alive(pid))
        self.assertEqual(self.server.launch_status("server")[1]["phase"], "idle")

    def test_a_failed_build_leaves_the_previous_run_alive(self):
        self.assertEqual(self.launched("server")["phase"], "running")
        pid = self.pid_of("server.pid")
        self.assertEqual(self.launched("broken")["phase"], "failed")
        self.assertTrue(alive(pid))
        self.assertEqual(self.server.launch_status("server")[1]["phase"], "running")

    def test_build_comes_before_quit(self):
        self.launched("ok")
        log = (self.root / serve.LAUNCH_LOG).read_text(encoding="utf-8")
        self.assertLess(log.index(" ok build: "), log.index(" ok quit: "))

    def test_shutdown_ends_the_run(self):
        self.launched("server")
        pid = self.pid_of("server.pid")
        self.assertTrue(self.launcher.shutdown())
        self.assertFalse(alive(pid))
        self.assertFalse(self.launcher.shutdown())

    def test_shutdown_ends_a_build_in_progress(self):
        self.server.launch("slow")
        time.sleep(SETTLE)
        self.assertTrue(self.launcher.shutdown())
        self.assertEqual(self.settled("slow")["phase"], "failed")

    def test_guards(self):
        self.assertEqual(self.server.launch("ok", headers={})[0], 403)
        self.assertEqual(self.server.launch("ok", headers={"X-Checklist": "reset"})[0], 403)
        self.assertEqual(self.server.launch("ok", headers={"X-Checklist": "launch",
                                                           "Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.server.launch("nope")[0], 404)
        self.assertEqual(self.server.launch("plain")[0], 404)
        self.assertEqual(self.server.launch_status("nope")[0], 404)
        self.assertEqual(self.server.launch_status("plain")[0], 404)
        status, _ = self.server.request("GET", "/launch?setup=ok", host="evil.example")
        self.assertEqual(status, 403)
        self.assertFalse((self.root / serve.LAUNCH_LOG).exists())


class GroupTest(LaunchTest):
    LAUNCHES = {
        # The shell leaves a child behind: only killing the group reaches it.
        "family": {"run": "sleep 30 & echo $! > child.pid; wait"},
        "detached": {"run": "sleep 30 > /dev/null 2>&1 & echo $! > detached.pid"},
        "ok": {"run": "true"},
    }

    def test_relaunch_ends_the_whole_group(self):
        self.assertEqual(self.launched("family")["phase"], "running")
        child = self.pid_of("child.pid")
        self.launched("ok")
        self.assertTrue(gone_soon(child))

    def test_done_app_is_left_alone(self):
        # Exited 0 within the settle time: whatever it left running detached itself.
        self.assertEqual(self.launched("detached")["phase"], "done")
        child = self.pid_of("detached.pid")
        try:
            self.assertFalse(self.launcher.shutdown())
            self.assertTrue(alive(child))
        finally:
            os.kill(child, 9)


class UrlTest(LaunchTest):
    def setUp(self):
        self.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        up = f"http://127.0.0.1:{self.web.server_address[1]}/"
        down = f"http://127.0.0.1:{free_port()}/"
        self.up = up
        self.LAUNCHES = {
            "ready": {"run": "sleep 30", "url": up},
            "never": {"run": "sleep 30", "url": down, "ready_timeout": 1},
            "dies": {"run": f"sleep {SETTLE * 2}; exit 5", "url": down},
        }
        super().setUp()

    def tearDown(self):
        super().tearDown()
        self.web.shutdown()
        self.web.server_close()

    def test_url_answers_then_opens(self):
        result = self.launched("ready")
        self.assertEqual(result["phase"], "running", result)
        self.assertEqual(self.opened, [self.up])
        self.assertEqual(result["url"], self.up)

    def test_url_timeout(self):
        result = self.launched("never")
        self.assertEqual(result["phase"], "failed")
        self.assertIn("1 s", result["message"])
        self.assertEqual(self.opened, [])

    def test_process_death_while_waiting(self):
        result = self.launched("dies")
        self.assertEqual(result["phase"], "failed")
        self.assertIn("5", result["message"])
        self.assertEqual(self.opened, [])


class UnexpectedErrorTest(LaunchTest):
    LAUNCHES = {"ready": {"run": "sleep 30", "url": "http://127.0.0.1:1/"}}

    def setUp(self):
        super().setUp()
        self.server.stop()

        def broken_probe(url):
            raise ValueError("probe broke")
        self.launcher = serve.Launcher(self.root, opener=self.opened.append, settle=SETTLE,
                                       kill_grace=GRACE, poll=POLL, probe=broken_probe)
        self.server = RunningServer(self.root, self.launcher)

    def test_any_error_fails_the_launch_instead_of_hanging(self):
        result = self.launched("ready")
        self.assertEqual(result["phase"], "failed", result)
        self.assertIn("probe broke", result["message"])
        self.assertTrue(any("ValueError" in line for line in result["tail"]), result["tail"])
        # The worker let go of the lock: the next ▶ is not refused as busy.
        self.assertEqual(self.server.launch("ready")[0], 202)


class TemplateTest(unittest.TestCase):
    def test_launch_chrome_in_both_languages(self):
        for lang in ("en", "de"):
            table = build.strings(SKILL_ROOT / "strings" / f"{lang}.json")
            for key in ("launch_start", "launch_failed", "launch_running", "same_as"):
                self.assertTrue(table.get(key), (lang, key))


if __name__ == "__main__":
    unittest.main()

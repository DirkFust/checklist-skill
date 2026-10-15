"""Shared fixtures: build a spec into a temp folder, run serve.py in-process.

The scripts live in the skill root, not in a package, so the root goes on
sys.path here — every test module imports this first.
"""

import contextlib
import http.client
import io
import json
import pathlib
import re
import sys
import tempfile
import threading

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

import build  # noqa: E402
import serve  # noqa: E402

LANG = "en"
# Port 0 lets the OS pick a free one, so tests never fight a running checklist.
EPHEMERAL_PORT = 0
# shutdown() waits for serve_forever's next poll; the default half second, paid
# by every test that starts a server, was most of the suite's run time.
SERVE_POLL = 0.05
STATE_RE = re.compile(r'<script type="application/json" id="state">(.*?)</script>', re.S)


def sample_spec() -> dict:
    """Two items with a setup, one without — enough to tell ids and order apart."""
    return {
        "title": "Sample",
        "items": [
            {"title": "First", "setup": {
                "label": "Prep", "text": "Start it.", "commands": ["make run"],
                "links": [{"label": "Preview", "url": "http://localhost:3000"}],
                "paths": [{"label": "Data", "path": "/tmp/checklist-test-declared"}]}},
            {"title": "Second"},
            {"title": "Third", "setup": "Short form text."},
        ],
    }


def build_into(spec: dict, folder: pathlib.Path, **options) -> None:
    """What `build.py spec folder` does, minus argument parsing, fonts and the
    project config walk — `options` go straight to `build.build`."""
    shell = (SKILL_ROOT / "shell.html").read_text(encoding="utf-8")
    table = build.strings(SKILL_ROOT / "strings" / f"{LANG}.json")
    build.build(spec, shell, "", table, LANG, folder, **options)


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_quietly(call) -> tuple:
    """`call()` with stderr caught: (its result, what it wrote there)."""
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        result = call()
    return result, captured.getvalue()


def stderr_of(call) -> str:
    return run_quietly(call)[1]


class Folder:
    """A temp working folder; `cleanup()` removes it."""

    def __init__(self):
        self._temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._temp.name) / "checklist"

    def cleanup(self) -> None:
        self._temp.cleanup()


class RunningServer:
    """serve.py's server on an ephemeral port, in a background thread."""

    def __init__(self, root: pathlib.Path, launcher: "serve.Launcher | None" = None):
        self.launcher = launcher or serve.Launcher(root.resolve())
        handler = serve.handler_for(root.resolve(), serve.Pulse(), launcher=self.launcher)
        handler.log_message = lambda *_: None      # keep the test output readable
        self.server, _ = serve.listen(EPHEMERAL_PORT, 1, handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        kwargs={"poll_interval": SERVE_POLL}, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        # As main() does: whatever ▶ started must not outlive the test.
        self.launcher.shutdown()

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict | None = None, host: str | None = None,
                length: str | None = None):
        """One request; returns (status, body bytes). `host` overrides Host,
        `length` the Content-Length that would match the body."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                connection.putheader("Host", host)
            for key, value in (headers or {}).items():
                connection.putheader(key, value)
            connection.putheader("Content-Length",
                                 str(len(body or b"")) if length is None else length)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def headers_of(self, path: str) -> dict:
        """The response headers of a GET, names lower-cased."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            response.read()
            return {name.lower(): value for name, value in response.getheaders()}
        finally:
            connection.close()

    def page_state(self) -> dict:
        """The state GET / injects into the page."""
        status, body = self.request("GET", "/")
        assert status == 200, status
        return json.loads(STATE_RE.search(body.decode("utf-8")).group(1))

    def put_state(self, state: dict):
        return self.request("PUT", "/state", json.dumps(state).encode("utf-8"),
                            {"Content-Type": "application/json"})

    def post(self, route: str, payload: dict, headers: dict | None = None):
        """POST /<route>; `headers` replaces the guard header the page sends."""
        sent = {"Content-Type": "application/json",
                **({"X-Checklist": route} if headers is None else headers)}
        return self.request("POST", f"/{route}", json.dumps(payload).encode("utf-8"), sent)

    def reveal(self, path: str):
        return self.post("reveal", {"path": path})

    def reset(self, setup_id: str, headers: dict | None = None):
        return self.post("reset", {"setup": setup_id}, headers)

    def launch(self, setup_id: str, headers: dict | None = None):
        return self.post("launch", {"setup": setup_id}, headers)

    def launch_status(self, setup_id: str) -> tuple[int, dict | bytes]:
        """GET /launch; the parsed JSON on 200, the raw body otherwise."""
        status, body = self.request("GET", f"/launch?setup={setup_id}")
        return status, (json.loads(body) if status == 200 else body)

    def peek(self, setup_id: str, index) -> tuple[int, dict | bytes]:
        """GET /peek; the parsed JSON on 200, the raw body otherwise."""
        status, body = self.request("GET", f"/peek?setup={setup_id}&file={index}")
        return status, (json.loads(body) if status == 200 else body)

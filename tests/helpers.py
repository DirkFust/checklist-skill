"""Shared fixtures: build a spec into a temp folder, run serve.py in-process.

The scripts live in the skill root, not in a package, so the root goes on
sys.path here — every test module imports this first.
"""

import http.client
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


def build_into(spec: dict, folder: pathlib.Path) -> None:
    """What `build.py spec folder` does, minus argument parsing and fonts."""
    shell = (SKILL_ROOT / "shell.html").read_text(encoding="utf-8")
    table = build.strings(SKILL_ROOT / "strings" / f"{LANG}.json")
    build.build(spec, shell, "", table, LANG, folder)


def read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Folder:
    """A temp working folder; `cleanup()` removes it."""

    def __init__(self):
        self._temp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self._temp.name) / "checklist"

    def cleanup(self) -> None:
        self._temp.cleanup()


class RunningServer:
    """serve.py's server on an ephemeral port, in a background thread."""

    def __init__(self, root: pathlib.Path):
        handler = serve.handler_for(root.resolve(), serve.Pulse())
        handler.log_message = lambda *_: None      # keep the test output readable
        self.server, _ = serve.listen(EPHEMERAL_PORT, 1, handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def request(self, method: str, path: str, body: bytes | None = None,
                headers: dict | None = None, host: str | None = None):
        """One request; returns (status, body bytes). `host` overrides Host."""
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                connection.putheader("Host", host)
            for key, value in (headers or {}).items():
                connection.putheader(key, value)
            connection.putheader("Content-Length", str(len(body or b"")))
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, response.read()
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

    def reveal(self, path: str):
        return self.request("POST", "/reveal", json.dumps({"path": path}).encode("utf-8"),
                            {"Content-Type": "application/json", "X-Checklist": "reveal"})

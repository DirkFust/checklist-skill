#!/usr/bin/env python3
"""Serves a local checklist and takes its state back.

    serve.py folder [--port 8765] [--open]

The folder comes from `build.py spec.json folder` and holds `template.html` and
`state.json`. On every request the server fills the state into the template; the
save button PUTs it back.

Fresh attachments arrive as `data:` URIs and become files under `images/`, named
after their content. The state then carries nothing but paths — which is why
there is no size limit here, and no reason to shrink anything.

Two routes lead to test data: whatever the browser can display lives under
`data/` in the working folder and is linked over `http`. Whatever has to sit in
the project gets a button that sends `POST /reveal` — the server reveals the path
in the Finder. Only paths the build froze into `setups.json` are allowed.

Setups are not the page's to change. `state.json` names each item's setup by id,
`setups.json` holds what the id stands for; GET fills the setups into the page,
PUT throws away whatever setup the page sends back and keeps the ids on disk.
Folders built before `setups.json` existed carry the setup objects in the state
and are served as they always were.

Every request has to name this server in its `Host` header — `127.0.0.1:<port>`
or `localhost:<port>`. A page on a foreign domain that has been re-pointed at
127.0.0.1 (DNS rebinding) counts as same-origin to the browser; its Host header
still carries the foreign name, and that is where it gets stopped.

Binds to 127.0.0.1 and nothing else, and the page it serves fetches nothing over
the network — the webfaces are inlined. The server runs while someone is ticking
boxes and may go away afterwards; everything is on disk already.

The default port is shared by every checklist, so a busy one is not an error: the
server takes the next free port and prints the URL it actually bound. Read that
line before handing the URL to anyone — otherwise they tick off somebody else's
list and save it into a folder nobody is reading.

It also stops by itself, because nobody remembers to stop it. Two silences mean
different things. No request at all: the page knocks every few seconds while it
is open, so silence means the tab is gone (`--idle`). Requests still arriving but
`.read` lying in the folder: `read.py` has taken the results, so an open tab is
no longer a reason to stay up (`--read-idle`). Every save deletes `.read` again —
whoever carries on ticking after the report is back under the long rule. Nothing
is lost when it goes: everything was written at save time.
"""

import argparse
import base64
import errno
import hashlib
import html
import http.server
import json
import os
import pathlib
import re
import shlex
import signal
import socketserver
import subprocess
import sys
import threading
import time
import webbrowser

DATA_RE = re.compile(r"^data:(?P<mime>image/[a-z0-9.+-]+);base64,(?P<payload>.*)$", re.S)
# Only self-made names get swept — whatever a human puts beside them stays.
OWNED_RE = re.compile(r"^[0-9a-f]{12}\.[a-z0-9]+$")
# One path segment. Subfolders under `data/` are fine — each segment has to
# look like this, and the result still has to sit inside the served folder.
SEGMENT_RE = re.compile(r"^[0-9a-zA-Z][0-9a-zA-Z._-]*$")
# Attachments land in `images/`, browsable test data in `data/`. The German names
# are still served so checklists built before the rename keep their pictures.
SHOTS = "images"
SERVED_DIRS = ("images", "data", "bilder", "daten")
SUFFIX = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
          "image/webp": "webp", "image/heic": "heic", "image/svg+xml": "svg"}
MAX_BODY = 256 * 1024 * 1024
# Every read and write of state.json goes through this. The server is threaded,
# so without it a GET can read the file while a PUT has truncated it — and two
# tabs of the same checklist can overwrite each other's ticks between the read
# and the write.
LOCK = threading.RLock()
PORT_TRIES = 20
# read.py drops this beside state.json once it has taken the results.
READ_MARK = ".read"
# Written by build.py; absent in folders built before setups were frozen.
SETUPS_FILE = "setups.json"
LOOPBACK_NAMES = ("127.0.0.1", "localhost")
# Never opened at all: the URL has just been handed over and the human may be
# somewhere else entirely. That silence says nothing, so it gets an hour.
GRACE_UNOPENED = 60 * 60
WATCH_TICK = 15
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
         ".webp": "image/webp", ".heic": "image/heic", ".svg": "image/svg+xml",
         ".json": "application/json; charset=utf-8", ".txt": "text/plain; charset=utf-8",
         ".md": "text/plain; charset=utf-8", ".csv": "text/plain; charset=utf-8",
         ".log": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8",
         ".pdf": "application/pdf"}


def escape(doc: dict) -> str:
    """Escape `<`, or a note takes the script element apart.

    The state is served inside a `<script type="application/json">`; the parser
    ends that element at the first `</script`, wherever it comes from. A note
    reading "the `</script>` tag" would take the rest of the page apart.
    """
    return json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")


def materialize(images: list, folder: pathlib.Path) -> list:
    """data: URIs become files, anything else already is a path."""
    out = []
    for image in images:
        if not isinstance(image, str):
            continue
        match = DATA_RE.match(image)
        if not match:
            out.append(image)
            continue
        blob = base64.b64decode(match.group("payload"))
        name = hashlib.sha256(blob).hexdigest()[:12] + "." + SUFFIX.get(match.group("mime"), "bin")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        if not path.exists():
            path.write_bytes(blob)
        out.append(SHOTS + "/" + name)
    return out


def sweep(state: dict, folder: pathlib.Path) -> int:
    """Sweep away attachments nobody names any more."""
    if not folder.is_dir():
        return 0
    kept = {pathlib.PurePosixPath(p).name
            for bucket in [*state["items"], state["notes"]] for p in bucket["images"]}
    gone = 0
    for path in folder.iterdir():
        if path.is_file() and OWNED_RE.match(path.name) and path.name not in kept:
            path.unlink()
            gone += 1
    return gone


def read_state(root: pathlib.Path) -> dict:
    """Never read state.json directly — a PUT may be halfway through writing it."""
    with LOCK:
        return json.loads((root / "state.json").read_text(encoding="utf-8"))


def write_state(root: pathlib.Path, state: dict) -> None:
    """Write through a temp file: a half-written state.json is a lost session,
    and `write_text` truncates before it writes a single byte."""
    target = root / "state.json"
    scratch = target.with_suffix(".json.new")
    with LOCK:
        scratch.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(scratch, target)


def read_setups(root: pathlib.Path) -> dict | None:
    """The frozen setups by id, or None for a folder built before they existed."""
    path = root / SETUPS_FILE
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["setups"]


def inject(state: dict, setups: dict | None) -> dict:
    """The state as the page sees it: each setup id replaced by what it stands for.

    The id travels along, so the page can tell two items with the same setup
    apart from two that merely look alike.
    """
    if setups is None:
        return state
    items = [{**item, "setup": {"id": item["setup"], **setups[item["setup"]]}}
             if isinstance(item.get("setup"), str) and item["setup"] in setups
             else item
             for item in state.get("items", [])]
    return {**state, "items": items}


def pin_setups(state: dict, on_disk: dict) -> dict:
    """Put the on-disk setup ids back, whatever the page sent along.

    Matched by position: the page can tick, note and attach, but it cannot add,
    drop or reorder items — the caller has checked the counts agree.
    """
    items = [{**item, "setup": kept.get("setup")}
             for item, kept in zip(state["items"], on_disk.get("items", []))]
    return {**state, "items": items}


def normalize(state: dict, folder: pathlib.Path) -> dict:
    items = []
    for raw in state.get("items", []):
        items.append({
            "title": raw.get("title", ""),
            "detail": raw.get("detail", ""),
            "setup": raw.get("setup"),
            "state": raw.get("state", "open"),
            "collapsed": bool(raw.get("collapsed")),
            "note": raw.get("note", ""),
            "images": materialize(raw.get("images") or [], folder),
        })
    notes = state.get("notes") or {}
    # The folder goes into the state so the file says where it belongs — after a
    # /clear that is the only thread back to the results.
    meta = dict(state.get("meta") or {})
    meta["folder"] = str(folder.parent)
    return {
        "meta": meta,
        "items": items,
        "notes": {"text": notes.get("text", ""),
                  "images": materialize(notes.get("images") or [], folder)},
    }


class Pulse:
    """When the last request came in, and whether the page was ever opened."""

    def __init__(self):
        self.last = time.monotonic()
        self.opened = False


class Handler(http.server.BaseHTTPRequestHandler):
    root: pathlib.Path
    restart: str        # command the page can offer for restarting the server
    pulse: Pulse

    def parse_request(self) -> bool:
        """Refuse any request not addressed to this server by name and port.

        Checked here rather than per method, so no route can forget it.
        """
        if not super().parse_request():
            return False
        port = self.server.server_address[1]
        allowed = {f"{name}:{port}" for name in LOOPBACK_NAMES}
        if (self.headers.get("Host") or "").lower() not in allowed:
            self.fail(403, "foreign host")
            return False
        return True

    def handle_one_request(self):
        # Every request is a sign of life — the page knocks by itself while it is
        # open, so silence is what tells the watchdog nobody is looking any more.
        self.pulse.last = time.monotonic()
        super().handle_one_request()

    def log_message(self, fmt, *args):
        if self.command == "HEAD":
            return          # the page knocks every five seconds — that floods the log
        sys.stderr.write("  %s\n" % (fmt % args))

    def send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code: int, why: str) -> None:
        self.send(code, why.encode("utf-8"), "text/plain; charset=utf-8")

    def do_HEAD(self) -> None:
        # This is how the page knocks after a save has failed.
        code = 200 if self.path.split("?", 1)[0] in ("/", "/index.html") else 404
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def declared_paths(self) -> set:
        """Reveal only what the checklist declares — never what someone hands in.

        With frozen setups that is `setups.json` alone: the state is written by
        the page, so whatever it names proves nothing.
        """
        setups = read_setups(self.root)
        if setups is not None:
            declared = setups.values()
        else:
            declared = [item.get("setup") or {} for item in read_state(self.root).get("items", [])]
        return {entry.get("path") for setup in declared for entry in (setup.get("paths") or [])}

    def body_length(self) -> int | None:
        """The declared body size, or None once the client has been told why not."""
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.fail(400, "Content-Length is not a number")
            return None

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/reveal":
            self.fail(404, "POST /reveal only")
            return
        # A header of our own: a foreign page would first have to send a preflight,
        # and there is none here. Sec-Fetch-Site rules out the rest.
        if self.headers.get("X-Checklist") != "reveal":
            self.fail(403, "missing header")
            return
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            self.fail(403, "foreign origin")
            return
        length = self.body_length()
        if length is None:
            return
        try:
            wanted = json.loads(self.rfile.read(length)).get("path", "")
        except json.JSONDecodeError:
            self.fail(400, "not valid JSON")
            return
        if wanted not in self.declared_paths():
            self.fail(403, "that path is not in the checklist")
            return
        target = pathlib.Path(wanted)
        if not target.exists():
            self.fail(404, "does not exist yet — create it first")
            return
        # Open folders, only reveal files: a click must not launch an application.
        subprocess.run(["open", str(target)] if target.is_dir() else ["open", "-R", str(target)],
                       check=False)
        self.send(200, b"{}", "application/json; charset=utf-8")

    @staticmethod
    def under(base: pathlib.Path, rest: str) -> pathlib.Path | None:
        """The file `rest` names inside `base`, or None. Test data may sit in
        subfolders; `..`, absolute paths and symlinks that point out do not."""
        segments = rest.split("/")
        if not segments or not all(SEGMENT_RE.match(part) for part in segments):
            return None
        file = (base / rest).resolve()
        if not file.is_file() or not file.is_relative_to(base.resolve()):
            return None
        return file

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self.pulse.opened = True
            template = (self.root / "template.html").read_text(encoding="utf-8")
            state = inject(read_state(self.root), read_setups(self.root))
            page = (template.replace("%%STATE%%", escape(state))
                            .replace("%%RESTART%%", html.escape(self.restart))
                            .replace("%%FOLDER%%", html.escape(str(self.root))))
            self.send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        for folder in SERVED_DIRS:
            prefix = f"/{folder}/"
            if not path.startswith(prefix):
                continue
            file = self.under(self.root / folder, path[len(prefix):])
            if file is None:
                self.fail(404, "not found")
                return
            self.send(200, file.read_bytes(),
                      TYPES.get(file.suffix, "application/octet-stream"))
            return
        self.fail(404, "nothing here")

    def do_PUT(self) -> None:
        if self.path.split("?", 1)[0] != "/state":
            self.fail(404, "PUT /state only")
            return
        length = self.body_length()
        if length is None:
            return
        if not 0 < length <= MAX_BODY:
            self.fail(413, "body missing or too large")
            return
        try:
            incoming = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as error:
            self.fail(400, f"not valid JSON: {error}")
            return
        setups = read_setups(self.root)
        # Materializing, writing and sweeping are one step: a second tab saving in
        # between would sweep away the attachments this save has just written.
        with LOCK:
            # Checked before normalizing: that already writes attachments, and a
            # refused save must not leave files behind.
            on_disk = read_state(self.root)
            if setups is not None:
                expected, got = len(on_disk.get("items", [])), len(incoming.get("items") or [])
                if got != expected:
                    self.fail(409, f"the checklist has {expected} items, this save has {got} — "
                                   f"reload the page; items cannot be added or removed from it")
                    return
            state = normalize(incoming, self.root / SHOTS)
            if setups is not None:
                state = pin_setups(state, on_disk)
            write_state(self.root, state)
            gone = sweep(state, self.root / SHOTS)
            # There is something new here, so the last read is spent: whoever is
            # still ticking must not have the server pulled from under them.
            (self.root / READ_MARK).unlink(missing_ok=True)
        shots = sum(len(b["images"]) for b in [*state["items"], state["notes"]])
        sys.stderr.write(f"  saved — {shots} attachment(s), {gone} orphan(s) removed\n")
        self.send(200, json.dumps(inject(state, setups), ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def handler_for(root: pathlib.Path, pulse: Pulse, restart: str = "") -> type[Handler]:
    """A Handler bound to one folder.

    A subclass rather than attributes set on Handler itself, so two servers in one
    process — the tests start one per case — never share a folder.
    """
    return type("FolderHandler", (Handler,), {"root": root, "pulse": pulse, "restart": restart})


def listen(port: int, tries: int, handler: type[Handler]) -> tuple[Server, int]:
    """Bind the first free port from `port` upwards.

    The default port is shared by every checklist, and several are often open at
    once. Refusing to start would be the safe half of the answer — the dangerous
    half is that the URL is handed over anyway, and the human then ticks off a
    *different* checklist and saves it into a folder nobody is reading. So the
    server takes the next port instead and says which one it took.
    """
    for offset in range(tries):
        try:
            return Server(("127.0.0.1", port + offset), handler), port + offset
        except OSError as busy:
            if busy.errno not in (errno.EADDRINUSE, errno.EACCES):
                raise
    sys.exit(f"ports {port}–{port + tries - 1} are all busy — pass a free --port")


def watchdog(server: Server, root: pathlib.Path, pulse: Pulse,
             quiet: int, after_read: int, stop: threading.Event) -> None:
    """End the server once nobody needs it any more.

    The read rule comes first: once the results are out, an open tab is no longer
    a reason to stay up — and that is the case the plain idle clock never
    catches, because a tab left open keeps knocking forever.
    """
    while not stop.wait(WATCH_TICK):
        if after_read and (root / READ_MARK).exists():
            limit, why = after_read, "read, then quiet"
        elif quiet and pulse.opened:
            limit, why = quiet, "no page open"
        elif quiet:
            limit, why = GRACE_UNOPENED, "never opened"
        else:
            continue
        idle = time.monotonic() - pulse.last
        if idle >= limit:
            sys.stderr.write(f"  idle for {int(idle // 60)} min ({why}) — stopping\n")
            server.shutdown()
            return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the browser right away")
    parser.add_argument("--idle", type=int, default=15, metavar="MIN",
                        help="stop after this long without a request (0 = never)")
    parser.add_argument("--read-idle", type=int, default=30, metavar="MIN",
                        help="stop this long after read.py took the results (0 = never)")
    args = parser.parse_args()

    root = pathlib.Path(args.folder).resolve()
    for needed in ("template.html", "state.json"):
        if not (root / needed).is_file():
            sys.exit(f"{root}/{needed} is missing — build it first: build.py spec.json {args.folder}")

    pulse = Pulse()
    handler = handler_for(root, pulse)
    server, port = listen(args.port, PORT_TRIES, handler)
    handler.restart = shlex.join(
        ["python3", str(pathlib.Path(__file__).resolve()), str(root), "--port", str(port)])
    url = f"http://127.0.0.1:{port}/"
    with server:
        # Long runs get killed from outside (observed: exit code 144 after several
        # hours, with a clean goodbye in the log — the trigger sits outside this
        # process). Nothing is lost: every save writes through, and the page holds
        # on to its state. Ignoring SIGURG is cheap insurance, SIGTERM is answered.
        signal.signal(signal.SIGURG, signal.SIG_IGN)
        # shutdown() from inside the handler deadlocks with serve_forever on the
        # same thread — the exception arrives cleanly down below instead.
        def stop(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        # Read this line before handing the URL on: the port may not be the one
        # that was asked for.
        print(f"{url} — {root}", flush=True)
        quiet, after_read = max(args.idle, 0) * 60, max(args.read_idle, 0) * 60
        limits = ([f"{args.idle} min without a page"] if quiet else []) + \
                 ([f"{args.read_idle} min after read.py"] if after_read else [])
        print("  stops by itself: " + (", ".join(limits) if limits else "never"), flush=True)
        stop = threading.Event()
        if quiet or after_read:
            threading.Thread(target=watchdog, daemon=True,
                             args=(server, root, pulse, quiet, after_read, stop)).start()
        if args.open:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
        print("stopped — the state is in state.json", flush=True)


if __name__ == "__main__":
    main()

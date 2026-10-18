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
in the Finder. Only paths the build froze into `setups.json` are allowed — a
setup's `paths` and its `files`. Whatever is served from `data/` and `images/`
comes with `Content-Security-Policy: sandbox`: a test page or an SVG still
renders, but its script never runs as this origin.

A setup's files have two more routes, both reading `setups.json` only:

  · `POST /reset {"setup": id}` copies the setup's managed files back from
    `.pristine/`, where the build left an untouched copy → `{"reset": [names]}`.
    External files are never written. All copies are checked first; one missing
    → 409 and nothing is touched. So is a managed file (or its copy) that is a
    symlink or sits in a folder resolving out of `data/` (`.pristine/`) — a
    reset must not write through a link to somewhere else; /peek refuses those
    the same way.
  · `GET /peek?setup=<id>&file=<index>` → name, size, encoding, bom, eol
    (lf | crlf | cr | mixed | none), final_newline, binary, and at most 400
    `lines`, each with its line end still on it (`truncated` says if more).
    Decoded with the declared encoding, else UTF-8; what does not decode, or
    holds NULs, is `binary` and has no lines.

A setup with a frozen `launch` can be started from the page (▶), again from
`setups.json` only — the commands are the build's, never the page's:

  · `POST /launch {"setup": id}` → 202 `{"phase"}`; the steps run in a thread:
    `build` (15 min, non-zero fails — and leaves whatever runs untouched,
    which the message then says; skipped when the server's last build was
    this one, it succeeded, and no file git does not ignore has changed
    since — outside a repository it always runs) →
    end the run an earlier ▶ of this server started (SIGTERM to its process
    group, SIGKILL after 3 s) → `quit` (30 s, a failure is logged, not fatal)
    → `run`, detached into a process group of its own. Exited 0 within 3 s: `done` (an app that handed itself over).
    Exited otherwise: `failed`. Alive with a `url`: `waiting` until the url
    answers HTTP, then the browser opens it → `running`; no answer within
    `ready_timeout` → `failed`, and the run, still alive, is ended. 409 while
    a launch is under way: one build at a time.
  · `GET /launch?setup=<id>` → phase (idle | building | quitting | starting |
    waiting | running | done | failed), message, `tail` (the last 20 lines
    this launch wrote — the run's once it has one, else the build's and
    quit's) and url. Build and quit write into `launch.log` in the folder,
    the run into `launch-run.log`, so a dev server that keeps talking never
    buries the next build's error; each step under a header line with time,
    setup id, step and command.

Whatever ▶ started and is still alive goes down with the server — on a stop,
a SIGTERM or SIGHUP (the terminal closed), and the idle stop alike. A second
signal while that cleanup runs is ignored rather than cutting it short.

Mutating POSTs carry `X-Checklist: <route>` — `reveal` for /reveal, `reset` for
/reset, `launch` for /launch — so the header sent for one action never passes
for another.

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
import codecs
import contextlib
import dataclasses
import errno
import hashlib
import html
import http.client
import http.server
import json
import os
import pathlib
import re
import shlex
import signal
import socketserver
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser

from workfolder import (BOMS, DATA_DIR, DEFAULT_ENCODING, PRISTINE_DIR, READ_MARK, SEGMENT_RE,
                        SETUPS_FILE, contained, replace_file)

DATA_RE = re.compile(r"^data:(?P<mime>image/[a-z0-9.+-]+);base64,(?P<payload>.*)$", re.S)
# Only self-made names get swept — whatever a human puts beside them stays.
OWNED_RE = re.compile(r"^[0-9a-f]{12}\.[a-z0-9]+$")
# Attachments land in `images/`, browsable test data in `data/`. The German names
# are still served so checklists built before the rename keep their pictures.
SHOTS = "images"
SERVED_DIRS = ("images", "data", "bilder", "daten")
SUFFIX = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
          "image/webp": "webp", "image/heic": "heic", "image/svg+xml": "svg"}
MAX_BODY = 256 * 1024 * 1024
# A POST carries one short field — a setup id or a path. Anything near this is
# not the page talking.
MAX_POST_BODY = 64 * 1024
# Every read and write of state.json goes through this. The server is threaded,
# so without it a GET can read the file while a PUT has truncated it — and two
# tabs of the same checklist can overwrite each other's ticks between the read
# and the write.
LOCK = threading.RLock()
PORT_TRIES = 20
# A preview, not a viewer: enough to see what a test file looks like.
PEEK_LINES = 400
# The encodings that have a byte order mark, by codecs.lookup's spelling.
UNICODE_CODECS = ("utf-8", "utf-16", "utf-32")
LINE_END_RE = re.compile(r"\r\n|\r|\n")
# Each line keeps its end, so the page can show which one it is.
LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")
EOL_NAMES = {"\n": "lf", "\r\n": "crlf", "\r": "cr"}
LOOPBACK_NAMES = ("127.0.0.1", "localhost")
# The one field each mutating POST reads, always a string.
POST_FIELDS = {"reveal": "path", "reset": "setup", "launch": "setup"}
# Fields of an item (and of the notes) a PUT may only send as text.
STATE_TEXT_FIELDS = ("title", "detail", "state", "note", "text")
# ▶: every step writes here, in the working folder — a build's output is what
# tells a failed launch apart from a slow one.
LAUNCH_LOG = "launch.log"
# …except the run, which gets a file of its own: a dev server keeps writing for
# as long as it lives, and in launch.log it would bury the output of the next
# build — the very lines that say why that build failed.
LAUNCH_RUN_LOG = "launch-run.log"
QUIT_TIMEOUT = 30
# What stops the server cleanly, taking ▶'s run along. SIGINT is Ctrl-C, which
# Python already turns into KeyboardInterrupt.
STOP_SIGNALS = (signal.SIGTERM, signal.SIGHUP)
BUILD_TIMEOUT = 15 * 60
# The git calls that tell whether a build can be skipped — a repository too
# slow to answer gets built instead.
GIT_TIMEOUT = 10
# A run that ends this quickly either detached itself (an app that hands over to
# launchd and exits 0) or failed to start; one still alive is a server.
SETTLE_SECONDS = 3
KILL_GRACE = 3
GROUP_POLL = 0.05
READY_POLL = 0.5
READY_PROBE_TIMEOUT = 1
TAIL_LINES = 20
# Enough for 20 lines of any sane width — the tail never reads the whole log.
TAIL_BYTES = 64 * 1024
IDLE, BUILDING, QUITTING, STARTING, WAITING, RUNNING, DONE, FAILED = (
    "idle", "building", "quitting", "starting", "waiting", "running", "done", "failed")
IN_PROGRESS = {BUILDING, QUITTING, STARTING, WAITING}
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


def decode(content: bytes, encoding: str, mark: str | None) -> str | None:
    """The text, or None when it is none: undecodable, or carrying NULs — which
    UTF-8 lets through, and which no file a human reads has."""
    try:
        codec = codecs.lookup(encoding).name
        text = (content[len(BOMS[codec]):] if mark == codec else content).decode(codec)
    except (LookupError, UnicodeDecodeError):
        return None
    return None if "\x00" in text else text


def unicode_encoding(encoding: str) -> bool:
    try:
        return codecs.lookup(encoding).name.startswith(UNICODE_CODECS)
    except LookupError:
        return False


def peek(name: str, content: bytes, encoding: str | None) -> dict:
    """What a test file looks like inside — the things an editor hides and a
    test may hinge on: encoding, byte order mark, line ends, the last newline.

    Decoded with the encoding the spec declared, else UTF-8; a BOM is reported
    whichever Unicode one it is (a single-byte encoding has none), and stripped
    when it belongs to that encoding.
    """
    encoding = encoding or DEFAULT_ENCODING
    # A BOM exists only in the Unicode encodings: `ÿþ` at the start of a
    # Latin-1 file is text that happens to share the UTF-16-LE mark's bytes.
    mark = next((codec for codec, bom in BOMS.items() if content.startswith(bom)), None) \
        if unicode_encoding(encoding) else None
    text = decode(content, encoding, mark)
    result = {"name": name, "size": len(content), "encoding": encoding,
              "bom": mark is not None, "eol": "none", "final_newline": False,
              "binary": text is None, "lines": [], "truncated": False}
    if text is None:
        return result
    kinds = {EOL_NAMES[end] for end in LINE_END_RE.findall(text)}
    lines = LINE_RE.findall(text)
    return {**result,
            "eol": kinds.pop() if len(kinds) == 1 else "mixed" if kinds else "none",
            "final_newline": text.endswith(("\n", "\r")),
            "lines": lines[:PEEK_LINES], "truncated": len(lines) > PEEK_LINES}


def file_location(root: pathlib.Path, entry: dict) -> pathlib.Path | None:
    """Where a setup's file is. A managed one sits under this folder's data/ —
    found by name rather than by the frozen path, so a folder that was moved
    still resets and previews its own files. None for a managed one that is
    not safely in there (see `workfolder.contained`): a reset writes there."""
    if not entry.get("managed"):
        return pathlib.Path(entry["path"])
    return contained(root / DATA_DIR, entry["name"])


def pin_setups(state: dict, on_disk: dict) -> dict:
    """Put the on-disk setup ids back, whatever the page sent along.

    Matched by position: the page can tick, note and attach, but it cannot add,
    drop or reorder items — the caller has checked the counts agree.
    """
    items = [{**item, "setup": kept.get("setup")}
             for item, kept in zip(state["items"], on_disk.get("items", []), strict=True)]
    return {**state, "items": items}


def state_problem(state) -> str | None:
    """What is wrong with a state a PUT brought, or None. Only the shape
    normalize relies on — a list where it calls `.get`, a string where it
    walks images letter by letter — so a broken client gets a 400 rather
    than a dropped connection or a state.json full of nonsense."""
    if not isinstance(state, dict):
        return "the state must be a JSON object"
    # Absent or null is fine — normalize fills those in; an empty list is not.
    meta, items, notes = (state.get(key) for key in ("meta", "items", "notes"))
    if meta is not None and not isinstance(meta, dict):
        return "meta must be an object"
    if items is not None and not (isinstance(items, list)
                                  and all(isinstance(item, dict) for item in items)):
        return "items must be a list of objects"
    if notes is not None and not isinstance(notes, dict):
        return "notes must be an object"
    buckets = [(f"item {number}", item) for number, item in enumerate(items or [], 1)]
    buckets.append(("notes", notes or {}))
    for where, bucket in buckets:
        for field in STATE_TEXT_FIELDS:
            if field in bucket and not isinstance(bucket[field], str):
                return f"{where}: {field} must be text"
        if not isinstance(bucket.get("images") or [], list):
            return f"{where}: images must be a list"
    return None


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


def reveal_command(target: pathlib.Path) -> list:
    """Open folders, only reveal files: a click must not launch an application.
    A package is a folder to the file system and an application, a plugin or a
    document to `open` — `.app`, `.rtfd`, `.xcodeproj`, `.pages` and more than
    anyone can list, most without `Contents/Info.plist`. So only a folder whose
    name has no suffix and that carries no Info.plist is opened; everything
    else is revealed like a file."""
    package = target.suffix != "" or (target / "Contents" / "Info.plist").is_file()
    if target.is_dir() and not package:
        return ["open", str(target)]
    return ["open", "-R", str(target)]


def open_url(url: str) -> None:
    """The default browser, the way a click in the Finder picks it."""
    subprocess.run(["open", url], check=False)


def answers(url: str) -> bool:
    """Whether anything answers HTTP at `url`. Any status counts — a dev server
    saying 404 is up. Certificates go unchecked: a local https dev server has a
    self-signed one, and this request carries nothing worth protecting."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(parts.hostname, parts.port,
                                                 timeout=READY_PROBE_TIMEOUT, context=context)
    else:
        connection = http.client.HTTPConnection(parts.hostname, parts.port,
                                                timeout=READY_PROBE_TIMEOUT)
    try:
        connection.request("GET", urllib.parse.urlunsplit(("", "", parts.path or "/",
                                                           parts.query, "")))
        connection.getresponse()
        return True
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def size_of(path: pathlib.Path) -> int:
    return path.stat().st_size if path.is_file() else 0


def tail(path: pathlib.Path, start: int, end: int | None) -> list:
    """The last lines of one stretch of a log, read from its end."""
    try:
        with path.open("rb") as log:
            stop = log.seek(0, os.SEEK_END) if end is None else end
            begin = max(start, stop - TAIL_BYTES)
            log.seek(begin)
            chunk = log.read(stop - begin)
    except FileNotFoundError:
        return []
    lines = chunk.decode("utf-8", "replace").splitlines()
    if begin > start:
        lines = lines[1:]               # cut off mid-line
    return lines[-TAIL_LINES:]


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def end_group(process: subprocess.Popen, grace: float) -> bool:
    """SIGTERM to the process's whole group, SIGKILL to whatever is left after
    `grace`. False when the process had already exited: what it left running
    then detached itself on purpose — an app handed over to launchd — and is
    not ours to chase."""
    if process.poll() is not None:
        return False
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    # Reaping the leader as it goes, or its zombie alone keeps the group alive.
    while process.poll() is None or group_alive(process.pid):
        if time.monotonic() >= deadline:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
            break
        time.sleep(GROUP_POLL)
    process.wait()
    return True


def git_output(cwd: str, *args: str) -> bytes | None:
    """What a git command printed; None if it failed, or there is no git."""
    try:
        done = subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def source_fingerprint(cwd: str, working_folder: pathlib.Path) -> str | None:
    """What a build in `cwd` builds from, as one hash: every file of the
    repository git does not ignore, tracked or not, by path, size and mtime.
    Stat'ed here rather than asked of `git status`, whose index cache misses
    an edit of the same size within the same second. Ignored files — build
    output, as a rule — do not count, nor does the working folder, which ▶
    writes its logs into. None outside a repository: there nothing can tell
    that the sources stayed the same."""
    top = git_output(cwd, "rev-parse", "--show-toplevel")
    if top is None:
        return None
    top_path = pathlib.Path(os.fsdecode(top.strip())).resolve()
    pathspec = [":(top)"]     # the whole repository — `:(top).` matches nothing
    with contextlib.suppress(ValueError):
        inside = working_folder.resolve().relative_to(top_path)
        pathspec.append(f":(top,exclude){inside}")
    listing = git_output(cwd, "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                         "--", *pathspec)
    if listing is None:
        return None
    digest = hashlib.sha256()
    # Paths come relative to cwd; a file deleted but still in the index
    # stats as missing, the same each time.
    for path in filter(None, listing.split(b"\0")):
        digest.update(path + b"\0")
        try:
            stat = (pathlib.Path(cwd) / os.fsdecode(path)).stat()
            digest.update(f"{stat.st_size}:{stat.st_mtime_ns}\0".encode())
        except OSError:
            digest.update(b"-\0")
    return digest.hexdigest()


class Stopping(Exception):
    """The server is going down: no further step may start."""


@dataclasses.dataclass
class Launch:
    """One press of ▶ for one setup: how far it got, and which stretches of
    launch.log and launch-run.log are its own."""
    setup_id: str
    start: int                              # offset in launch.log
    url: str | None
    phase: str
    message: str = ""
    end: int | None = None                  # set once a later launch writes
    process: subprocess.Popen | None = None  # the run, once spawned
    run_start: int | None = None            # offset in launch-run.log, once spawned
    run_end: int | None = None              # set once a later run writes


class Launcher:
    """▶ for one server: a setup's build → quit → run (→ url wait → open) in a
    worker thread, its status per setup id, and at most one live run.

    One launch at a time — a second ▶ during a build would start a parallel
    build of the same project. Every step gets a process group of its own, so
    stopping one reaches what it started too (a dev server's watcher, a
    bundler) and nothing else. Timeouts and the opener are arguments so the
    tests can run in fractions of a second and open no browser; the readiness
    probe too, so they can make it fail in ways a real url would only rarely.
    """

    def __init__(self, root: pathlib.Path, opener=open_url, quit_timeout: float = QUIT_TIMEOUT,
                 build_timeout: float = BUILD_TIMEOUT, settle: float = SETTLE_SECONDS,
                 kill_grace: float = KILL_GRACE, poll: float = READY_POLL, probe=answers):
        self.root = root
        self.root = root
        self.log, self.run_log = root / LAUNCH_LOG, root / LAUNCH_RUN_LOG
        self.opener, self.probe = opener, probe
        self.quit_timeout, self.build_timeout = quit_timeout, build_timeout
        self.settle, self.kill_grace, self.poll = settle, kill_grace, poll
        self.lock = threading.Lock()
        self.closing = threading.Event()
        self.launches: dict[str, Launch] = {}
        self.busy = False
        self.step_process: subprocess.Popen | None = None   # a quit or build under way
        self.live: Launch | None = None                     # whose run is ours
        # (command, cwd, fingerprint) of the last build that succeeded. Only the
        # worker touches it, and there is one worker at a time.
        self.last_build: tuple | None = None

    def start(self, setup_id: str, launch: dict) -> str | None:
        """Start a launch; None if one is under way (or the server is stopping)."""
        with self.lock:
            if self.busy or self.closing.is_set():
                return None
            self.busy = True
            start = size_of(self.log)
            for earlier in self.launches.values():
                if earlier.end is None:
                    earlier.end = start
            first = BUILDING if launch["build"] else QUITTING
            run = self.launches[setup_id] = Launch(setup_id, start, launch["url"], first)
            phase = run.phase
        threading.Thread(target=self.work, args=(run, launch), daemon=True).start()
        return phase

    def status_of(self, setup_id: str) -> dict:
        with self.lock:
            run = self.launches.get(setup_id)
            if run is None:
                return {"phase": IDLE, "message": "", "tail": [], "url": None}
            # A server that was up may have gone down since — say so.
            if run.phase == RUNNING and run.process.poll() is not None:
                code = run.process.returncode
                run.phase, run.message = (DONE if code == 0 else FAILED), f"run exited with {code}"
            status = {"phase": run.phase, "message": run.message, "url": run.url}
            stretch = self.stretch(run)
        return {**status, "tail": tail(*stretch)}

    def stretch(self, run: Launch) -> tuple:
        """Where a launch's tail is: the run's output once there is a run —
        that is what a run that failed or a url that never answered is about —
        and before that the build's and quit's."""
        if run.run_start is not None:
            return self.run_log, run.run_start, run.run_end
        return self.log, run.start, run.end

    def shutdown(self) -> bool:
        """End whatever is ours: a step under way, the live run. True if
        something was running and has been stopped."""
        self.closing.set()
        with self.lock:
            step = self.step_process
        stopped_step = step is not None and end_group(step, self.kill_grace)
        return self.stop_live("stopped with the server") or stopped_step

    def live_alive(self) -> bool:
        """Whether the run an earlier ▶ started is still up."""
        with self.lock:
            return self.live is not None and self.live.process.poll() is None

    def stop_live(self, why: str) -> bool:
        with self.lock:
            run, self.live = self.live, None
            # Said before the signal, or a status read mid-kill would report the
            # SIGTERM as a crash. A run whose url never answered stays `failed`.
            if run is not None and run.phase == RUNNING and run.process.poll() is None:
                run.phase, run.message = IDLE, why
        return run is not None and end_group(run.process, self.kill_grace)

    def set(self, run: Launch, phase: str, message: str = "") -> None:
        with self.lock:
            run.phase, run.message = phase, message

    def header(self, run: Launch, step: str, command: str,
               path: pathlib.Path | None = None) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (path or self.log).open("a", encoding="utf-8") as log:
            log.write(f"--- {stamp} {run.setup_id} {step}: {command}\n")

    def spawn(self, run: Launch, step: str, command: str, cwd: str) -> subprocess.Popen:
        """A step's process, in a group of its own, writing into the log. Under
        the lock, so a shutdown either sees it or keeps it from starting."""
        with self.lock:
            if self.closing.is_set():
                raise Stopping("the server is stopping")
            path = self.run_log if step == "run" else self.log
            if step == "run":
                run.run_start = size_of(self.run_log)
                for earlier in self.launches.values():
                    if earlier is not run and earlier.run_start is not None \
                            and earlier.run_end is None:
                        earlier.run_end = run.run_start
            self.header(run, step, command, path)
            with path.open("ab") as log:
                process = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd,
                                           stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
            if step == "run":
                run.process, self.live = process, run
            else:
                self.step_process = process
        return process

    def step(self, run: Launch, step: str, command: str, cwd: str, timeout: float) -> str | None:
        """Run a quit or build to its end; None if it exited 0, else what happened."""
        process = self.spawn(run, step, command, cwd)
        try:
            code = process.wait(timeout)
        except subprocess.TimeoutExpired:
            end_group(process, self.kill_grace)
            return f"{step} took longer than {timeout:g} s"
        finally:
            with self.lock:
                self.step_process = None
        return None if code == 0 else f"{step} exited with {code}"

    def work(self, run: Launch, launch: dict) -> None:
        try:
            self.steps(run, launch)
        except (OSError, Stopping) as error:     # a cwd that is gone, a full disk
            self.set(run, FAILED, str(error))
        except Exception as error:
            # Anything else is a bug — but a worker that dies of it leaves the
            # phase where it was, and the page polls `waiting` forever. Failed,
            # with the traceback in the log, is something a human can act on.
            self.set(run, FAILED, f"{type(error).__name__}: {error}")
            # Into the log the tail reads from, so the page shows it.
            with contextlib.suppress(OSError):
                with self.stretch(run)[0].open("a", encoding="utf-8") as log:
                    log.write(traceback.format_exc())
        finally:
            with self.lock:
                self.busy = False

    def steps(self, run: Launch, launch: dict) -> None:
        """Build first, and only then end what is running: a build that fails
        leaves the app or server it would have replaced alive. Building beside
        a running app is fine; replacing it is what has to wait."""
        cwd = launch["cwd"]
        if launch["build"] and not self.built(run, launch["build"], cwd):
            return
        self.set(run, QUITTING)
        self.stop_live("stopped for the next launch")
        if launch["quit"]:
            failure = self.step(run, "quit", launch["quit"], cwd, self.quit_timeout)
            # The app may simply not have been running — no reason to stop here.
            if failure:
                self.set(run, QUITTING, f"{failure} — carrying on")
        self.set(run, STARTING)
        process = self.spawn(run, "run", launch["run"], cwd)
        try:
            code = process.wait(self.settle)
        except subprocess.TimeoutExpired:
            code = None
        if code is not None:
            self.set(run, DONE if code == 0 else FAILED, "" if code == 0 else f"run exited with {code}")
            return
        if not run.url:
            self.set(run, RUNNING)
            return
        self.open_when_ready(run, process, launch["ready_timeout"])

    def built(self, run: Launch, command: str, cwd: str) -> bool:
        """Run the build unless the last one was this very build and the
        sources have not moved since; False — with the launch marked failed —
        if it did not succeed."""
        if self.up_to_date(command, cwd):
            self.header(run, "build", "skipped, nothing changed since the last build")
            return True
        self.last_build = None
        failure = self.step(run, "build", command, cwd, self.build_timeout)
        if not failure:
            # Taken after the build, so output it leaves unignored counts as
            # part of what it built.
            self.last_build = (command, cwd, source_fingerprint(cwd, self.root))
            return True
        # Said, because the old instance may be this very setup's: its
        # `running` has just given way to this launch's status.
        if self.live_alive():
            failure += " — the running instance was left as it was"
        self.set(run, FAILED, failure)
        return False

    def up_to_date(self, command: str, cwd: str) -> bool:
        """Whether the last build was `command` in `cwd` and git sees nothing
        that changed since. Another build in between may have written the same
        output, so it has to be the very last one."""
        if self.last_build is None or self.last_build[:2] != (command, cwd):
            return False
        fingerprint = self.last_build[2]
        return fingerprint is not None and fingerprint == source_fingerprint(cwd, self.root)

    def open_when_ready(self, run: Launch, process: subprocess.Popen, timeout: float) -> None:
        """Wait for the run's url to answer, then open it in the browser."""
        self.set(run, WAITING)
        failure = self.wait_ready(run.url, process, timeout)
        if failure:
            # A run still alive after its url never answered is ours, and left
            # alone it would serve on with the page saying `failed` — holding
            # its port against the next ▶. A server going down ends it anyway.
            if not self.closing.is_set() and self.stop_live("its url never answered"):
                failure += " — stopped it"
            self.set(run, FAILED, failure)
            return
        self.header(run, "open", run.url)
        self.opener(run.url)
        self.set(run, RUNNING)

    def wait_ready(self, url: str, process: subprocess.Popen, timeout: float) -> str | None:
        """None once `url` answers, else why it never will."""
        deadline = time.monotonic() + timeout
        while not self.probe(url):
            code = process.poll()
            if code is not None:
                return f"run exited with {code} before {url} answered"
            if time.monotonic() >= deadline:
                return f"{url} did not answer within {timeout:g} s"
            if self.closing.wait(self.poll):
                return "the server is stopping"
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    root: pathlib.Path
    restart: str        # command the page can offer for restarting the server
    pulse: Pulse
    launcher: Launcher

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

    def send(self, code: int, body: bytes, ctype: str, page: bool = False) -> None:
        """One response. Everything but the page itself goes out sandboxed: a
        test file under data/ or an SVG under images/ is foreign content, and
        script in it would otherwise run as this origin — past the Host check
        and the X-Checklist header alike, straight to POST /launch. A sandbox
        without allow-same-origin still renders an HTML or PDF preview in its
        tab; it just gives it an origin of its own."""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # A .txt that looks like HTML stays text.
        self.send_header("X-Content-Type-Options", "nosniff")
        if not page:
            self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, code: int, why: str) -> None:
        self.send(code, why.encode("utf-8"), "text/plain; charset=utf-8")

    def send_json(self, code: int, document) -> None:
        self.send(code, json.dumps(document, ensure_ascii=False).encode("utf-8"),
                  "application/json; charset=utf-8")

    def query_value(self, name: str, default=None):
        """The first value of one query parameter, or `default`."""
        query = urllib.parse.parse_qs(self.path.partition("?")[2])
        return (query.get(name) or [default])[0]

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
        # A setup's files too, managed or not: the spec's author named them.
        return {entry.get("path") for setup in declared
                for entry in [*(setup.get("paths") or []), *(setup.get("files") or [])]}

    def body_length(self, limit: int) -> int | None:
        """The declared body size, or None once the client has been told why not.
        Above 0 and at most `limit`: a negative length would have the read wait
        for a hang-up, and an unbounded one would read whatever is sent."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.fail(400, "Content-Length is not a number")
            return None
        if length <= 0:
            self.fail(400, "body missing")
            return None
        if length > limit:
            self.fail(413, "body too large")
            return None
        return length

    def do_POST(self) -> None:
        route = self.path.split("?", 1)[0].lstrip("/")
        actions = {"reveal": self.reveal, "reset": self.reset, "launch": self.launch}
        if route not in actions:
            self.fail(404, "POST /reveal, /reset or /launch only")
            return
        # A header of our own: a foreign page would first have to send a preflight,
        # and there is none here. Sec-Fetch-Site rules out the rest. Its value is
        # the route, so a header sent along for one action does not pass for another.
        if self.headers.get("X-Checklist") != route:
            self.fail(403, "missing header")
            return
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            self.fail(403, "foreign origin")
            return
        body = self.post_body(route)
        if body is None:
            return
        actions[route](body)

    def post_body(self, route: str) -> dict | None:
        """The POST body as an object carrying the route's field as text, or
        None once the client has been told why not."""
        length = self.body_length(MAX_POST_BODY)
        if length is None:
            return None
        try:
            body = json.loads(self.rfile.read(length))
        # ValueError covers bytes that are no text at all as well as bad JSON;
        # RecursionError is JSON nested deeper than the parser goes.
        except (ValueError, RecursionError):
            self.fail(400, "not valid JSON")
            return None
        if not isinstance(body, dict):
            self.fail(400, "expected a JSON object")
            return None
        # Checked here, so no action ever meets a list where it expects a name —
        # that ended in an exception and a dropped connection, not an answer.
        field = POST_FIELDS[route]
        if not isinstance(body.get(field), str):
            self.fail(400, f"{field} must be text")
            return None
        return body

    def frozen_setup(self, setup_id) -> dict | None:
        """A setup from setups.json, or None once the client has been told why
        not. Only frozen ones: the state is the page's to write."""
        setups = read_setups(self.root)
        if setups is None:
            self.fail(404, "this folder was built before setups were frozen — rebuild it")
            return None
        if not isinstance(setup_id, str) or setup_id not in setups:
            self.fail(404, f"no setup {setup_id!r} in this checklist")
            return None
        return setups[setup_id]

    def reset(self, body: dict) -> None:
        """Put a setup's managed files back the way the build wrote them.

        All pristine copies are checked before the first one is copied, so a
        reset either happens or leaves the files alone — never half of it.
        """
        setup = self.frozen_setup(body.get("setup"))
        if setup is None:
            return
        pairs = []
        for entry in setup.get("files") or []:
            if not entry.get("managed"):
                continue
            target = file_location(self.root, entry)
            source = contained(self.root / PRISTINE_DIR, entry["name"])
            if target is None or source is None:
                self.fail(409, f"{entry['name']} is a symlink or leads out of "
                               f"{DATA_DIR}/ or {PRISTINE_DIR}/ — refusing to reset it; "
                               f"remove the link or rebuild the folder")
                return
            if not source.is_file():
                self.fail(409, f"no pristine copy of {entry['name']} — rebuild the folder")
                return
            pairs.append((entry["name"], source, target))
        for _, source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_file(target, source.read_bytes())
        self.send_json(200, {"reset": [name for name, _, _ in pairs]})

    def launchable(self, setup_id) -> dict | None:
        """A frozen setup's launch, or None once the client has been told why not."""
        setup = self.frozen_setup(setup_id)
        if setup is None:
            return None
        if not setup.get("launch"):
            self.fail(404, f"setup {setup_id!r} has nothing to start")
            return None
        return setup["launch"]

    def launch(self, body: dict) -> None:
        """POST /launch {"setup": id} — 202 and the steps run in the background;
        409 while an earlier launch is still under way."""
        launch = self.launchable(body.get("setup"))
        if launch is None:
            return
        phase = self.launcher.start(body["setup"], launch)
        if phase is None:
            self.fail(409, "a launch is already under way — wait for it to finish")
            return
        self.send_json(202, {"phase": phase})

    def launch_status(self) -> None:
        """GET /launch?setup=<id> — phase, message, the last log lines of this
        launch, and its url; `idle` for a setup not launched by this server."""
        setup_id = self.query_value("setup")
        if self.launchable(setup_id) is None:
            return
        self.send_json(200, self.launcher.status_of(setup_id))

    def reveal(self, body: dict) -> None:
        wanted = body.get("path", "")
        if wanted not in self.declared_paths():
            self.fail(403, "that path is not in the checklist")
            return
        target = pathlib.Path(wanted)
        if not target.exists():
            self.fail(404, "does not exist yet — create it first")
            return
        subprocess.run(reveal_command(target), check=False)
        self.send_json(200, {})

    @staticmethod
    def under(base: pathlib.Path, rest: str) -> pathlib.Path | None:
        """The file `rest` names inside `base`, or None. Test data may sit in
        subfolders; `..`, absolute paths and symlinks that point out do not.

        Looser than `workfolder.contained` on purpose: this only reads, so a
        link that stays inside `base` is harmless, and it serves images/ and
        the old German folders too, which the build never writes."""
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
            self.send(200, page.encode("utf-8"), "text/html; charset=utf-8", page=True)
            return
        if path == "/peek":
            self.peek()
            return
        if path == "/launch":
            self.launch_status()
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

    def peek(self) -> None:
        """GET /peek?setup=<id>&file=<index> — one of a frozen setup's files,
        by position, so the page names nothing it was not handed."""
        setup = self.frozen_setup(self.query_value("setup"))
        if setup is None:
            return
        files = setup.get("files") or []
        index = self.query_value("file", "")
        if not (index.isascii() and index.isdigit()) or int(index) >= len(files):
            self.fail(404, f"this setup has no file {index!r}")
            return
        entry = files[int(index)]
        target = file_location(self.root, entry)
        if target is None:
            self.fail(409, f"{entry['name']} is a symlink or leads out of {DATA_DIR}/ — "
                           f"not previewing it")
            return
        if not target.is_file():
            self.fail(404, f"{entry['path']} does not exist"
                           + (" — reset the files" if entry.get("managed") else ""))
            return
        self.send_json(200, peek(entry["name"], target.read_bytes(), entry.get("encoding")))

    def do_PUT(self) -> None:
        if self.path.split("?", 1)[0] != "/state":
            self.fail(404, "PUT /state only")
            return
        incoming = self.incoming_state()
        if incoming is None:
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
        self.send_json(200, inject(state, setups))

    def incoming_state(self) -> dict | None:
        """The state a PUT brought, or None once the client has been told why
        not."""
        length = self.body_length(MAX_BODY)
        if length is None:
            return None
        try:
            incoming = json.loads(self.rfile.read(length))
        except (ValueError, RecursionError) as error:     # see post_body
            self.fail(400, f"not valid JSON: {error}")
            return None
        problem = state_problem(incoming)
        if problem:
            self.fail(400, problem)
            return None
        return incoming


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def handler_for(root: pathlib.Path, pulse: Pulse, restart: str = "",
                launcher: Launcher | None = None) -> type[Handler]:
    """A Handler bound to one folder.

    A subclass rather than attributes set on Handler itself, so two servers in one
    process — the tests start one per case — never share a folder or a launch.
    """
    return type("FolderHandler", (Handler,), {"root": root, "pulse": pulse, "restart": restart,
                                              "launcher": launcher or Launcher(root)})


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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the browser right away")
    parser.add_argument("--idle", type=int, default=15, metavar="MIN",
                        help="stop after this long without a request (0 = never)")
    parser.add_argument("--read-idle", type=int, default=30, metavar="MIN",
                        help="stop this long after read.py took the results (0 = never)")
    return parser.parse_args()


def install_stop_signals() -> None:
    """Turn the signals that should end the server into a clean stop."""
    # Long runs get killed from outside (observed: exit code 144 after several
    # hours, with a clean goodbye in the log — the trigger sits outside this
    # process). Nothing is lost: every save writes through, and the page holds
    # on to its state. Ignoring SIGURG is cheap insurance, SIGTERM is answered.
    signal.signal(signal.SIGURG, signal.SIG_IGN)
    # shutdown() from inside the handler deadlocks with serve_forever on the
    # same thread — the exception arrives cleanly in serve_until_stopped instead.
    def stop(*_):
        raise KeyboardInterrupt
    # SIGHUP too: a terminal that closes would otherwise kill the server
    # outright, and whatever ▶ started would live on with nobody to stop it.
    for signum in STOP_SIGNALS:
        signal.signal(signum, stop)


def serve_until_stopped(server: Server, launcher: Launcher, watch_stop: threading.Event) -> bool:
    """Serve until a stop, then take ▶'s run down too. True if there was one
    and it has been stopped."""
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watch_stop.set()
        # Already on the way down: a second Ctrl-C or SIGTERM would raise
        # in the middle of the cleanup below and leave the run alive.
        for signum in (*STOP_SIGNALS, signal.SIGINT):
            signal.signal(signum, signal.SIG_IGN)
        # Whatever ▶ started goes down with the server, the idle stop
        # included — even a dev server someone still uses in another tab.
        # Left running, it would hold its port with nobody to stop it.
        ended = launcher.shutdown()
    return ended


def main() -> None:
    args = parse_arguments()

    root = pathlib.Path(args.folder).resolve()
    for needed in ("template.html", "state.json"):
        if not (root / needed).is_file():
            sys.exit(f"{root}/{needed} is missing — build it first: build.py spec.json {args.folder}")

    pulse = Pulse()
    launcher = Launcher(root)
    handler = handler_for(root, pulse, launcher=launcher)
    server, port = listen(args.port, PORT_TRIES, handler)
    handler.restart = shlex.join(
        ["python3", str(pathlib.Path(__file__).resolve()), str(root), "--port", str(port)])
    url = f"http://127.0.0.1:{port}/"
    with server:
        install_stop_signals()
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
        ended = serve_until_stopped(server, launcher, stop)
        print("stopped — the state is in state.json"
              + (f"; the process ▶ started was stopped too (log: {launcher.run_log})" if ended else ""),
              flush=True)


if __name__ == "__main__":
    main()

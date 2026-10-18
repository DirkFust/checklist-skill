#!/usr/bin/env python3
"""Turns a checklist spec into a working folder for serve.py.

    build.py spec.json folder [--shell shell.html] [--lang en] [--merge | --force]
    build.py --clean folder

The folder gets `template.html`, `state.json` and `setups.json`, and with a
`data` in the spec the test files under `data/` plus their untouched twins under
`.pristine/` — what the page's reset copies back. `%%STATE%%` stays open in the
template — `serve.py` fills the state in on every request and takes it back via
PUT.

Every build rewrites the test data, `--merge` included: what a round of testing
did to it is not a result. Files an earlier build wrote that the spec no longer
names go; anything else in `data/` (a script's output) stays. A file the build
would write that is a symlink, or lies in a folder resolving elsewhere, aborts:
the write would land on whatever the link points at. So does anything in the
way (a folder where a file goes, or the reverse), checked before the first
write, and two data names that differ only in case — one file on the Mac's
disk. The data goes before template, state and setups, so a build that fails
on the disk leaves those as they were. `--clean` removes
`data/` and `.pristine/` whole and touches nothing else — state, images,
setups and template stay.

A folder a human has saved into (`meta.folder` in its state) holds results, and
a rebuild would wipe them — so that aborts. `--merge` hands each new item the
state, note, images and fold of the old item with the identical title and names
the old items nothing claimed; `--force` overwrites.

Setups are frozen here, not carried in the state. The state is what the page
writes back, and a setup names paths the server will reveal and commands it
will run — so the page must not be able to change them. `state.json` items carry
only a setup id (`item-NN` for an inline setup, 1-based, shared by later ones
written exactly alike; the name for a named one); `setups.json` holds the
resolved setup per id under `setups`, and under `source` what the spec gave —
`inline` setups by id, `setups`, `launch`, `launches` and `data` as written —
so `read.py --as-spec` can hand it back to this script unchanged. A setup's
`files` are frozen resolved as well: name, absolute path, whether the build
manages it, and a declared encoding.

Project config: `checklist.json`, the first one found walking up from the
folder's parent; the walk stops at a directory with `.git` (inclusive) or at
$HOME. Outside $HOME only a repository bounds it — without one there is no
walk at all. A config that is not the user's own, or that group or others may
write to, is skipped with a warning: it names commands ▶ runs. It may hold
`lang`, `launch` and `launches`, nothing else — what every checklist of a
project shares, written down once.

    {"lang": "de", "launch": {"build": "make app", "run": "open -a App {files}"},
     "launches": {"editor": {"run": "open -a Editor {files}"}}}

Language: `--lang` beats the spec's `lang`, that beats — on `--merge` — the
old state's, that beats the project's, then `en`. It lands in `meta.lang`, so
`--as-spec` writes it back into the spec and `--merge` keeps it.

Spec format:

    {
      "title":    "Short name",           // browser tab
      "eyebrow":  "Context",
      "heading":  "Headline",
      "standfirst": "A sentence or two.",
      "lang":     "en",                   // chrome language, see above
      "autofold": true,                   // a ticked item folds itself away
                                          //   after a moment; false keeps every
                                          //   item where the user put it
      "setups": {                         // optional: written once, used by name
        "app": {"label": "App", "commands": ["make app"],
                "launch": {"run": "open -a App"}}
      },                                  //   name: ^[a-z0-9][a-z0-9_-]*$, not item-NN;
                                          //   one no item uses only warns
      "launch": {                         // optional, for every setup; field by
        "cwd":   "~/dev/x",               //   field over the project's, under the
        "quit":  "pkill App",             //   setup's own. null or "" switches a
        "build": "make app",              //   step off. Default cwd: the project
        "run":   "open -a App {files}",   //   root (dir of checklist.json, else
        "url":   "http://localhost:3000", //   the folder's parent). Without a
        "ready_timeout": 60               //   `run` a setup has nothing to start.
      },                                  //   Placeholders: {data} = <folder>/data
                                          //   (quoted in commands, bare in url),
                                          //   {files} = the setup's files, quoted
                                          //   (commands only). Any other bare
                                          //   {word} aborts; `${VAR}`, `{}`,
                                          //   `{print $1}` are left to the shell.
      "launches": {                       // optional: named profiles for a project
        "preview": {"build": "npm run build",  // that starts in more than one
                    "run": "npm run preview"}  // way. Same fields as `launch`;
      },                                  //   per name, field by field over the
                                          //   project's. A setup chooses one;
                                          //   one no setup chooses only warns.
      "data": {                           // optional: test files, written to
        "notes.txt": "verbatim UTF-8\\n", //   <folder>/data/<name>. Name: /-separated
        "case-1/win.txt": {               //   segments of letters, digits, . _ -
          "text": "a\\nb\\n",               //   every line end becomes the eol (lf |
                                          //   crlf | cr); without eol: verbatim;
          "encoding": "utf-16-le",        //   any Python text codec, default utf-8;
          "eol": "crlf", "bom": true},    //   bom: utf-8 / utf-16-le|be / utf-32-le|be
                                          //   (bare utf-16/utf-32 abort: byte order)
        "real.pdf": {"copy": "~/x/real.pdf"},     // copied at build time
        "tiny.png": {"base64": "iVBORw0KGgo…"}    // raw bytes
      },

      "items": [
        {"title": "What to do",
         "detail": "Steps and expectation.\\nSecond paragraph.",
         // title/detail/standfirst/setup.text render a small markdown set:
         // **bold**, `code`, and lines starting with -/*/· become bullets.
         "state": "open",                 // open | ok | problem | skipped, optional
         "collapsed": false,              // true starts the item folded — marker,
                                          //   title and number only
         "note": "",                      // pre-filled note — singular, on the item;
                                          //   `notes` exists only above, on the document
         "images": [],                    // data:image/… URIs, or "images/<name>"
                                          //   paths carried over from a save
         "setup": {                       // optional, sits above the item; with
                                          //   `setups` in the spec a string is a
                                          //   name from there, without it text
           "label": "Setup",              //   heading, short and upper-cased
           "text": "What has to be running.\\nSecond line.",
           "commands": ["make run-gui"],  //   one copy button per line
           "links": [{"label": "Preview", "url": "http://localhost:3000"}],
           "paths": [{"label": "Test data",           //   button: reveal in Finder
                      "path": "~/dev/x/.manual-tests/case-1"}],
           "collapsed": false,           //   true starts folded
           "files": ["case-1/win.txt",   //   a name from data: managed — shown,
                     "~/dev/x/in.csv"],  //   previewed, reset; an absolute or ~
                                          //   path: shown and previewed, never
                                          //   written. Anything else aborts.
                                          //   {files} in launch = all of them.
           "launch": {"run": "make dev"} //   over the spec's launch, see above;
                                          //   or "preview" (a profile), or
                                          //   {"profile": "preview", …overrides}.
                                          //   Order: project launch < spec
                                          //   launch < profile < these fields
         }}
      ],
      "notes": "Pre-fill for the observations field"  // optional, also as
                                                      // {"text": …, "images": […]}
    }

The page speaks one language, chosen at build time (see Language). The chrome
lives in `strings/<lang>.json`, the shell carries `{{t.key}}` where the text goes.
Shell and table have to agree exactly — an unknown key in the shell and a key
nobody uses both abort the build, so a translation cannot drift away from the
markup unnoticed.

Images are normally dropped into the page by a human and `serve.py` turns them
into files. In the spec they only serve to pre-fill a list.

Unknown keys abort the build. That is deliberate: a silently discarded field
looks right in the spec and is missing from the page all the same.
"""

import argparse
import base64
import codecs
import dataclasses
import html
import json
import os
import pathlib
import re
import shlex
import shutil
import stat
import sys
import urllib.parse

from workfolder import (
    BOMS,
    DATA_DIR,
    DEFAULT_ENCODING,
    PRISTINE_DIR,
    READ_MARK,
    SEGMENT_RE,
    SETUPS_FILE,
    contained,
    replace_file,
)

STATES = {"open", "ok", "problem", "skipped"}
PROJECT_CONFIG = "checklist.json"
EOLS = {"lf": "\n", "crlf": "\r\n", "cr": "\r"}
LINE_END_RE = re.compile(r"\r\n|\r")
# Not in BOMS: their byte order is whatever this machine has, and Python writes
# a BOM for them even unasked.
AMBIGUOUS_ENCODINGS = {"utf-16", "utf-32"}
DATA_TEXT_KEYS = {"text", "encoding", "eol", "bom"}
DEFAULT_LANG = "en"
# It names a file under strings/, so nothing that could climb out of there.
LANG_RE = re.compile(r"^[a-z]{2,3}([_-][A-Za-z0-9]+)?$")
# Two digits keep the ids sorting in item order for any list a human ticks off.
INLINE_SETUP_ID = "item-{:02d}"
# Named setups share the id space with inline ones, so a name must never be able
# to pass for an inline id.
SETUP_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
INLINE_ID_RE = re.compile(r"^item-\d+$")

LAUNCH_COMMANDS = ("quit", "build", "run")
DEFAULT_READY_TIMEOUT = 60
# Only `{word}` is a placeholder, and not after a `$`: commands are shell, and
# `${VAR}`, `awk '{print $1}'`, `-exec … {} \;` or `a.{txt,bak}` must pass as
# they are. A `{word}` nobody knows aborts — a typo like `{file}` would otherwise
# reach the shell verbatim.
PLACEHOLDER_RE = re.compile(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")

# A folder a human has saved into holds results; a plain rebuild would wipe them.
REBUILD_REFUSE, REBUILD_MERGE, REBUILD_FORCE = "refuse", "merge", "force"
# What a human put into an item — carried over by --merge, matched by title.
MERGED_FIELDS = ("state", "note", "images", "collapsed")

# A key nobody reads is worse than an error: the spec looks complete and the
# content is missing anyway. So what is allowed is written down here, and
# anything else aborts the build.
SPEC_KEYS = {"title", "eyebrow", "heading", "standfirst", "items", "notes", "autofold",
             "lang", "setups", "launch", "launches", "data"}
ITEM_KEYS = {"title", "detail", "setup", "state", "collapsed", "note", "images"}
SETUP_KEYS = {"label", "text", "commands", "links", "paths", "collapsed", "launch", "files"}
LAUNCH_KEYS = {"cwd", *LAUNCH_COMMANDS, "url", "ready_timeout"}
# Only a setup chooses a profile; a profile or a default choosing one would chain.
PROFILE_KEY = "profile"
PROJECT_KEYS = {"launch", "launches", "lang"}
# The typos that actually happen — the singular/plural pairs above all.
HINTS = {"notes": "note", "comment": "note", "remark": "note",
         "status": "state", "image": "images", "description": "detail",
         "body": "detail", "steps": "items", "command": "commands",
         "link": "links", "path": "paths", "prerequisites": "setup",
         "file": "files"}


def only(raw: dict, allowed: set, where: str) -> None:
    """Reject unknown keys instead of dropping them without a word."""
    for key in raw:
        if key in allowed:
            continue
        hint = f" — did you mean {HINTS[key]!r}?" if HINTS.get(key) in allowed else ""
        sys.exit(f"{where}: unknown key {key!r}{hint}\n"
                 f"  allowed: {', '.join(sorted(allowed))}")

# `shell.html` is only the body; the wrapper lives here so the server serves a
# complete document.
HEAD_OPEN = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
)
HEAD_CLOSE = "</head><body>\n"
DOC_CLOSE = "\n</body></html>"


def fonts(path: pathlib.Path) -> str:
    """The webfaces, inlined — the page must not fetch anything over the network.

    A `<link>` to a font CDN would tell that CDN, on every single page load, that
    someone is looking at a checklist here; it would also leave the page unstyled
    on a machine that is offline. Missing file: the fallback stacks in the shell
    do the job, so this warns instead of aborting.
    """
    if not path.is_file():
        print(f"warning: {path} is missing — the page falls back to system fonts",
              file=sys.stderr)
        return ""
    return "<style>\n" + path.read_text(encoding="utf-8") + "</style>"


def strings(path: pathlib.Path) -> dict:
    """The chrome in one language. `_` is the note to whoever edits the file.

    The values go into the markup and into JS string literals alike, so they stay
    plain text: a quote would break the literal, and a `<` could close the script
    early and turn the rest of the page into visible body text.
    """
    if not path.is_file():
        sys.exit(f"no string table at {path} — available: "
                 f"{', '.join(sorted(f.stem for f in path.parent.glob('*.json')))}")
    table = json.loads(path.read_text(encoding="utf-8"))
    table.pop("_", None)
    for key, value in table.items():
        if not isinstance(value, str):
            sys.exit(f"{path.name}: {key!r} must be text")
        bad = [c for c in '"<\\' if c in value]
        if bad:
            sys.exit(f"{path.name}: {key!r} contains {' '.join(bad)} — "
                     f"the chrome must be plain text")
    return table


def translate(shell: str, table: dict, where: str) -> str:
    """Fill `{{t.key}}` in from the table, and insist the two match exactly.

    Both directions matter. A key the table has lost leaves a raw `{{t.…}}` on
    the page; a key nobody uses any more is a line of translation that quietly
    stopped being shown.
    """
    used = set(re.findall(r"\{\{t\.([a-z0-9_]+)\}\}", shell))
    missing = used - set(table)
    if missing:
        sys.exit(f"{where}: no text for {', '.join(sorted(missing))}")
    unused = set(table) - used
    if unused:
        sys.exit(f"{where}: nothing in the shell uses {', '.join(sorted(unused))}")
    return re.sub(r"\{\{t\.([a-z0-9_]+)\}\}", lambda m: table[m.group(1)], shell)


def lang_of(value, where: str) -> str:
    if not isinstance(value, str) or not LANG_RE.match(value):
        sys.exit(f"{where}: lang must be a language code like 'en', not {value!r}")
    return value


def choose_lang(cli: str | None, spec: dict, project_lang: str | None,
                previous: str | None = None) -> str:
    """`--lang` beats the spec, the spec beats the language a `--merge` finds in
    the old state, that beats the project, `en` is the floor.

    The spec's own `lang` is what makes a rebuild keep its language — `read.py
    --as-spec` writes it back — so a forgotten `--lang` no longer flips the page.
    The old state's covers a `--merge` from the original spec of a page first
    built with `--lang` alone.
    """
    spec_lang = lang_of(spec["lang"], "spec") if "lang" in spec else None
    return cli or spec_lang or previous or project_lang or DEFAULT_LANG


def previous_lang(folder: pathlib.Path, rebuild: str) -> str | None:
    """The language a `--merge` carries over: the saved state's `meta.lang`.
    Nothing for any other rebuild — that starts the page afresh."""
    if rebuild != REBUILD_MERGE:
        return None
    lang = ((saved_state(folder) or {}).get("meta") or {}).get("lang")
    return lang_of(lang, str(folder / "state.json")) if lang else None


def launch_layer(raw, where: str) -> dict:
    """One level of launch config, checked but neither merged nor resolved.

    `null` and `""` pass: at a higher level they switch off what a lower one set.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        sys.exit(f"{where} must be an object")
    only(raw, LAUNCH_KEYS, where)
    for key, value in raw.items():
        if value is None or value == "":
            continue
        if key == "ready_timeout":
            # `type` rather than isinstance: `true` is an int to Python.
            if type(value) is not int or value <= 0:
                sys.exit(f"{where}.ready_timeout must be whole seconds above 0, not {value!r}")
        elif not isinstance(value, str):
            sys.exit(f"{where}.{key} must be text, not {value!r}")
    return dict(raw)


def launch_profiles(raw, where: str) -> dict:
    """`launches`: named launch configs, for a project that starts in more than
    one way (the app vs. the editor, the dev server vs. the preview build)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        sys.exit(f"{where} must be an object, profile name -> launch")
    for name in raw:
        if not SETUP_NAME_RE.match(name):
            sys.exit(f"{where}: a profile name is lower-case letters, digits, - and _ "
                     f"(first a letter or digit), not {name!r}")
    return {name: launch_layer(launch, f"{where}.{name}") for name, launch in raw.items()}


def setup_launch(raw, where: str) -> tuple[str | None, dict]:
    """A setup's `launch`: a profile name, overrides, or both (`profile` key)."""
    if isinstance(raw, str):
        return raw, {}
    if not isinstance(raw, dict):
        return None, launch_layer(raw, where)
    profile = raw.get(PROFILE_KEY)
    if profile is not None and not isinstance(profile, str):
        sys.exit(f"{where}.{PROFILE_KEY} must be a profile name, not {profile!r}")
    overrides = {key: value for key, value in raw.items() if key != PROFILE_KEY}
    return profile, launch_layer(overrides, where)


@dataclasses.dataclass(frozen=True)
class Project:
    """What a `checklist.json` above the working folder contributes."""
    root: pathlib.Path          # default working directory for launch steps
    launch: dict
    lang: str | None
    launches: dict = dataclasses.field(default_factory=dict)


def load_project(path: pathlib.Path) -> Project:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as broken:
        sys.exit(f"{path} is not valid JSON: {broken}")
    if not isinstance(raw, dict):
        sys.exit(f"{path} must be an object")
    only(raw, PROJECT_KEYS, str(path))
    lang = lang_of(raw["lang"], str(path)) if "lang" in raw else None
    return Project(path.parent, launch_layer(raw.get("launch"), f"{path}, launch"), lang,
                   launch_profiles(raw.get("launches"), f"{path}, launches"))


def searched_folders(start: pathlib.Path, stop: pathlib.Path) -> list:
    """Where a project config may sit: `start` and its parents up to a
    repository root (a `.git` there, inclusive) or `stop`, whichever comes
    first. Outside `stop` only a repository bounds the walk — without one,
    nothing is searched: a walk on to `/` would take a config out of
    `/private/tmp` or any other folder whoever likes can write to."""
    folders = []
    for here in [start, *start.parents]:
        folders.append(here)
        if (here / ".git").exists() or here == stop:
            return folders
    return []


def trusted(path: pathlib.Path) -> bool:
    """A config may name commands ▶ runs, so it counts only if it is this
    user's own and nobody else can write to it."""
    info = path.stat()
    if info.st_uid == os.getuid() and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return True
    print(f"warning: ignoring {path} — it is not yours or others can write to it",
          file=sys.stderr)
    return False


def find_project(start: pathlib.Path, stop: pathlib.Path) -> Project:
    """The nearest trusted `checklist.json` from `start` upwards, through the
    folders `searched_folders` allows — a config in some unrelated folder
    further up must not reach into this project. Nothing found: no config,
    and `start` is the root.
    """
    for here in searched_folders(start, stop):
        path = here / PROJECT_CONFIG
        if path.is_file() and trusted(path):
            return load_project(path)
    return Project(start, {}, None)


@dataclasses.dataclass(frozen=True)
class DataFile:
    """One entry of the spec's `data`, settled into the bytes it is written as."""
    content: bytes
    encoding: str | None        # the codec's own name if the entry declared one


def data_name(name, where: str) -> str:
    """A path under data/ that cannot climb out of it or hide in it."""
    if not isinstance(name, str) or not all(SEGMENT_RE.match(part)
                                            for part in name.split("/")):
        sys.exit(f"{where}: a data name is /-separated segments of letters, digits, "
                 f". _ - (each starting with a letter or digit), not {name!r}")
    return name


def encoded_text(entry: dict, where: str) -> DataFile:
    """`{"text", "encoding"?, "eol"?, "bom"?}` — the file exactly as a test
    needs it, down to the line ends and the byte order mark."""
    only(entry, DATA_TEXT_KEYS, where)
    text, encoding = entry["text"], entry.get("encoding", DEFAULT_ENCODING)
    eol, bom = entry.get("eol", "lf"), entry.get("bom", False)
    if not isinstance(text, str):
        sys.exit(f"{where}.text must be text")
    if not isinstance(eol, str) or eol not in EOLS:
        sys.exit(f"{where}.eol is one of {', '.join(EOLS)}, not {eol!r}")
    if type(bom) is not bool:
        sys.exit(f"{where}.bom is true or false, not {bom!r}")
    if not isinstance(encoding, str):
        sys.exit(f"{where}.encoding must be a codec name like 'utf-16-le', not {encoding!r}")
    try:
        codec = codecs.lookup(encoding).name
    except LookupError:
        sys.exit(f"{where}: unknown encoding {encoding!r}")
    if codec in AMBIGUOUS_ENCODINGS:
        sys.exit(f"{where}: {encoding!r} leaves the byte order to this machine, and Python "
                 f"writes a BOM for it unasked — say {codec}-le or {codec}-be")
    if bom and codec not in BOMS:
        sys.exit(f"{where}: bom goes with {', '.join(BOMS)} — not {encoding!r}")
    if "eol" in entry:
        # Line ends already in the text count as line ends, not as stray
        # characters — otherwise crlf turns a pasted `\r\n` into `\r\r\n`.
        text = LINE_END_RE.sub("\n", text)
    try:
        content = text.replace("\n", EOLS[eol]).encode(codec)
    except UnicodeEncodeError as error:
        sys.exit(f"{where}: {encoding} cannot hold {error.object[error.start:error.end]!r}")
    except LookupError:                 # a codec, but not for text: base64, rot13, …
        sys.exit(f"{where}: {encoding!r} is no text encoding")
    return DataFile((BOMS[codec] if bom else b"") + content,
                    codec if "encoding" in entry else None)


def copied(entry: dict, where: str) -> DataFile:
    """`{"copy": path}` — read now, so the checklist keeps working when the
    original moves or changes."""
    only(entry, {"copy"}, where)
    source = entry["copy"]
    path = pathlib.Path(source).expanduser() if isinstance(source, str) else None
    if path is None or not path.is_absolute():
        sys.exit(f"{where}.copy needs an absolute or ~ path, not {source!r}")
    if not path.is_file():
        sys.exit(f"{where}: no file to copy at {path}")
    return DataFile(path.read_bytes(), None)


def decoded(entry: dict, where: str) -> DataFile:
    """`{"base64": …}` — bytes no text form can spell."""
    only(entry, {"base64"}, where)
    try:
        return DataFile(base64.b64decode(entry["base64"], validate=True), None)
    except (TypeError, ValueError):     # binascii.Error is a ValueError
        sys.exit(f"{where}.base64 does not decode")


DATA_FORMS = {"text": encoded_text, "copy": copied, "base64": decoded}


def data_entry(entry, where: str) -> DataFile:
    if isinstance(entry, str):
        return DataFile(entry.encode("utf-8"), None)
    forms = [form for form in DATA_FORMS if isinstance(entry, dict) and form in entry]
    if len(forms) != 1:
        sys.exit(f"{where}: an entry is text, or an object with exactly one of "
                 f"{', '.join(DATA_FORMS)}")
    return DATA_FORMS[forms[0]](entry, where)


def data_files(raw) -> dict:
    """The spec's `data` by name, every entry already turned into bytes — so a
    missing copy source or a bad encoding aborts before anything is written."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        sys.exit("spec: data must be an object, name -> entry")
    files = {data_name(name, "data"): data_entry(entry, f"data.{name}")
             for name, entry in raw.items()}
    folders = {"/".join(name.split("/")[:depth])
               for name in files for depth in range(1, name.count("/") + 1)}
    clash = sorted(folders & set(files))
    if clash:
        sys.exit(f"data: {', '.join(clash)} cannot be a file and a folder at once")
    # The Mac's disk ignores case: `A.txt` and `a.txt` are one file there, and
    # the second write would quietly replace the first.
    seen = {}
    for name in sorted(files) + sorted(folders - set(files)):
        other = seen.setdefault(name.casefold(), name)
        if other != name:
            sys.exit(f"data: {other!r} and {name!r} differ only in case — one file on a "
                     f"case-insensitive disk; rename one")
    return files


def remove_built(folder: pathlib.Path, base: pathlib.Path, name: str) -> None:
    """A file an earlier build wrote, and the folders that leaves empty."""
    target = base / name
    target.unlink(missing_ok=True)
    for parent in target.parents:
        if parent == folder or not parent.is_dir() or any(parent.iterdir()):
            return
        parent.rmdir()


def write_data(folder: pathlib.Path, files: dict) -> None:
    """data/ and its pristine twin, as `files` says.

    A file the previous build wrote — it has a pristine copy — that `files` no
    longer names goes from both. Anything else in data/ stays: a script may have
    generated it, and it is not the build's to remove.

    Every target is checked before the first file goes: a folder where a file
    belongs, a file where a folder belongs, a symlink — any of them aborts with
    nothing written, rather than an OSError halfway through.
    """
    folder = folder.resolve()
    data, pristine = folder / DATA_DIR, folder / PRISTINE_DIR
    built = [path.relative_to(pristine).as_posix()
             for path in pristine.rglob("*") if path.is_file()] if pristine.is_dir() else []
    gone = [name for name in built if name not in files]
    leaving = {base / name for name in gone for base in (data, pristine)}
    targets = [(data_target(base, name, leaving), file.content)
               for name, file in files.items() for base in (data, pristine)]
    for name in gone:
        for base in (data, pristine):
            remove_built(folder, base, name)
    for target, content in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        replace_file(target, content)


def data_target(base: pathlib.Path, name: str, leaving: set) -> pathlib.Path:
    """Where `name` goes under `base` (data/ or .pristine/) — aborting when that
    is a symlink, or a folder on the way resolves elsewhere: the write would
    land on whatever the link points at. Aborting too when something else is
    in the way; `leaving` is what an earlier build wrote and this one removes,
    so a file and a folder of the same name may trade places between builds."""
    home = base.parent.resolve() / base.name
    target = contained(base, name)
    if target is None:
        sys.exit(f"{home / name} is a symlink or leads out of {home} — the build will not "
                 f"write through it; remove the link and build again")
    if target.is_dir():
        inside = [path for path in target.rglob("*") if not path.is_dir()]
        if not inside or not all(path in leaving for path in inside):
            sys.exit(f"{target} is a folder, and data names a file there — move the folder "
                     f"away or rename the file in the spec")
    for parent in [target.parent, *target.parent.parents]:
        if parent == home.parent:
            break
        if parent.exists() and not parent.is_dir() and parent not in leaving:
            sys.exit(f"{parent} is a file, and data needs a folder there for {name} — move "
                     f"the file away or rename the entry in the spec")
    return target


def clean(folder: pathlib.Path) -> list:
    """--clean: the test data goes, the results stay. Returns what it removed."""
    removed = []
    for path in (folder / DATA_DIR, folder / PRISTINE_DIR):
        if path.is_symlink():
            path.unlink()               # the link, never what it points to
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            continue
        removed.append(path)
    return removed


@dataclasses.dataclass(frozen=True)
class LaunchBase:
    """What every setup's launch starts from: the default levels below it
    (project, then spec), the profiles it may choose (project and spec already
    merged per name), and the places its defaults and placeholders point to —
    the data dir, and the files the spec's `data` puts there, by name."""
    layers: tuple
    root: pathlib.Path
    data: pathlib.Path
    profiles: dict = dataclasses.field(default_factory=dict)
    files: dict = dataclasses.field(default_factory=dict)


def merged_profiles(project: dict, spec: dict) -> dict:
    """Per profile name, the spec's fields over the project's. `null` and `""`
    stay in, so they can still switch off a step of the defaults below."""
    return {name: {**project.get(name, {}), **spec.get(name, {})}
            for name in {**project, **spec}}


def profile_layer(base: LaunchBase, profile: str | None, where: str) -> dict:
    if profile is None:
        return {}
    if profile not in base.profiles:
        sys.exit(f"{where}: unknown launch profile {profile!r} — "
                 f"known: {', '.join(sorted(base.profiles)) or 'none'}")
    return base.profiles[profile]


def files_argument(files: list) -> str:
    """`{files}` in a command: every path quoted on its own, space-separated."""
    return " ".join(shlex.quote(str(path)) for path in files)


def fill(text: str | None, values: dict, where: str) -> str | None:
    """Substitute the placeholders `values` knows; any other `{word}` aborts."""
    if text is None:
        return None

    def substitute(match: re.Match) -> str:
        name = match.group(1)
        if name not in values:
            allowed = ", ".join(f"{{{key}}}" for key in sorted(values))
            sys.exit(f"{where}: no placeholder {{{name}}} here — allowed: {allowed}\n"
                     f"  shell braces pass when they are not a bare {{word}}, "
                     f"e.g. {{ {name} }}")
        return values[name]
    return PLACEHOLDER_RE.sub(substitute, text)


def check_url(url: str, where: str) -> None:
    """A url ▶ can wait for: http(s), a host, a port that is one. The server
    probes it until it answers — one it cannot even ask would leave the page
    saying `waiting` until the timeout, for a typo. `{data}` right after the
    scheme (`http://{data}`) lands here too: the path makes a url without a
    host."""
    parts = urllib.parse.urlsplit(url)
    try:
        port_ok = parts.port is None or 0 < parts.port
    except ValueError:              # not a number, or above 65535
        port_ok = False
    if parts.scheme not in ("http", "https") or not parts.hostname or not port_ok:
        sys.exit(f"{where}: launch.url needs http(s), a host and a valid port, not {url!r}")


def resolve_launch(base: LaunchBase, profile: str | None, own: dict, files: list,
                   where: str) -> dict | None:
    """The launch a setup freezes: levels merged, placeholders filled, `cwd`
    absolute. None without a `run` — then there is nothing to start.

    Merged field by field — project `launch` < spec `launch` < the chosen
    profile < the setup's own fields — so a setup can change one step and
    inherit the rest; `null` or `""` at a higher level switch a step off.
    """
    merged = {}
    for layer in (*base.layers, profile_layer(base, profile, where), own):
        merged.update(layer)
    merged = {key: value for key, value in merged.items() if value not in (None, "")}

    quoted = {"files": files_argument(files), "data": shlex.quote(str(base.data))}
    launch = {key: fill(merged.get(key), quoted, f"{where}, launch.{key}")
              for key in LAUNCH_COMMANDS}
    # The url is opened, not run, so it takes the bare path — and no file list.
    url = fill(merged.get("url"), {"data": str(base.data)}, f"{where}, launch.url")
    if url is not None:
        check_url(url, where)
    cwd = pathlib.Path(merged.get("cwd") or base.root).expanduser()
    if not cwd.is_absolute():
        sys.exit(f"{where}: launch.cwd needs an absolute path, not {merged['cwd']!r}")
    if launch["run"] is None:
        return None
    return {"cwd": str(cwd), **launch, "url": url,
            "ready_timeout": merged.get("ready_timeout", DEFAULT_READY_TIMEOUT)}


def setup_of(setup, where: str, base: LaunchBase) -> dict | None:
    """A setup's prerequisites: which app, which server, which files.

    Bare text is the short form.
    """
    if not setup:
        return None
    if isinstance(setup, str):
        setup = {"text": setup}
    if not isinstance(setup, dict):
        sys.exit(f"{where}: setup must be text or an object")
    only(setup, SETUP_KEYS, f"{where}, setup")

    commands = setup.get("commands") or []
    if isinstance(commands, str):
        commands = [commands]
    if not all(isinstance(command, str) for command in commands):
        sys.exit(f"{where}: setup.commands must be a list of commands")

    links = setup_links(setup.get("links"), where)
    paths = setup_paths(setup.get("paths"), where)
    files = setup_files(setup.get("files"), base, where)
    profile, own_launch = setup_launch(setup.get("launch"), f"{where}, setup.launch")
    return {
        "label": setup.get("label", ""),
        "collapsed": bool(setup.get("collapsed", False)),
        "text": setup.get("text", ""),
        "commands": list(commands),
        "links": links,
        "paths": paths,
        "files": files,
        "launch": resolve_launch(base, profile, own_launch,
                                 [file["path"] for file in files], where),
    }


def setup_links(raw, where: str) -> list:
    """A setup's `links`. They have to be `http(s)` — the page turns them into
    a button, and a `javascript:` target has no business there."""
    links = raw or []
    for link in links:
        if not isinstance(link, dict) or not str(link.get("url", "")).startswith(("http://", "https://")):
            sys.exit(f"{where}: setup.links needs objects with an http(s) url")
    return [{"label": link.get("label", ""), "url": link["url"]} for link in links]


def setup_paths(raw, where: str) -> list:
    """A setup's `paths`, each a button that reveals it in the Finder.

    Absolute, so the button hits regardless of the working directory; the short
    `~` form is what gets shown, so the line stays readable.
    """
    paths = []
    for entry in raw or []:
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict) or not entry.get("path"):
            sys.exit(f"{where}: setup.paths needs objects with a path")
        full = pathlib.Path(str(entry["path"])).expanduser()
        if not full.is_absolute():
            sys.exit(f"{where}: setup.paths needs absolute paths, not {entry['path']!r}")
        home = pathlib.Path.home()
        show = f"~/{full.relative_to(home)}" if full.is_relative_to(home) else str(full)
        paths.append({"label": entry.get("label", ""), "path": str(full), "show": show})
    return paths


def setup_files(raw, base: LaunchBase, where: str) -> list:
    """A setup's `files`: a name from `data` is a managed file under data/ —
    written by the build, put back by a reset. An absolute or `~` path is
    somebody else's file: shown and revealed, never written. Anything else is
    most likely a data name with a typo, so it aborts.
    """
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(name, str) and name for name in raw):
        sys.exit(f"{where}: setup.files must be a list of names from data or absolute paths")
    files = []
    for name in raw:
        if name in base.files:
            entry = {"name": name, "path": str(base.data / name), "managed": True}
            if base.files[name].encoding:
                entry["encoding"] = base.files[name].encoding
        elif name.startswith(("/", "~")):
            path = pathlib.Path(name).expanduser()
            if not path.is_absolute():
                sys.exit(f"{where}: setup.files cannot place {name!r}")
            entry = {"name": name, "path": str(path), "managed": False}
        else:
            sys.exit(f"{where}: setup.files names {name!r}, which is neither in data nor an "
                     f"absolute path — data has: {', '.join(sorted(base.files)) or 'nothing'}")
        files.append(entry)
    return files


def named_setups(raw, base: LaunchBase) -> dict | None:
    """The spec's `setups`, resolved by name — None when the spec has none,
    which keeps a bare-string item setup the text short form it always was."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        sys.exit("spec: setups must be an object, name -> setup")
    named = {}
    for name, setup in raw.items():
        where = f"setups.{name}"
        if not SETUP_NAME_RE.match(name):
            sys.exit(f"{where}: a name is lower-case letters, digits, - and _ "
                     f"(first a letter or digit), not {name!r}")
        if INLINE_ID_RE.match(name):
            sys.exit(f"{where}: {name!r} is how inline setups are numbered — pick another name")
        resolved = setup_of(setup, where, base)
        if resolved is None:
            sys.exit(f"{where}: empty setup")
        named[name] = resolved
    return named


def item_setup(value, number: int, named: dict | None,
               base: LaunchBase) -> tuple[str | None, dict | None]:
    """The id an item's setup goes by, and what that id stands for."""
    if named is not None and isinstance(value, str) and value:
        if value not in named:
            sys.exit(f"item {number}: unknown setup {value!r} — "
                     f"known: {', '.join(sorted(named)) or 'none'}")
        return value, named[value]
    setup = setup_of(value, f"item {number}", base)
    if setup is None:
        return None, None
    return INLINE_SETUP_ID.format(number), setup


# A fresh attachment is a data: URI; one that has been through a save is a path
# under `images/`. Rebuilding a list carries the second kind over, which is why it
# is allowed here — the files stay where they are, so rebuild into the same folder.
KEPT_RE = re.compile(r"^(images|bilder)/[0-9a-zA-Z._-]+$")


def images_of(owner: dict, where: str) -> list:
    """Attachments: fresh ones as data: URIs, saved ones as paths under images/."""
    images = owner.get("images") or []
    if not isinstance(images, list):
        sys.exit(f"{where}: images must be a list")
    for image in images:
        if not isinstance(image, str) or not (
                image.startswith("data:image/") or KEPT_RE.match(image)):
            sys.exit(f"{where}: images needs data:image/… URIs "
                     f"or paths like 'images/<name>' — got {image[:40]!r}")
    return list(images)


def frame(shell: str, spec: dict, faces: str, table: dict, lang: str) -> str:
    """The template with everything settled at build time — state and restart
    command stay open, serve.py fills those in on every request."""
    for placeholder in ("%%STATE%%", "%%TITLE%%", "%%RESTART%%", "%%FOLDER%%"):
        if shell.count(placeholder) != 1:
            sys.exit(f"shell needs exactly one {placeholder}, found: {shell.count(placeholder)}")
    body = translate(shell, table, f"shell vs {lang}")
    # Escaped: the title is markup here, and a `</title>` in it would end the
    # element and run whatever follows as part of the page.
    title = html.escape(spec.get("title", "Checklist"))
    return HEAD_OPEN + faces + HEAD_CLOSE + body.replace("%%TITLE%%", title) + DOC_CLOSE


def document(spec: dict, folder: pathlib.Path, lang: str = DEFAULT_LANG,
             project: Project | None = None, data: dict | None = None) -> tuple[dict, dict]:
    """The initial state and the frozen setups it refers to by id. `data` is
    the spec's, already through data_files — read from the spec if not given."""
    only(spec, SPEC_KEYS, "spec")
    if not isinstance(spec.get("items"), list) or not spec["items"]:
        sys.exit("spec: items must be a non-empty list")
    folder = folder.resolve()
    project = project or Project(folder.parent, {}, None)
    if data is None:
        data = data_files(spec.get("data"))
    spec_profiles = launch_profiles(spec.get("launches"), "spec, launches")
    base = LaunchBase((project.launch, launch_layer(spec.get("launch"), "spec, launch")),
                      project.root, folder / DATA_DIR,
                      merged_profiles(project.launches, spec_profiles), data)
    named = named_setups(spec.get("setups"), base)
    items, resolved_setups, inline_sources = state_items(spec["items"], named, base)
    warn_split_setups(items, resolved_setups)
    state = {"meta": meta_of(spec, lang), "items": items, "notes": notes_of(spec)}
    if named is not None:
        unused = [name for name in named if name not in resolved_setups]
        if unused:
            print(f"warning: no item uses setup {', '.join(unused)}", file=sys.stderr)
    warn_unused_profiles(spec_profiles, [*(spec.get("setups") or {}).values(),
                                         *inline_sources.values()])
    # Only what the spec gave, so --as-spec does not invent keys it never had.
    source = {"inline": inline_sources}
    for key in ("setups", "launch", "launches", "data"):
        if spec.get(key) is not None:
            source[key] = spec[key]
    return state, {"setups": resolved_setups, "source": source}


def state_items(raw_items: list, named: dict | None,
                base: LaunchBase) -> tuple[list, dict, dict]:
    """The spec's items as the state holds them, plus the setups they use:
    resolved by id, and — for the inline ones — as the spec wrote them. An
    inline setup written exactly like an earlier one takes that one's id, so
    the page folds it to `same as` just like a repeated named setup."""
    items = []
    resolved_setups, inline_sources = {}, {}
    inline_ids: dict[str, str] = {}
    for number, raw in enumerate(raw_items, 1):
        only(raw, ITEM_KEYS, f"item {number}")
        if not raw.get("title"):
            sys.exit(f"item {number}: title is missing")
        state = raw.get("state", "open")
        if state not in STATES:
            sys.exit(f"item {number}: unknown state {state!r} — "
                     f"allowed: {', '.join(sorted(STATES))}")
        setup_id, setup = item_setup(raw.get("setup"), number, named, base)
        if setup is not None:
            if named is None or setup_id not in named:
                written = json.dumps(raw["setup"], sort_keys=True)
                setup_id = inline_ids.setdefault(written, setup_id)
                inline_sources[setup_id] = raw["setup"]
            resolved_setups[setup_id] = setup
        items.append({
            "title": raw["title"],
            "detail": raw.get("detail", ""),
            "setup": setup_id,
            "state": state,
            "collapsed": bool(raw.get("collapsed", False)),
            "note": raw.get("note", ""),
            "images": images_of(raw, f"item {number}"),
        })
    return items, resolved_setups, inline_sources


def warn_split_setups(items: list, resolved_setups: dict) -> None:
    """Items with the same setup belong together: one after the other, the
    page folds the repeat to `same as`, and ▶ can skip the build. Equal by
    resolved content, so setups that resolve alike count as one even under
    two ids. Only a warning — a real dependency may force the order. Together
    under two ids still warns: the page folds by id, so it shows both."""
    runs: dict[str, list] = {}
    previous = None
    for number, item in enumerate(items, 1):
        setup_id = item["setup"]
        key = None if setup_id is None else json.dumps(resolved_setups[setup_id], sort_keys=True)
        if key is not None:
            runs.setdefault(key, []).append((number, setup_id, key != previous))
        previous = key
    for uses in runs.values():
        numbers = ", ".join(f"{number:02d}" for number, _, _ in uses)
        ids = list(dict.fromkeys(setup_id for _, setup_id, _ in uses))
        names = ", ".join(ids)
        if sum(starts for _, _, starts in uses) >= 2:
            print(f"warning: setup {names} is split up — items {numbers}; put them one after "
                  "another unless a dependency forces the order", file=sys.stderr)
        elif len(ids) > 1:
            print(f"warning: setups {names} are the same — items {numbers}; give them one "
                  "name so the page folds the repeat", file=sys.stderr)


def meta_of(spec: dict, lang: str) -> dict:
    """The page's head and settings, as the state carries them."""
    return {
        "eyebrow": spec.get("eyebrow", ""),
        "heading": spec.get("heading", spec.get("title", "")),
        "standfirst": spec.get("standfirst", ""),
        # The page reads this, so it survives a --as-spec round trip on its
        # own.
        "autofold": bool(spec.get("autofold", True)),
        # Kept so --as-spec can write it back and a rebuild stays in the
        # language the page was built in.
        "lang": lang,
    }


def notes_of(spec: dict) -> dict:
    """The observations at the foot: text, or an object {text, images}."""
    notes = spec.get("notes", "")
    if isinstance(notes, str):
        notes = {"text": notes}
    if not isinstance(notes, dict):
        sys.exit("notes must be text or an object {text, images}")
    only(notes, {"text", "images"}, "notes")
    return {"text": notes.get("text", ""), "images": images_of(notes, "notes")}


def chosen_profile(setup) -> str | None:
    """The profile a raw setup — already checked by setup_of — chooses."""
    launch = setup.get("launch") if isinstance(setup, dict) else None
    if isinstance(launch, dict):
        return launch.get(PROFILE_KEY)
    return launch if isinstance(launch, str) else None


def warn_unused_profiles(spec_profiles: dict, setups: list) -> None:
    """A spec profile no setup chooses is likely a typo or a leftover. The
    project's are left alone: they serve every checklist of the project, and
    each one uses only some."""
    used = {chosen_profile(setup) for setup in setups}
    unused = [name for name in spec_profiles if name not in used]
    if unused:
        print(f"warning: no setup uses launch profile {', '.join(unused)}", file=sys.stderr)


def saved_state(folder: pathlib.Path) -> dict | None:
    """The folder's state if a human has saved into it — `meta.folder` is
    stamped in by serve.py on every save, never by a build."""
    path = folder / "state.json"
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as broken:
        sys.exit(f"{path} is not valid JSON: {broken} — --force overwrites it")
    return state if (state.get("meta") or {}).get("folder") else None


def merge_items(items: list, previous: list) -> list:
    """The new items, each with what a human put into the old one of that title.

    Matched by title, first come first served, so two items of the same title
    pair off in order. Old items nobody claims are named, not dropped in silence.
    """
    unclaimed = list(previous)
    merged = []
    for item in items:
        index = next((i for i, old in enumerate(unclaimed)
                      if old.get("title") == item["title"]), None)
        if index is not None:
            old = unclaimed.pop(index)
            item = {**item, **{field: old[field] for field in MERGED_FIELDS if field in old}}
        merged.append(item)
    for old in unclaimed:
        print(f"warning: --merge dropped {old.get('title')!r} "
              f"({old.get('state', 'open')}) — no new item has that title", file=sys.stderr)
    return merged


def merge_notes(notes: dict, previous) -> dict:
    """The observations at the foot belong to no item, so no title can claim
    them: the old ones stay unless the spec brings its own."""
    if isinstance(previous, str):                    # short form, older folders
        previous = {"text": previous}
    old_text, old_images = (previous or {}).get("text", ""), (previous or {}).get("images") or []
    if not notes["text"] and not notes["images"]:
        return {"text": old_text, "images": old_images}
    if old_text or old_images:
        print(f"warning: --merge replaced the observations {old_text!r} "
              f"({len(old_images)} image(s)) with the spec's", file=sys.stderr)
    return notes


def merge(state: dict, previous: dict) -> dict:
    """The fresh state with the human's work from `previous` carried over.

    `meta.folder` stays: the state still holds saved results, so the next
    rebuild must be protected as well.
    """
    return {**state,
            "meta": {**state["meta"], "folder": previous["meta"]["folder"]},
            "items": merge_items(state["items"], previous.get("items") or []),
            "notes": merge_notes(state["notes"], previous.get("notes"))}


def build(spec: dict, shell: str, faces: str, table: dict, lang: str,
          folder: pathlib.Path, project: Project | None = None,
          rebuild: str = REBUILD_REFUSE) -> None:
    previous = None if rebuild == REBUILD_FORCE else saved_state(folder)
    if previous is not None and rebuild != REBUILD_MERGE:
        sys.exit(f"{folder} holds a saved checklist — rebuilding would wipe its "
                 f"ticks and notes.\n  --merge carries them over to items of the "
                 f"same title, --force overwrites them.")
    # Everything that can abort runs before the first write, so a bad spec
    # leaves the folder as it was — the test data included, read and encoded
    # here in full.
    data = data_files(spec.get("data"))
    state, frozen = document(spec, folder, lang, project, data)
    if previous is not None:
        state = merge(state, previous)
    template = frame(shell, spec, faces, table, lang)
    folder.mkdir(parents=True, exist_ok=True)
    # Every build, --merge included, puts the test data back as the spec says:
    # whatever a previous round of testing did to it is not a result. First,
    # because it is what can still fail on the disk: until it is through, the
    # old template, state and setups stay as they were.
    write_data(folder, data)
    (folder / "template.html").write_text(template, encoding="utf-8")
    (folder / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / SETUPS_FILE).write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    # A rebuild opens a fresh round of ticking, so an earlier read is spent —
    # otherwise a serve.py on this folder would time itself out on stale news.
    (folder / READ_MARK).unlink(missing_ok=True)


def argument_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(usage="%(prog)s spec folder [options] | --clean folder")
    p.add_argument("spec")
    # Optional only so `--clean folder` needs no spec; a build checks it below.
    p.add_argument("out", nargs="?", help="working folder")
    p.add_argument("--shell", default=str(pathlib.Path(__file__).with_name("shell.html")))
    p.add_argument("--fonts", default=str(pathlib.Path(__file__).with_name("fonts.css")),
                   help="inlined webfaces; regenerate with refresh-fonts.py")
    p.add_argument("--lang", default=None,
                   help="language of the page chrome; a file in strings/. Beats the "
                        "spec's and checklist.json's lang; without any: en")
    rebuild = p.add_mutually_exclusive_group()
    rebuild.add_argument("--merge", dest="rebuild", action="store_const", const=REBUILD_MERGE,
                         help="rebuild a saved folder, carrying ticks, notes and "
                              "images over to items of the same title")
    rebuild.add_argument("--force", dest="rebuild", action="store_const", const=REBUILD_FORCE,
                         help="rebuild a saved folder, discarding what was saved")
    rebuild.add_argument("--clean", action="store_true",
                         help="remove the folder's data/ and .pristine/ and stop; the "
                              "results, images and template stay")
    p.set_defaults(rebuild=REBUILD_REFUSE)
    return p


def main() -> None:
    p = argument_parser()
    a = p.parse_args()

    if a.clean:
        folder = pathlib.Path(a.out or a.spec).expanduser().resolve()
        removed = clean(folder)
        print("removed " + ", ".join(str(path) for path in removed) if removed
              else f"{folder}: no test data to remove")
        return
    if a.out is None:
        p.error("the working folder is missing")

    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        sys.exit("spec: must be an object")
    folder = pathlib.Path(a.out).expanduser().resolve()
    project = find_project(folder.parent, pathlib.Path.home())
    lang = choose_lang(a.lang, spec, project.lang, previous_lang(folder, a.rebuild))
    shell = pathlib.Path(a.shell).read_text(encoding="utf-8")
    table = strings(pathlib.Path(__file__).with_name("strings") / f"{lang}.json")
    build(spec, shell, fonts(pathlib.Path(a.fonts)), table, lang, folder,
          project, a.rebuild)
    here = pathlib.Path(__file__).parent
    print(f"{a.out}/ — {len(spec['items'])} items\n"
          f"  serve: python3 {here / 'serve.py'} {a.out} --open\n"
          f"  read:  python3 {here / 'read.py'} {a.out}")


if __name__ == "__main__":
    main()

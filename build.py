#!/usr/bin/env python3
"""Turns a checklist spec into a working folder for serve.py.

    build.py spec.json folder [--shell shell.html] [--lang en] [--merge | --force]

The folder gets `template.html`, `state.json` and `setups.json`. `%%STATE%%`
stays open in the template — `serve.py` fills the state in on every request and
takes it back via PUT.

A folder a human has saved into (`meta.folder` in its state) holds results, and
a rebuild would wipe them — so that aborts. `--merge` hands each new item the
state, note, images and fold of the old item with the identical title and names
the old items nothing claimed; `--force` overwrites.

Setups are frozen here, not carried in the state. The state is what the page
writes back, and a setup names paths the server will reveal and commands it
will run — so the page must not be able to change them. `state.json` items carry
only a setup id (`item-NN` for an inline setup, 1-based; the name for a named
one); `setups.json` holds the resolved setup per id under `setups`, and under
`source` what the spec gave — `inline` setups by id, `setups` and `launch` as
written — so `read.py --as-spec` can hand it back to this script unchanged.

Project config: `checklist.json`, the first one found walking up from the
folder's parent; the walk stops at a directory with `.git` (inclusive) or at
$HOME. It may hold `lang`, `launch` and `launches`, nothing else — what every
checklist of a project shares, written down once.

    {"lang": "de", "launch": {"build": "make app", "run": "open -a App {files}"},
     "launches": {"editor": {"run": "open -a Editor {files}"}}}

Language: `--lang` beats the spec's `lang`, that beats the project's, then
`en`. It lands in `meta.lang`, so `--as-spec` writes it back into the spec.

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
import dataclasses
import json
import pathlib
import re
import shlex
import sys

STATES = {"open", "ok", "problem", "skipped"}
SETUPS_FILE = "setups.json"
PROJECT_CONFIG = "checklist.json"
DATA_DIR = "data"
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
             "lang", "setups", "launch", "launches"}
ITEM_KEYS = {"title", "detail", "setup", "state", "collapsed", "note", "images"}
SETUP_KEYS = {"label", "text", "commands", "links", "paths", "collapsed", "launch"}
LAUNCH_KEYS = {"cwd", *LAUNCH_COMMANDS, "url", "ready_timeout"}
# Only a setup chooses a profile; a profile or a default choosing one would chain.
PROFILE_KEY = "profile"
PROJECT_KEYS = {"launch", "launches", "lang"}
# The typos that actually happen — the singular/plural pairs above all.
HINTS = {"notes": "note", "comment": "note", "remark": "note",
         "status": "state", "image": "images", "description": "detail",
         "body": "detail", "steps": "items", "command": "commands",
         "link": "links", "path": "paths", "prerequisites": "setup"}


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


def choose_lang(cli: str | None, spec: dict, project_lang: str | None) -> str:
    """`--lang` beats the spec, the spec beats the project, `en` is the floor.

    The spec's own `lang` is what makes a rebuild keep its language — `read.py
    --as-spec` writes it back — so a forgotten `--lang` no longer flips the page.
    """
    spec_lang = lang_of(spec["lang"], "spec") if "lang" in spec else None
    return cli or spec_lang or project_lang or DEFAULT_LANG


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


def find_project(start: pathlib.Path, stop: pathlib.Path) -> Project:
    """The nearest `checklist.json` from `start` upwards.

    The walk ends at a repository root (a `.git` there, checked inclusive) or at
    `stop` — a config in some unrelated folder further up must not reach into
    this project. Nothing found: no config, and `start` is the root.
    """
    here = start
    while True:
        if (here / PROJECT_CONFIG).is_file():
            return load_project(here / PROJECT_CONFIG)
        if (here / ".git").exists() or here == stop or here.parent == here:
            return Project(start, {}, None)
        here = here.parent


@dataclasses.dataclass(frozen=True)
class LaunchBase:
    """What every setup's launch starts from: the default levels below it
    (project, then spec), the profiles it may choose (project and spec already
    merged per name), and the places its defaults and placeholders point to."""
    layers: tuple
    root: pathlib.Path
    data: pathlib.Path
    profiles: dict = dataclasses.field(default_factory=dict)


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
    if url is not None and not url.startswith(("http://", "https://")):
        sys.exit(f"{where}: launch.url needs http(s), not {url!r}")
    cwd = pathlib.Path(merged.get("cwd") or base.root).expanduser()
    if not cwd.is_absolute():
        sys.exit(f"{where}: launch.cwd needs an absolute path, not {merged['cwd']!r}")
    if launch["run"] is None:
        return None
    return {"cwd": str(cwd), **launch, "url": url,
            "ready_timeout": merged.get("ready_timeout", DEFAULT_READY_TIMEOUT)}


def setup_of(setup, where: str, base: LaunchBase) -> dict | None:
    """A setup's prerequisites: which app, which server, which files.

    Bare text is the short form. Links have to be `http(s)` — the page turns them
    into a button, and a `javascript:` target has no business there.
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

    links = setup.get("links") or []
    for link in links:
        if not isinstance(link, dict) or not str(link.get("url", "")).startswith(("http://", "https://")):
            sys.exit(f"{where}: setup.links needs objects with an http(s) url")

    # Absolute, so the button hits regardless of the working directory; the short
    # `~` form is what gets shown, so the line stays readable.
    paths = []
    for entry in setup.get("paths") or []:
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

    profile, own_launch = setup_launch(setup.get("launch"), f"{where}, setup.launch")
    return {
        "label": setup.get("label", ""),
        "collapsed": bool(setup.get("collapsed", False)),
        "text": setup.get("text", ""),
        "commands": list(commands),
        "links": [{"label": link.get("label", ""), "url": link["url"]} for link in links],
        "paths": paths,
        # No setup names files yet, so `{files}` stands for nothing; the list
        # goes in here once setups carry `files`.
        "launch": resolve_launch(base, profile, own_launch, [], where),
    }


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
    return (HEAD_OPEN + faces + HEAD_CLOSE
            + body.replace("%%TITLE%%", spec.get("title", "Checklist")) + DOC_CLOSE)


def document(spec: dict, folder: pathlib.Path, lang: str = DEFAULT_LANG,
             project: Project | None = None) -> tuple[dict, dict]:
    """The initial state and the frozen setups it refers to by id."""
    only(spec, SPEC_KEYS, "spec")
    if not isinstance(spec.get("items"), list) or not spec["items"]:
        sys.exit("spec: items must be a non-empty list")
    folder = folder.resolve()
    project = project or Project(folder.parent, {}, None)
    spec_profiles = launch_profiles(spec.get("launches"), "spec, launches")
    base = LaunchBase((project.launch, launch_layer(spec.get("launch"), "spec, launch")),
                      project.root, folder / DATA_DIR,
                      merged_profiles(project.launches, spec_profiles))
    named = named_setups(spec.get("setups"), base)

    items = []
    resolved_setups, inline_sources = {}, {}
    for number, raw in enumerate(spec["items"], 1):
        only(raw, ITEM_KEYS, f"item {number}")
        if not raw.get("title"):
            sys.exit(f"item {number}: title is missing")
        state = raw.get("state", "open")
        if state not in STATES:
            sys.exit(f"item {number}: unknown state {state!r} — "
                     f"allowed: {', '.join(sorted(STATES))}")
        setup_id, setup = item_setup(raw.get("setup"), number, named, base)
        if setup is not None:
            resolved_setups[setup_id] = setup
            if named is None or setup_id not in named:
                inline_sources[setup_id] = raw["setup"]
        items.append({
            "title": raw["title"],
            "detail": raw.get("detail", ""),
            "setup": setup_id,
            "state": state,
            "collapsed": bool(raw.get("collapsed", False)),
            "note": raw.get("note", ""),
            "images": images_of(raw, f"item {number}"),
        })

    notes = spec.get("notes", "")
    if isinstance(notes, str):
        notes = {"text": notes}
    if not isinstance(notes, dict):
        sys.exit("notes must be text or an object {text, images}")
    only(notes, {"text", "images"}, "notes")

    state = {
        "meta": {
            "eyebrow": spec.get("eyebrow", ""),
            "heading": spec.get("heading", spec.get("title", "")),
            "standfirst": spec.get("standfirst", ""),
            # The page reads this, so it survives a --as-spec round trip on its
            # own.
            "autofold": bool(spec.get("autofold", True)),
            # Kept so --as-spec can write it back and a rebuild stays in the
            # language the page was built in.
            "lang": lang,
        },
        "items": items,
        "notes": {"text": notes.get("text", ""), "images": images_of(notes, "notes")},
    }
    if named is not None:
        unused = [name for name in named if name not in resolved_setups]
        if unused:
            print(f"warning: no item uses setup {', '.join(unused)}", file=sys.stderr)
    warn_unused_profiles(spec_profiles, [*(spec.get("setups") or {}).values(),
                                         *inline_sources.values()])
    # Only what the spec gave, so --as-spec does not invent keys it never had.
    source = {"inline": inline_sources}
    for key in ("setups", "launch", "launches"):
        if spec.get(key) is not None:
            source[key] = spec[key]
    return state, {"setups": resolved_setups, "source": source}


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
    # leaves the folder as it was.
    state, frozen = document(spec, folder, lang, project)
    if previous is not None:
        state = merge(state, previous)
    template = frame(shell, spec, faces, table, lang)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.html").write_text(template, encoding="utf-8")
    (folder / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / SETUPS_FILE).write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2), encoding="utf-8")
    # A rebuild opens a fresh round of ticking, so an earlier read is spent —
    # otherwise a serve.py on this folder would time itself out on stale news.
    (folder / ".read").unlink(missing_ok=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("spec")
    p.add_argument("out", help="working folder")
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
    p.set_defaults(rebuild=REBUILD_REFUSE)
    a = p.parse_args()

    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        sys.exit("spec: must be an object")
    folder = pathlib.Path(a.out).expanduser().resolve()
    project = find_project(folder.parent, pathlib.Path.home())
    lang = choose_lang(a.lang, spec, project.lang)
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

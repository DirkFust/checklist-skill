#!/usr/bin/env python3
"""Turns a checklist spec into a working folder for serve.py.

    build.py spec.json folder [--shell shell.html] [--lang en]

The folder gets `template.html` and `state.json`. `%%STATE%%` stays open in the
template — `serve.py` fills the state in on every request and takes it back via
PUT.

Spec format:

    {
      "title":    "Short name",           // browser tab
      "eyebrow":  "Context",
      "heading":  "Headline",
      "standfirst": "A sentence or two.",
      "autofold": true,                   // a ticked item folds itself away
                                          //   after a moment; false keeps every
                                          //   item where the user put it
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
         "setup": {                       // optional, sits above the item
           "label": "Setup",              //   heading, short and upper-cased
           "text": "What has to be running.\\nSecond line.",
           "commands": ["make run-gui"],  //   one copy button per line
           "links": [{"label": "Preview", "url": "http://localhost:3000"}],
           "paths": [{"label": "Test data",           //   button: reveal in Finder
                      "path": "~/dev/x/.manual-tests/case-1"}],
           "collapsed": false            //   true starts folded
         }}
      ],
      "notes": "Pre-fill for the observations field"  // optional, also as
                                                      // {"text": …, "images": […]}
    }

The page speaks one language, chosen at build time with `--lang`. The chrome
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
import json
import pathlib
import re
import sys

STATES = {"open", "ok", "problem", "skipped"}

# A key nobody reads is worse than an error: the spec looks complete and the
# content is missing anyway. So what is allowed is written down here, and
# anything else aborts the build.
SPEC_KEYS = {"title", "eyebrow", "heading", "standfirst", "items", "notes", "autofold"}
ITEM_KEYS = {"title", "detail", "setup", "state", "collapsed", "note", "images"}
SETUP_KEYS = {"label", "text", "commands", "links", "paths", "collapsed"}
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


def setup_of(raw: dict, where: str) -> dict | None:
    """An item's prerequisites: which app, which server, which files.

    Bare text is the short form. Links have to be `http(s)` — the page turns them
    into a button, and a `javascript:` target has no business there.
    """
    setup = raw.get("setup")
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

    return {
        "label": setup.get("label", ""),
        "collapsed": bool(setup.get("collapsed", False)),
        "text": setup.get("text", ""),
        "commands": list(commands),
        "links": [{"label": link.get("label", ""), "url": link["url"]} for link in links],
        "paths": paths,
    }


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


def document(spec: dict) -> dict:
    only(spec, SPEC_KEYS, "spec")
    if not isinstance(spec.get("items"), list) or not spec["items"]:
        sys.exit("spec: items must be a non-empty list")

    items = []
    for number, raw in enumerate(spec["items"], 1):
        only(raw, ITEM_KEYS, f"item {number}")
        if not raw.get("title"):
            sys.exit(f"item {number}: title is missing")
        state = raw.get("state", "open")
        if state not in STATES:
            sys.exit(f"item {number}: unknown state {state!r} — "
                     f"allowed: {', '.join(sorted(STATES))}")
        items.append({
            "title": raw["title"],
            "detail": raw.get("detail", ""),
            "setup": setup_of(raw, f"item {number}"),
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

    return {
        "meta": {
            "eyebrow": spec.get("eyebrow", ""),
            "heading": spec.get("heading", spec.get("title", "")),
            "standfirst": spec.get("standfirst", ""),
            # The page reads this, so it survives a --as-spec round trip on its
            # own; --lang does not, that one has to be given again.
            "autofold": bool(spec.get("autofold", True)),
        },
        "items": items,
        "notes": {"text": notes.get("text", ""), "images": images_of(notes, "notes")},
    }


def build(spec: dict, shell: str, faces: str, table: dict, lang: str,
          folder: pathlib.Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "template.html").write_text(
        frame(shell, spec, faces, table, lang), encoding="utf-8")
    (folder / "state.json").write_text(
        json.dumps(document(spec), ensure_ascii=False, indent=2), encoding="utf-8")
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
    p.add_argument("--lang", default="en",
                   help="language of the page chrome; a file in strings/")
    a = p.parse_args()

    spec = json.loads(pathlib.Path(a.spec).read_text(encoding="utf-8"))
    shell = pathlib.Path(a.shell).read_text(encoding="utf-8")
    table = strings(pathlib.Path(__file__).with_name("strings") / f"{a.lang}.json")
    build(spec, shell, fonts(pathlib.Path(a.fonts)), table, a.lang, pathlib.Path(a.out))
    here = pathlib.Path(__file__).parent
    print(f"{a.out}/ — {len(spec['items'])} items\n"
          f"  serve: python3 {here / 'serve.py'} {a.out} --open\n"
          f"  read:  python3 {here / 'read.py'} {a.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Reads a checklist's state and writes it to stdout as a report.

    read.py folder

Why this script exists: querying `state.json` by hand means remembering the key
names — `note` on an item, `notes.text` at the foot of the page. Reach for the
wrong one and you get no error, you get an empty answer, and an empty answer
reads like "no notes". Here every field is on the page or it is not there at all.

What comes out:

  · whether anything was ever saved (`serve.py` stamps `meta.folder` in on every
    save; if it is missing, the page never sent anything back)
  · per item its number, marker, title, note and image paths
  · the observations at the foot
  · a list of every image still waiting to be opened with `Read`

This script does not open images — it cannot. It names the absolute paths so
they can go straight into a `Read` without thinking about it.

Reading also drops a `.read` marker into the folder — that is what lets a running
`serve.py` stop: the results are out. Only when something was actually saved; a
pre-fill is not a result. Every save deletes the marker again, so a human who
carries on ticking after the report keeps their server.

`--as-spec` prints the state back in the shape `build.py` eats, so a list can be
extended without losing what the human already put in: read it, add the new
items, rebuild **into the same folder** (the attachments stay where they are)
with `--force` — the spec already carries the ticks, and a saved folder refuses
a plain rebuild. That is a conversion, not a result, and sets no marker.
`--json` prints the raw state.

Setups are not in the state any more, only their ids — `setups.json` keeps each
inline setup exactly as the spec gave it, plus the named `setups`, the spec's
`launch`, `launches` and `data`, and `--as-spec` puts all of that back in place,
with `lang` from the state.
Folders built before then still carry the setup object in the state; that is
what goes back for them — without `launch` and `files` (a warning names each
item): the page writes that state, so nothing in it may become a command.
"""

import argparse
import datetime
import json
import pathlib
import sys

from workfolder import READ_MARK, SETUPS_FILE

TOKEN = {"open": "[ ]", "ok": "[x]", "problem": "[!]", "skipped": "[-]"}
# What a setup out of a page-written state must not carry into a spec.
UNTRUSTED_SETUP_KEYS = ("launch", "files")


def mark_read(folder: pathlib.Path, state: dict) -> bool:
    """Say that the results are out, so a running serve.py may go.

    Only a saved state counts. Without `meta.folder` nothing ever came back from
    the page and what was read is the pre-fill — arming a shutdown on that would
    take the server away before anyone has ticked a single box.
    """
    if not (state.get("meta") or {}).get("folder"):
        return False
    (folder / READ_MARK).touch()
    return True


def load(folder: pathlib.Path) -> dict:
    state = folder / "state.json"
    if not state.is_file():
        sys.exit(f"{state} does not exist — wrong folder, or never built.")
    try:
        return json.loads(state.read_text(encoding="utf-8"))
    except json.JSONDecodeError as broken:
        sys.exit(f"{state} is not valid JSON: {broken}")


def load_setups(folder: pathlib.Path) -> dict | None:
    """The frozen setups file, or None for a folder built before there was one."""
    path = folder / SETUPS_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as broken:
        sys.exit(f"{path} is not valid JSON: {broken}")


def spec_setup(setup, frozen: dict | None, number: int):
    """An item's setup as the spec wrote it: a named setup by its name, the raw
    inline source for an inline id, the state's own object for a folder without
    frozen setups — minus what would run or be written on a rebuild."""
    if isinstance(setup, str) and frozen is not None:
        source = frozen.get("source", {})
        if setup in (source.get("setups") or {}):
            return setup
        return source.get("inline", {}).get(setup)
    if frozen is None and isinstance(setup, dict):
        return untrusted_setup(setup, number)
    return setup


def untrusted_setup(setup: dict, number: int) -> dict:
    """A setup out of an old folder's state. The page wrote that state, so its
    `launch` and `files` prove nothing — carried into a spec, a rebuild would
    freeze a command the page put there, and ▶ would run it. They go; whoever
    rebuilds writes them again on purpose."""
    dropped = [key for key in UNTRUSTED_SETUP_KEYS if key in setup]
    if dropped:
        print(f"warning: item {number:02d}: dropped {' and '.join(dropped)} from its setup — "
              f"this folder predates setups.json, its state is the page's to write; "
              f"put them back into the spec yourself if they belong there", file=sys.stderr)
    return {key: value for key, value in setup.items() if key not in UNTRUSTED_SETUP_KEYS}


def indent(text: str, pad: str) -> str:
    return "\n".join(pad + line for line in text.strip().splitlines())


def report(folder: pathlib.Path, state: dict) -> list[str]:
    """Everything the state holds — empty fields reported as empty, explicitly."""
    meta = state.get("meta") or {}
    items = state.get("items") or []
    notes = state.get("notes") or {}
    if isinstance(notes, str):                       # short form from a spec
        notes = {"text": notes}

    stamp = datetime.datetime.fromtimestamp(
        (folder / "state.json").stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    saved = bool(meta.get("folder"))

    lines = [f"Folder:  {folder}",
             f"Title:   {meta.get('heading') or meta.get('eyebrow') or '—'}",
             f"As of:   {stamp}" + ("" if saved else "   (never saved)")]
    if not saved:
        lines.append("!! The page never sent a state back. What follows is the pre-fill")
        lines.append("!! from the spec — not a result produced by the human.")
    lines.append(f"Items:   {len(items)} — {counted(items)}")
    lines.append("")

    for number, item in enumerate(items, 1):
        lines.extend(item_lines(number, item, folder))
    lines.extend(skipped_lines(items))

    text = (notes.get("text") or "").strip()
    lines.append("Observations:")
    lines.append(indent(text, "  ") if text else "  —")
    lines.extend(f"  Image: {folder / image}" for image in notes.get("images") or [])

    shots = [str(folder / image) for bucket in [*items, notes]
             for image in bucket.get("images") or []]
    if shots:
        lines.append("")
        lines.append(f"{len(shots)} image(s) — open each with `Read` before reporting on it:")
        lines.extend("  " + shot for shot in shots)
    return lines


def counted(items: list) -> str:
    """How many items stand in each state, for the summary line."""
    tally = {key: 0 for key in TOKEN}
    for item in items:
        key = item.get("state", "open")
        tally[key] = tally.get(key, 0) + 1
    text = "{} fine, {} open, {} problem".format(
        tally["ok"], tally["open"], tally["problem"])
    # Named only when there are any: a standing "0 skipped" is noise on the
    # line that gets read first.
    if tally["skipped"]:
        text += ", {} skipped".format(tally["skipped"])
    # Anything outside the three states is named rather than silently dropped —
    # otherwise the numbers do not add up and nothing says why.
    stray = {key: n for key, n in tally.items() if key not in TOKEN and n}
    if stray:
        text += "".join(f", {n} {key!r}(?)" for key, n in sorted(stray.items()))
    return text


def item_lines(number: int, item: dict, folder: pathlib.Path) -> list[str]:
    """One item: marker and title, its note — or what its missing note means —
    and its images, closed by a blank line."""
    state_key = item.get("state", "open")
    lines = ["{:02d} {} {}".format(number, TOKEN.get(state_key, "[?]"), item.get("title", "—"))]
    note = (item.get("note") or "").strip()
    if note:
        head, *rest = note.splitlines()
        lines.append(f"      Note:  {head}")
        lines.extend("             " + line for line in rest)
    elif state_key == "problem":
        lines.append("      Note:  — (a problem with no note — ask about it)")
    elif state_key == "skipped":
        lines.append("      Note:  — (no reason given)")
    lines.extend(f"      Image: {folder / image}" for image in item.get("images") or [])
    lines.append("")
    return lines


def skipped_lines(items: list) -> list[str]:
    """The skipped items named once more, as settled — nothing if there are none.

    The marker exists so nobody has to answer for these twice. Spelling that
    out here beats hoping the tokens in the list are read as an instruction.
    """
    settled = ["{:02d}".format(n) for n, item in enumerate(items, 1)
               if item.get("state") == "skipped"]
    if not settled:
        return []
    return ["Skipped ({}) — set aside on purpose. Do not report these "
            "as open and do not ask for them to be run.".format(", ".join(settled)),
            ""]


def as_spec(state: dict, frozen: dict | None = None) -> dict:
    """The state in the shape `build.py` reads.

    Rebuilding overwrites `state.json` and with it every tick, note and image
    reference. Hand-copying those back is where they get lost, so the conversion
    lives here: `meta.*` moves back up top, `meta.folder` (which serve.py stamps
    in and no spec knows) is dropped, attachments keep their paths, and setups
    come back from `frozen` (the parsed `setups.json`) in their raw form.
    """
    meta = state.get("meta") or {}
    notes = state.get("notes") or {}
    if isinstance(notes, str):
        notes = {"text": notes}
    spec = {
        "title": meta.get("heading") or "Checklist",
        "eyebrow": meta.get("eyebrow", ""),
        "heading": meta.get("heading", ""),
        "standfirst": meta.get("standfirst", ""),
        "autofold": meta.get("autofold", True),
        "items": [],
        "notes": {"text": notes.get("text", ""), "images": notes.get("images") or []},
    }
    # Absent in folders built before these existed; then the spec leaves them out.
    if meta.get("lang"):
        spec["lang"] = meta["lang"]
    source = (frozen or {}).get("source", {})
    for key in ("setups", "launch", "launches", "data"):
        if key in source:
            spec[key] = source[key]
    spec["items"] = [spec_item(item, frozen, number)
                     for number, item in enumerate(state.get("items") or [], 1)]
    return spec


def spec_item(item: dict, frozen: dict | None, number: int) -> dict:
    """One item as the spec writes it, ticks, note and attachments included."""
    kept = {"title": item.get("title", ""),
            "detail": item.get("detail", ""),
            "state": item.get("state", "open"),
            "collapsed": bool(item.get("collapsed")),
            "note": item.get("note", ""),
            "images": item.get("images") or []}
    setup = spec_setup(item.get("setup"), frozen, number)
    if setup:
        kept["setup"] = setup
    return kept


def main() -> None:
    p = argparse.ArgumentParser(description="print a checklist's state")
    p.add_argument("folder", help="the checklist's working folder")
    p.add_argument("--json", action="store_true", help="print the raw state instead")
    p.add_argument("--as-spec", action="store_true",
                   help="print it as a build.py spec, to extend the list without "
                        "losing ticks and notes (rebuild into the same folder "
                        "with --force)")
    a = p.parse_args()

    folder = pathlib.Path(a.folder).expanduser().resolve()
    state = load(folder)
    if a.as_spec:
        print(json.dumps(as_spec(state, load_setups(folder)), ensure_ascii=False, indent=2))
        return
    if a.json:
        mark_read(folder, state)
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    print("\n".join(report(folder, state)))
    if mark_read(folder, state):
        print("\n.read set — a running serve.py stops half an hour from now; "
              "the next save clears it again.")


if __name__ == "__main__":
    main()

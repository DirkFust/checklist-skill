# Checklist — mechanics

How the page behaves underneath. `SKILL.md` holds the judgment; this is what to look up when a
human asks why something moved, or when you change `shell.html` or `serve.py`.

## Contents

- Folding: the timer and the scroll
- Saving: what travels where
- Rescue: when the server dies
- Markup is DOM

## Folding: the timer and the scroll

**A ticked item folds itself away after two seconds** — but the timer only arms the fold.
Pointer and caret have the last word: while the mouse rests on the item or the caret sits in
its note, the fold waits for them to leave, so nothing disappears from under a hand. Only an
editable field counts — the marker button takes the focus on its own click, and counting that
would leave every ticked item waiting for a click somewhere else.

The automatic fold compensates its own height with a scroll, anchored on the pointer, so the
ground below it stays where the hand left it — without that, the next click lands one item too
far. **Folding by hand does not compensate**, and must not: there the pointer is on the item's
own head line, which stays put precisely when nothing scrolls.

Every further click on the marker disarms the timer, and that is what keeps the way to `[!]`
and `[-]` open. `[!]` unfolds the item again, because it wants a note written into it; `[-]`
stays folded, being settled. Nothing folds on load.

## Saving: what travels where

`build.py` writes `template.html` (with `%%STATE%%` left open), `state.json` and `setups.json`.
`serve.py` fills the state in on every request; the save button PUTs it back to `/state`, the
server writes images as files and answers with the state including paths — the page adopts
them without re-rendering. It binds to `127.0.0.1` and nothing else. Every save also deletes
`.read`, so a checklist somebody is still working on cannot be timed out as read; so does a
rebuild, which opens a fresh round of ticking.

`read.py` drops that `.read` marker into the folder — that is what lets a running `serve.py`
go: the results are out. It sets one only when something was actually saved, so a pre-fill
never arms a shutdown, and `--as-spec` sets none at all (that is a conversion, not a result).

On save each image becomes a file under `<folder>/images/`, named after its content: original
format, original size, no limit. The state then carries paths only, and `serve.py` sweeps away
attachments nobody names any more.

## Rescue: when the server dies

If a save fails, a rescue block unfolds in the page. The state is then still in the browser
and is lost only if the window closes:

- **Save as a file** puts `state.json` into Downloads. That is the way as soon as unsaved
  images are involved — those sit in the JSON as `data:` URIs and blow past any clipboard.
- **To the clipboard** for small, image-free states. If the browser refuses, the text appears
  for selecting by hand.

The block names the full restart command — `serve.py` fills it in when serving, absolute path
and port included. After that the page reports on its own that the server is back; another
click on Save is enough. A rescued file can simply be dropped over `<folder>/state.json`: the
`data:` URIs inside it become files on the next save.

The outage shows even without a save attempt: the page knocks every five seconds via `HEAD`
and unfolds the block by itself after two misses.

## Markup is DOM

The small markdown set is built as DOM, not via `innerHTML`; a `<b onclick=…>` in the spec is
therefore visible text, not markup.

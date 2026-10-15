---
name: checklist
description: >
  Builds a local, tickable checklist page whose ticks, flags and notes can be
  read back afterwards. Use when the user wants a checklist, test plan,
  smoke-test or step-by-step run they tick off themselves — "gib mir eine
  checkliste", "zum abhaken", "/checklist" — or when handing manual verification
  steps to a human whose results are needed back afterwards. Also use when
  reading back a checklist handed over earlier: which items are ticked, which flagged, what was noted.
---

# Checklist

A tickable page whose state **you can read back later**. It runs on `127.0.0.1`, the save
button writes into a working folder, and you read from there. Nothing leaves the machine —
the webfaces are inlined from `fonts.css`, so the page makes no request of its own either.
Keep it that way: a checklist carries screenshots of unreleased work. (`refresh-fonts.py`
is the one script that goes online, and only when you want different typefaces.)

The page speaks the user's language, chosen at build time with `--lang`: `de` for German,
`en` (the default) for English. The chrome lives in `strings/<lang>.json`, `shell.html`
carries `{{t.key}}` where the text belongs. The two have to agree exactly — a key the table
has lost and a key nobody uses both abort the build, so a half-translated page cannot ship.
A language nobody has written yet means one more file beside those two, never an edit to
`shell.html`. Item text always comes from the spec, so it is in the user's language by
construction.

## When

- Manual steps only a human can carry out (GUI smoke, repro attempt, migration)
- Results you need afterwards in order to keep working
- **Not** for your own working list — that is what the todo tool is for

Precondition: the user sits at the same machine. For someone on another device this is no
good — pick a different handover form, don't bend this one.

## Building one

1. **Derive the content from the task, don't fill in boilerplate.** Every item needs the
   action, the checkable expectation and, where it helps, the stop criterion ("if that
   doesn't hold, stop here — the rest tests something else"). Order = a real dependency;
   otherwise don't imply a sequence.
   **Where an item has different prerequisites than the one before it — another app, another
   server, other files, test data that has to be created first — that belongs in its
   `setup`.** Not in the check text: that says what to do, not what has to hold first.
2. Write the spec (the format is documented at the top of `build.py`), then build and serve:

       FOLDER=~/dev/project/.manual-tests/2026-08-26-smoke
       python3 ~/.claude/skills/checklist/build.py spec.json $FOLDER --lang de
       python3 ~/.claude/skills/checklist/serve.py $FOLDER --open
       python3 ~/.claude/skills/checklist/read.py  $FOLDER   # later, to read it back

   `build.py` rejects unknown keys — a field the page does not know aborts the build instead
   of quietly disappearing. The note on an item is called `note` (singular); `notes` exists
   once only, at the top, on the document.

   **Put the working folder in the project, not in the scratchpad.**
   `.manual-tests/<date>-<topic>/` is short, greppable and survives a `/clear` — the
   scratchpad does not, and ticks, notes and images would go with it.

   Start `serve.py` **in the background**, otherwise it blocks. **Then read the URL it
   printed** — the default port is shared by every checklist, so a busy one sends the server
   to the next free port, and the URL you were about to hand over would have opened somebody
   else's list. Hand over the printed URL, together with the note that saving is required:
   without that click nothing reaches you.

   Put the working folder in `.gitignore` if the project tracks its neighbourhood —
   screenshots and a `state.json` full of notes are not commit material.
3. **The server stops itself**, because nobody remembers to. Fifteen minutes after the last
   request — the page knocks every few seconds while it is open, so silence means the tab is
   gone — and half an hour after `read.py` has taken the results, even with a tab still
   knocking. Nothing is lost either way: everything was written at save time, and the page
   carries the restart command. A save clears the read marker again, so a human who carries
   on ticking after your report keeps their server. `--idle 0 --read-idle 0` holds one open
   for a session that has to run all day.
4. For a different look, copy `shell.html`, pull colours and type towards the subject, and
   build with `--shell my-shell.html`.

## Setup per item

Every item can carry a `setup` — a block above the check text, on cooler ground and with an
edge of its own so it does not read like an instruction. The fields are in the `build.py`
docstring (`label`, `text`, `commands`, `links`, `paths`, `collapsed`); the judgment is here.

It stands **open** — unread prerequisites are the costliest mistake in a checklist.
`"collapsed": true` folds it away when a long list would otherwise get unwieldy.

The `label` says **how** this item differs ("Different server", "Fresh test data") — not just
"Setup". Under `collapsed` it is the only visible line. Items without prerequisites of their
own leave `setup` out.

## Test data

**Test data gets created, not assumed — and you create it, not the user.** Before handing
the checklist over, write every file the items need yourself (one `setup-data.sh` in the
working folder that builds the whole set, run by you). The user does not copy file-creating
commands out of the page; nine `printf … >` lines to paste is exactly the friction this rule
removes. What stays in `setup.commands` is only what must happen *at that moment* in the
run (an external change, a `git commit --amend`, launching the app on a file) — and even
those are bundled into one script per step where there is more than one line. This holds
for **every** item of the list, ticked ones included — when you rework an existing list, sweep
all items, not just the ones you were touching.

**Every item that uses a file gets a Finder button for it** (`paths`, absolute) — every
item, not just the first one that mentions the file. Nobody should have to remember where
item 3's file went when they reach item 40.

**Clean up afterwards.** Once the results are read back and the run is over, delete the test
data you created (the working folder's `data/`, `/tmp/<short-name>/`, throwaway repos) —
but never the working folder's `state.json` and `images/`, which are the results. Write the
cleanup into `setup-data.sh --clean` so a rerun and a teardown are one command each, and say
in your report that you cleaned up.

Where test data lands decides how fast anyone finds it:

| Kind | Where | How it opens |
|---|---|---|
| Whatever the browser can display (JSON, text, CSV, images, HTML, PDF) | `<folder>/data/` next to `state.json` | `links` to `http://127.0.0.1:<port>/data/<file>` — one click, new tab |
| Whatever the project needs on disk (fixtures, input files) | `.manual-tests/` in the project root | `paths` — the button reveals it in the Finder |
| Throwaway material nothing reuses | `/tmp/<short-name>/` | `paths`, or nothing at all |

**The working folder is the first choice** wherever it works at all: `serve.py` serves
everything under `data/` directly — subfolders included, so a case per folder is fine — the
link opens in the browser, and nobody has to go looking for a path. `.manual-tests/` is for data a build or a program has to read, and for
anything reused — short, greppable, easy to ignore in `.gitignore`.

`paths` needs **absolute** paths (`~` is expanded); the short `~` form is what gets shown.
The button sends `POST /reveal`; the server opens folders and only **reveals** files in the
Finder rather than launching them. Only paths written in the checklist are allowed —
otherwise any web page in the browser could talk the local server into opening anything. If
the path does not exist yet the button says so instead of staying silent: that means the user
has not run the create command yet.

## Markup in text

`title`, `detail`, `standfirst` and `setup.text` render a small markdown set: `**bold**`,
`` `code` `` and lines starting with `-`, `*` or `·` (they become bullets). Nothing else — no
headings, no links, no tables. Links belong in `setup.links`, commands in `setup.commands`.

It is built as DOM, not via `innerHTML`; a `<b onclick=…>` in the spec is therefore visible
text, not markup.

## States

| Token | Meaning |
|---|---|
| `[ ]` | open |
| `[x]` | fine |
| `[!]` | problem — fill in the note field |
| `[-]` | skipped — settled, will not be asked about again |

Clicking the marker advances it. Every item has a note field.

The right-hand corner of each card carries its number out of the total (`01 / 12`, `02 / 12`, …) so a conversation can
point at an item without anyone counting. It numbers the list order, not a dependency — if
the order isn't load-bearing, don't put it into the items' text either.

## Folding

The chevron on the right of an item folds it away: marker, title and number stay, `setup`,
detail, note and images go. The folded line keeps a trace of what is inside: the note's first
words and the image count.

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
stays folded, being settled. Nothing folds on load, and `"autofold": false` at the top of the
spec turns the automatic folding off for a list where it gets in the way.

`"collapsed": true` in the spec starts an item folded. Worth it for a long list whose first
half is groundwork — but never for an item whose `setup` carries prerequisites, because folding
hides those too. Folding is a view, not a result: it raises no unsaved warning and simply rides
along with the next save.

## Images and observations

Every item takes any number of images: Cmd-V into the note field, drag one in, or `+ Image`.
Below the list there is a free field, **Observations**, for anything that belongs to no single
item — with the same image mechanics.

On save each image becomes a file under `<folder>/images/`, named after its content: original
format, original size, no limit. The state then carries paths only, and `serve.py` sweeps away
attachments nobody names any more.

Both can be pre-filled from the spec: `"notes": "…"` at the top, `"images": […]` on the item.

## Reading it back

The foot of the page names its own folder, ready to copy. After a `/clear` the user gives you
that path and you carry on from there — that is all it takes. The same path is in `state.json`
under `meta.folder`; `serve.py` stamps it in fresh on every save, so the file says where it
belongs.

**Use `read.py <folder>`, don't query `state.json` by hand.** The script writes out every item
with its number, marker, note and image paths, and says at the top whether anything was ever
saved (`serve.py` stamps `meta.folder` in on every save — if it is missing, everything you see
is your own pre-fill). The reason for the script: reach into the JSON yourself and type
`notes` instead of `note`, and no error comes back, an empty answer does — and that reads like
"no notes". That is exactly how comments went missing twice.

    python3 ~/.claude/skills/checklist/read.py ~/dev/project/.manual-tests/2026-08-26-smoke

Images sit as files beside it; `read.py` names the absolute paths — **open them with `Read`**,
otherwise you do not know what is on them. An unopened image is not a finding. `--json` prints
the raw state when you genuinely need the whole document.

Reading also drops a `.read` marker into the folder — that is what lets a running `serve.py`
go: the results are out. It sets one only when something was actually saved, so a pre-fill
never arms a shutdown, and `--as-spec` sets none at all (that is a conversion, not a result).

`[-]` is settled, not open: the user has decided this item will not be run. Report it as
skipped, don't fold it back into the open ones and don't ask for it again — preventing that
second ask is the entire point of the marker. A reason in the note is welcome; its absence is
not a finding.

Then report: what is open, what stuck, what the notes and images show, and what follows from
it. Never claim someone ticked something without having read it. If everything looks unchanged
although the user says they saved: say so and read again — don't sell the old state as a
result. `serve.py` logs every save to stderr; that tells you whether one arrived at all.

## How saving works

`build.py` writes `template.html` (with `%%STATE%%` left open) and `state.json`. `serve.py`
fills the state in on every request; the save button PUTs it back to `/state`, the server
writes images as files and answers with the state including paths — the page adopts them
without re-rendering. It binds to `127.0.0.1` and nothing else. Every save also deletes
`.read`, so a checklist somebody is still working on cannot be timed out as read; so does a
rebuild, which opens a fresh round of ticking.

Rebuilding overwrites `state.json` and with it everything the user entered. To change the list
without losing their state, let `read.py` do the conversion:

    python3 ~/.claude/skills/checklist/read.py <folder> --as-spec > spec.json
    # edit spec.json — add, reword, reorder
    python3 ~/.claude/skills/checklist/build.py spec.json <folder> --lang de

`--as-spec` prints the state in the shape `build.py` reads, with ticks, notes and attachments
in place. **Rebuild into the same folder**: the images are carried over as paths, and the
files they name live there. Copying the raw `--json` output into a spec by hand does not
work — `meta.folder` alone aborts the build.

## When the server dies

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

**The page cannot start a process** — no button in the world brings `serve.py` back.

## Traps

| Trap | Consequence |
|---|---|
| `serve.py` started in the foreground | Blocks; start it in the background |
| The URL handed over without reading what `serve.py` printed | The default port was busy, the server moved — the user ticks off a stranger's checklist and saves it into a folder nobody reads |
| `serve.py` stopped while the user is still ticking | Saving fails; the rescue block holds the state, the window must not close |
| Working folder created in the scratchpad | After a `/clear` the ticks, notes and images are gone |
| `"collapsed": true` on an item that carries prerequisites | Folding hides `setup` as well — the human never sees what has to hold first |
| `--idle 0` used out of habit | The old problem is back: the server runs until the machine is rebooted |
| Rebuilding before reading | Ticks, notes and image references are gone |
| Rebuilding into a *different* folder from `--as-spec` | The image paths point at files that are not there |
| Working folder left out of `.gitignore` | Screenshots and notes land in the next commit |
| A literal `</script>` anywhere in a script — comments included | The parser ends the script right there, the rest turns into visible body text, nothing renders |
| `<` in the state JSON left unescaped | The first angle bracket in a note takes the page apart (`serve.py` escapes it as a unicode escape) |
| An image path in the report that was never opened with `Read` | You are reporting on a picture you have not seen |
| Generic steps ("test the feature") | The user cannot tell when something counts as passed |
| Prerequisites written into the check text instead of `setup` | The item reads like an instruction and the deviation goes unnoticed |
| `label` left as "Setup" although the item differs | Nobody can see that something else applies here |
| Test data assumed instead of created | The user guesses what is supposed to be there |
| File-creating commands handed to the user to paste | Busywork; create the data yourself before handing over |
| A file used by an item without a `paths` button | The user hunts for it in the Finder |
| Test data left behind after the run | Clutter in `/tmp` and the project; clean up with `setup-data.sh --clean` |
| A long path in body text instead of `paths` or `links` | They have to hunt for it by hand when a button would have done |
| A `file://` link in `links` | Dead from an `http` page; `build.py` rejects it |
| A `[-]` item reported as open, or asked about again | The user settled it on purpose — the second ask is exactly what the marker exists to prevent |
| Reporting the state from memory | A false statement — always run `read.py` first |
| `state.json` queried by hand with a mistyped key (`notes` for `note`) | An empty answer instead of an error — you report "no notes" while there are some |
| `--lang` forgotten for a user who works in another language | The chrome ships English by default — the page speaks past its reader |
| A rebuild from `--as-spec` without `--lang` | `autofold` rides along in the state, the language does not: the page comes back English |
| A `<link>` to a font or script CDN added to `shell.html` | Every page load tells that CDN somebody is looking at this checklist, and offline the page is unstyled |

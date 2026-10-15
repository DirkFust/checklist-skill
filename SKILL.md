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

The page speaks the user's language: `de` for German, `en` (the default) for English. Say it
once — `lang` in the project's `checklist.json` (see *Starting it* below) or in the
spec; `--lang` beats both. A `--merge` rebuild keeps the language the page already has unless
the spec names one, and `read.py --as-spec` writes it back into the spec. The chrome lives
in `strings/<lang>.json`, `shell.html`
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
   **Where an item has prerequisites — an app, a server, files, test data — that belongs in
   its `setup`.** Not in the check text: that says what to do, not what has to hold first.
2. Write the spec (the format is documented at the top of `build.py`), then build and serve:

       FOLDER=~/dev/project/.manual-tests/2026-08-26-smoke
       python3 ~/.claude/skills/checklist/build.py spec.json $FOLDER
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
docstring (`label`, `text`, `commands`, `links`, `paths`, `files`, `launch`, `collapsed`); the
judgment is here.

It stands **open** — unread prerequisites are the costliest mistake in a checklist.
`"collapsed": true` folds it away when a long list would otherwise get unwieldy.

The `label` says **how** this item differs ("Different server", "Fresh test data") — not just
"Setup". Under `collapsed` it is the only visible line. Items without prerequisites of their
own leave `setup` out.

**A setup that more than one item shares gets a name.** `setups` at the top of the spec holds
it once (`"app": {…}`), an item says `"setup": "app"`. Write the name on **every** item that
needs it, not only where it changes: an item whose setup equals the one of the item before
folds the block to one line, `<label> · same as 03`, by itself — the repeat costs the reader
nothing, and an item reached out of order still says what has to hold. An inline object stays
right for a one-off.

## Starting it: ▶

A setup with a `launch` gets a ▶ button, so the human does not copy build commands out of the
page. A press runs **build → stop → quit → run**: `build` first — if it fails, the page shows
the output tail and whatever was running keeps running (the message says so); then the run an
earlier ▶ of this server started goes down, its whole process group; `quit` for an app outside that group (a
failure is passed by, it may not have been running); `run`, detached — exited 0 within 3 s is
*done* (an app that handed itself over), alive is *running*. With a `url` it waits until that
answers HTTP, then opens it; a url that never answers fails the launch and stops the run. One
live run per server. `build` and `quit` write into `launch.log`
in the working folder, the run into `launch-run.log` — read those before guessing when a human
says ▶ failed. The server's idle stop ends what ▶ started too, so
a dev server goes down with the checklist.

**The launch belongs to the project, not to each spec.** `checklist.json`, found walking up
from the working folder's parent (the walk stops at the repository root or `$HOME`; outside
`$HOME` it happens only inside a repository), holds what every checklist of the project shares:
`lang`, `launch` and named `launches` profiles. One that is not yours, or that group or others
may write to, is skipped with a warning — it names commands ▶ runs.
Write it the first time a project gets a checklist — a native app, then a web project:

    {"lang": "de",
     "launch": {"quit": "osascript -e 'quit app \"Diff\"'", "build": "make app",
                "run": "open -a build/Diff.app {files}"}}

    {"launch": {"run": "npm run dev", "url": "http://localhost:5173"},
     "launches": {"preview": {"build": "npm run build", "run": "npm run preview",
                              "url": "http://localhost:4173"}}}

A setup then needs nothing (it gets the default `launch`), or names a profile:
`"launch": "preview"`. The spec's `launch` and `launches` override the project's field by
field, `null` switches a step off; fields and merge order are in the `build.py` docstring.

Two placeholders: `{files}` — the setup's files, absolute, quoted, space-separated — and
`{data}`, the working folder's `data/`. Both arrive quoted already — write `open {files}`, never
`open "{files}"`: the extra quotes glue every file into one argument, or break a path with a
space. Any other bare `{word}` aborts the build, so a typo
never reaches the shell; `${VAR}`, `{}` and `{print $1}` pass through. Shell code that needs a
lone word in braces gets spaces: `awk '{ print }'`.

**What ▶ runs is decided at build time.** `build.py` freezes every setup — commands, paths,
files, launch — into `setups.json`. The page sends a setup id and nothing else; the server runs
what the build froze and refuses requests whose `Host` is not itself. That leaves the spec as
the only source of a command — and you, its author, responsible for what ▶ runs. No secrets in
commands: they land in `setups.json` and the launch logs.

## Test data

**Test data gets created, not assumed — and you create it, not the user.** The way to do it is
`data` in the spec: each entry becomes a file under `<folder>/data/`, rewritten on every build,
with an untouched copy beside it. A string is written verbatim; an object sets `encoding`,
`eol` and `bom` — exactly what a test about encodings or line ends needs and what nobody gets
right with `printf`. `copy` takes a real file in at build time, `base64` takes raw bytes.

A setup's `files` names what its item works on: names from `data`, or absolute paths to files
elsewhere. Each gets a row in the page — badges for what is unusual (encoding, BOM, CRLF, no
final newline), **Show** for a preview with visible whitespace, a Finder button. Files from
`data` also get **Reset files**, so a destructive step can be run twice without asking you for
fresh data; files elsewhere are never written. `{files}` hands them all to `run`.

`setup-data.sh` in the working folder is only for what a spec cannot hold — a git repository
with history, a huge file, anything generated — and you run it before handing over: nine
`printf … >` lines for the user to paste is exactly the friction this removes. `commands`
keep only what must happen *at that moment* in the run (an external change, a `git commit
--amend`), one script per step where there is more than one line. This holds for **every**
item, ticked ones included — when you rework a list, sweep all of them.

**Every item that uses a file shows it** — in `files`, or as a `paths` button for folders and
whatever lives elsewhere — every item, not just the first one that mentions it. Nobody should
have to remember where item 3's file went when they reach item 40.

**Clean up afterwards.** Once the results are read back and the run is over,
`build.py --clean <folder>` removes `data/` and `.pristine/` and leaves `state.json` and
`images/` — the results — alone. What a script made elsewhere (`/tmp/<short-name>/`, throwaway
repos) goes with its own `--clean`. Say in your report that you cleaned up.

| Kind | Where | How it opens |
|---|---|---|
| Anything a spec can describe | `data` → `<folder>/data/` | `files` (preview, reset, Finder), or `links` to `http://127.0.0.1:<port>/data/<file>` for the browser |
| Whatever the project needs on disk and reuses (fixtures) | `.manual-tests/` in the project root | `files` or `paths` |
| Generated throwaway material | `/tmp/<short-name>/` | `paths`, or nothing at all |

`paths` needs **absolute** paths (`~` is expanded); the short `~` form is what gets shown.
The server opens plain folders and only **reveals** files — and packages: any folder with a
suffix (`*.app`, `*.rtfd`, `*.xcodeproj`, …) or a `Contents/Info.plist` — in the Finder rather
than launching them,
and only paths the build froze — otherwise any web page in the browser could talk it into
opening anything. A path that does not exist yet says so: the data was never created.

## Markup in text

`title`, `detail`, `standfirst` and `setup.text` render a small markdown set: `**bold**`,
`` `code` `` and lines starting with `-`, `*` or `·` (they become bullets). Nothing else — no
headings, no links, no tables. Links belong in `setup.links`, commands in `setup.commands`.
It is built as DOM, so HTML in the spec shows as text.

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

**A ticked item folds itself away after two seconds**, unless pointer or caret still rest on
it; a further click on the marker disarms the fold, `[!]` unfolds again. `"autofold": false`
at the top of the spec turns it off for a list where it gets in the way. Timer and scroll
compensation: [reference.md](reference.md).

`"collapsed": true` in the spec starts an item folded. Worth it for a long list whose first
half is groundwork — but never for an item whose `setup` carries prerequisites, because folding
hides those too. Folding is a view, not a result: it raises no unsaved warning and simply rides
along with the next save.

## Images and observations

Every item takes any number of images: Cmd-V into the note field, drag one in, or `+ Image`.
Below the list there is a free field, **Observations**, for anything that belongs to no single
item — with the same image mechanics. On save each image becomes a file under
`<folder>/images/`; the state carries paths only.

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

`[-]` is settled, not open: the user has decided this item will not be run. Report it as
skipped, don't fold it back into the open ones and don't ask for it again — preventing that
second ask is the entire point of the marker. A reason in the note is welcome; its absence is
not a finding.

Then report: what is open, what stuck, what the notes and images show, and what follows from
it. Never claim someone ticked something without having read it. If everything looks unchanged
although the user says they saved: say so and read again — don't sell the old state as a
result. `serve.py` logs every save to stderr; that tells you whether one arrived at all.

## Changing a list

**A folder a human has saved into refuses a plain rebuild** — it holds their results. To change
a list they have started:

- **`--merge`** is the normal way: rebuild from the edited spec, and every new item takes
  state, note, images and fold from the old item with the identical title. Old items no title
  claims are listed on stderr — read that list, a reworded title lands there.
- **A rewrite that keeps everything**, reworded items included: let `read.py` convert, edit,
  then overwrite — the spec itself carries ticks, notes, setups, data and `lang`:

      python3 ~/.claude/skills/checklist/read.py <folder> --as-spec > spec.json
      # edit spec.json — add, reword, reorder
      python3 ~/.claude/skills/checklist/build.py spec.json <folder> --force

**Rebuild into the same folder** either way: the images are carried over as paths, and the
files they name live there. Copying the raw `--json` output into a spec by hand does not
work — `meta.folder` alone aborts the build.

## When the server dies

A failed save — or two missed knocks — unfolds a rescue block in the page: the state stays in
the browser until the window closes, can be saved as a file (the way once images are
involved) or copied, and the block names the full restart command. After the restart another
click on Save is enough; a rescued file can be dropped over `<folder>/state.json`. Details:
[reference.md](reference.md).

**The page cannot start `serve.py`** — ▶ starts what a setup names, but only through a running
server; no button brings the server itself back.

## Traps

| Trap | Consequence |
|---|---|
| `serve.py` started in the foreground | Blocks; start it in the background |
| The URL handed over without reading what `serve.py` printed | The default port was busy, the server moved — the user ticks off a stranger's checklist and saves it into a folder nobody reads |
| `serve.py` stopped while the user is still ticking | Saving fails; the rescue block holds the state, the window must not close |
| Working folder created in the scratchpad | After a `/clear` the ticks, notes and images are gone |
| `"collapsed": true` on an item that carries prerequisites | Folding hides `setup` as well — the human never sees what has to hold first |
| `--idle 0` used out of habit | The old problem is back: the server runs until the machine is rebooted |
| `--force` used to get past the rebuild refusal, without `--as-spec` | Ticks, notes and image references are gone |
| `--merge` after rewording titles | The reworded items start empty — stderr names what nothing claimed |
| Rebuilding into a *different* folder from `--as-spec` | The image paths point at files that are not there |
| Working folder left out of `.gitignore` | Screenshots and notes land in the next commit |
| A literal `</script>` anywhere in a script — comments included | The parser ends the script right there, the rest turns into visible body text, nothing renders |
| An image path in the report that was never opened with `Read` | You are reporting on a picture you have not seen |
| Generic steps ("test the feature") | The user cannot tell when something counts as passed |
| Prerequisites written into the check text instead of `setup` | The item reads like an instruction and the deviation goes unnoticed |
| `label` left as "Setup" although the item differs | Nobody can see that something else applies here |
| A named setup written only on the first item of a run | An item reached out of order lacks its prerequisites; write the name on each — the repeat folds itself |
| `"setups": {}` left in a spec that uses the short text form | Every string `setup` turns into a reference to a name that does not exist, and the build aborts |
| File-creating commands handed to the user to paste | Busywork; put the files into `data`, or run a script yourself before handing over |
| A file used by an item without `files` or `paths` | The user hunts for it in the Finder |
| Test data left behind after the run | Clutter in the folder, `/tmp` and the project; `build.py --clean`, plus the script's own cleanup |
| `"{files}"` or `'{data}'` — a placeholder wrapped in quotes | They are quoted already; the extra pair turns all files into one argument or splits a path with a space |
| `{file}` or any other typo in braces in a launch command | The build aborts — only `{files}` and `{data}` exist; a word in shell braces needs spaces |
| A one-off profile put into `checklist.json` | Every checklist of the project sees it; a profile for one list belongs in the spec's `launches` |
| A dev server started with ▶, then the checklist server idles out | The dev server goes down with it — restart `serve.py` and press ▶ again |
| A secret in a launch command | Frozen into `setups.json`, echoed into `launch.log` / `launch-run.log` |
| A long path in body text instead of `paths` or `links` | They have to hunt for it by hand when a button would have done |
| A `[-]` item reported as open, or asked about again | The user settled it on purpose — the second ask is exactly what the marker exists to prevent |
| Reporting the state from memory | A false statement — always run `read.py` first |
| `state.json` queried by hand with a mistyped key (`notes` for `note`) | An empty answer instead of an error — you report "no notes" while there are some |
| No `lang` anywhere for a user who works in another language | The chrome ships English by default — the page speaks past its reader; put `lang` into `checklist.json` once |
| A `<link>` to a font or script CDN added to `shell.html` | Every page load tells that CDN somebody is looking at this checklist, and offline the page is unstyled |

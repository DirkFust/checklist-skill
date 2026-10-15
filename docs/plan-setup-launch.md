# Plan: named setups, test data, ▶ launch

Goal: per-item setup stops being copy-paste busywork. A setup is written once,
names its files, and the page can **start** it (▶), **reset** its files and
**show** what is in them. Generic — a native app (dft: quit, `make app`, open two
files) and a web project (`npm run dev`, wait for the URL, open it) must both fit
without special cases.

Context: no tests existed before this plan. Every slice adds stdlib `unittest`
tests under `tests/` (`python3 -m unittest discover -s tests` from the skill
root). No third-party dependencies. Each slice ends with the full suite green and
one conventional commit in this folder's git repo.

## Decisions (binding for all slices)

### Spec additions (top level)

- `lang`: chrome language, persisted into `meta.lang`. Precedence: `--lang` CLI >
  spec `lang` > project config `lang` > `"en"`. `read.py --as-spec` emits it.
- `setups`: object `name -> setup`. A setup has the existing keys (`label`,
  `text`, `commands`, `links`, `paths`, `collapsed`) plus `files` and `launch`.
  Name must match `^[a-z0-9][a-z0-9_-]*$`.
- `data`: object `relative name -> entry`, materialized into `<folder>/data/`.
  Name: `/`-separated segments, each `^[0-9a-zA-Z][0-9a-zA-Z._-]*$`. Entry forms:
  - string → UTF-8 text, written verbatim (no newline added or removed)
  - `{"text", "encoding"?, "eol"?, "bom"?}` — `encoding` a Python codec name
    (default `utf-8`); `eol` one of `lf|crlf|cr` (converts `\n` in `text`);
    `bom: true` prepends the BOM of `utf-8`/`utf-16-le`/`utf-16-be`/`utf-32-*`
    (`utf-16`/`utf-32` without endianness: abort, ambiguous)
  - `{"copy": "<absolute path or ~>"}` — file copied at build time
  - `{"base64": "..."}` — raw bytes
- `launch`: object, see below; overrides the project config field by field.
- `launches`: named launch profiles, see below.

### Item `setup`

- object → inline setup (as today, plus `files`/`launch`)
- string → if the spec has `setups`: a reference; unknown name aborts the build
  listing the known ones. If the spec has no `setups`: the old short form (text).
- Inline setups get the id `item-NN` (two digits, 1-based); named ones keep their name.

### `files` (on a setup)

List of strings. A name present in `data` → managed file under `<folder>/data/`.
An absolute or `~` path → external file (never written, never reset). Anything
else aborts. Resolved per file: `{"name", "path" (absolute), "managed": bool,
"encoding"?}`. Managed files' paths join the reveal whitelist automatically.

### `launch`

Fields, all optional, all strings except `ready_timeout` (seconds, int, default 60):

| Field | Meaning |
|---|---|
| `cwd` | working directory for every step; `~` expanded; must be absolute; default: the project root (dir of the project config, else the folder's parent) |
| `quit` | run first, awaited (timeout 30 s); failure is logged, not fatal |
| `build` | awaited (timeout 15 min); non-zero exit → failed, output tail shown |
| `run` | spawned detached (`start_new_session=True`); required for ▶ to exist |
| `url` | if set: after `run`, poll until an HTTP connection succeeds (any status), then `open` it; process death or timeout → failed |

Placeholders in `quit`/`build`/`run`/`url`: `{files}` (all files of the setup,
absolute, `shlex.quote`d, space-separated; in `url` not allowed), `{data}` (the
data dir, quoted in commands, raw in `url`). Any other `{…}` aborts the build.
Commands run via `/bin/sh -c`.

Profiles: a project can start in more than one way (dft: diff vs. editor; web:
dev server vs. preview build). `launches` — in the project config and the spec —
is an object `profile name -> launch object` (same fields; name
`^[a-z0-9][a-z0-9_-]*$`). Spec `launches` merges over project `launches` per
profile name, field by field. The single `launch` stays the default for setups
that choose no profile. A setup's `launch` is an object (overrides), a string
(a profile name), or an object with a `"profile"` key plus overrides; `profile`
is allowed nowhere else. An unknown profile aborts, listing the known ones. A
spec profile no setup chooses warns on stderr; project profiles never warn
(they serve every checklist of the project, each uses only some).

Merge: project config `launch` < spec `launch` < chosen profile (project + spec
merged) < setup `launch` fields. A key set to `null` or `""` at a higher level
disables that step. A setup without a resulting `run` has no ▶.

### Project config

`checklist.json`, found by walking up from the working folder's parent; the
first one wins; the walk stops at a directory containing `.git` (inclusive) or at
`$HOME`. Keys: `launch`, `launches`, `lang`. Unknown keys abort.

### Frozen setups (security)

`build.py` writes `<folder>/setups.json`: `{"setups": {id: resolved}, "source":
{...}}`. `resolved` holds the display fields, resolved `files`, resolved `launch`
(placeholders substituted, `cwd` absolute) or `null`. `source` holds what
`--as-spec` needs to rebuild the spec (`launch`, `launches`, `data`, `setups` raw, and the raw
inline setups by id).

- `state.json` items carry `"setup": "<id>"` or `null` — never the object.
- `serve.py` GET `/` injects the state with each item's `setup` replaced by
  `{"id": id, **resolved}` (so old shells still render label/text/commands).
- PUT `/state` **ignores** the client's `setup` and keeps the on-disk id by item
  index (the page cannot add, remove or reorder items).
- Reveal whitelist, `/launch`, `/reset`, `/peek` read only `setups.json`.
- Every request: the `Host` header must be `127.0.0.1:<port>` or
  `localhost:<port>`, else 403 (DNS rebinding). All mutating POSTs additionally
  keep the existing `X-Checklist` header + `Sec-Fetch-Site` guard.
- Old folders without `setups.json` (state carries setup objects): serve them as
  before, read-only for setups (reveal from state as today). No migration needed.

### Rebuild protection

`build.py` into a folder whose `state.json` has `meta.folder` (= saved by a
human) aborts unless `--merge` or `--force`. `--merge`: for each new item, an old
item with the identical title passes on `state`, `note`, `images`, `collapsed`;
old items without a match are listed on stderr (not silently dropped).
`--force`: overwrite. `--clean`: remove `data/` and `.pristine/` only, then exit
(keeps `state.json`, `images/`).

### Server endpoints (new)

- `POST /launch` `{"setup": id}` → 202; runs in a thread. Only one launched
  process per server: a new ▶ first terminates the previous `run` process group
  (SIGTERM, 3 s, SIGKILL). Steps: quit → build → run → (url wait → open).
  Output of all steps appended to `<folder>/launch.log`.
- `GET /launch?setup=id` → `{"phase": idle|quitting|building|starting|waiting|running|done|failed, "message", "tail": [last 20 log lines of this launch], "url"}`.
  `done` = run exited 0 within 3 s (app detached itself, e.g. dft);
  `running` = still alive after 3 s (and url reachable if set).
- `POST /reset` `{"setup": id}` → rewrites the setup's managed files from
  `<folder>/.pristine/` (written by build.py). 200 `{"reset": [names]}`.
- `GET /peek?setup=id&file=<index>` → `{"name","size","encoding","bom","eol":
  "lf|crlf|cr|mixed|none","final_newline": bool,"binary": bool,"lines": [...],
  "truncated": bool}` — at most 400 lines; decode with the declared encoding,
  else UTF-8; undecodable → `binary: true`, no lines.
- On shutdown the server terminates its running process group.

### Page (shell.html)

In the setup block:
- ▶ button (only if `launch`) + status line, polling `GET /launch` every second
  while not idle/done/failed; on failed shows the tail in a `<pre>`.
- One row per file: name, badges (encoding if not utf-8, BOM, CRLF/CR/mixed,
  "no newline at end"), a "Show" toggle that fetches `/peek` and renders the lines
  with visible whitespace (tab `→`, line end `¬`, CR `␍`), and a reveal button.
- "Reset files" button if any file is managed.
- An item whose setup id equals the id of the nearest previous item with a
  setup renders its setup block collapsed, summary `<label> · <same_as> NN`.
- All new chrome strings in `strings/en.json` and `strings/de.json`.

## Slices

1. **Tests & security** — test harness; `setups.json` freezing for the existing
   inline setups; state carries ids; GET injects; PUT ignores client setup;
   reveal from frozen; Host check; old-folder compatibility.
2. **Named setups, language, rebuild protection** — `setups` + string
   references; `lang` precedence and persistence; project config discovery
   (`lang`, and `launch` parsed/validated/merged into the frozen file — no
   execution yet); `--merge`/`--force`; `--as-spec` round trip incl. `setups`,
   `launch`, `lang`.
3. **Test data & preview** — `data` materialization + `.pristine/`; `files`;
   `--clean`; `/reset`; `/peek`; page file rows, preview, reset button; strings.
4. **▶ launch** — `/launch` engine, status, url wait, process-group handling,
   shutdown cleanup; page ▶ + status + "same as" collapse; strings.
5. **SKILL.md** — document the new spec surface and judgment, shorten what the
   code now enforces (traps rows for `--lang` on rebuild, rebuild-before-read,
   test-data-assumed); keep `setup-data.sh` only for generated data.

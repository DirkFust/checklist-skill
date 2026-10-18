# checklist

A Claude Code skill for manual tests. Claude writes the checklist, you tick it off in your
browser, and Claude reads the result back: every tick, `[!]` problem, note and screenshot.

Use it for steps only a human can do: a GUI smoke test, a repro attempt, a migration you
have to watch.

## Install

```
/plugin marketplace add DirkFust/checklist-skill
/plugin install checklist@checklist-skill
```

Restart Claude Code. Then ask for a checklist ("give me a checklist to test this") or run
`/checklist:checklist`.

**Requirements:** Python 3.10+ (stdlib only), macOS or Linux, a browser. git is optional.
With it, ▶ skips a build when nothing has changed since the last one.

## How it works

1. Claude writes a spec and builds it into a working folder in your project
   (`.manual-tests/<date>-<topic>/`).
2. A small server serves the page on `127.0.0.1` only. Saving writes the state to that folder.
3. Claude runs `read.py` on the folder to read the results, even after a `/clear`.

Nothing leaves your machine. The page loads no remote assets, and its fonts are inlined.

What a checklist can do:

- **Setup per item:** prerequisites (files, paths, links, commands) shown above the check.
- **▶ Launch:** a button that builds and starts the app or server an item needs.
- **Test data:** files created fresh on every build, with exact encoding, line endings and BOM.
- **Images:** paste or drag screenshots into any item, or into the free *Observations* field.
- English and German pages.

The server stops itself once the page is closed or the results have been read.

## Development

```
python3 -m unittest discover -s tests   # needs Python 3.11+
uvx ruff check .
```

The skill lives in `plugins/checklist/skills/checklist/`. `SKILL.md` is the guide Claude
follows. The spec format is documented at the top of `build.py`.

## License

MIT

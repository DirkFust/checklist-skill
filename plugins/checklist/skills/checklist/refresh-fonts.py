#!/usr/bin/env python3
"""Rebuilds `fonts.css` from Google Fonts — the only step here that goes online.

    refresh-fonts.py [--out fonts.css]

The page itself must never reach the network, so the faces are fetched once,
base64'd into `fonts.css` and inlined by `build.py` from then on. Run this only
to change or refresh the typefaces; the result is a normal file to commit.

Only the `latin` subset is kept. Glyphs outside it fall back per glyph to the
stack in `shell.html`, so a missing arrow costs a slightly different shape and
nothing else — whereas carrying every subset would triple the file.
"""

import argparse
import base64
import pathlib
import re
import urllib.request

CSS = ("https://fonts.googleapis.com/css2?"
       "family=Public+Sans:wght@400;600&family=JetBrains+Mono:wght@400;600&display=swap")
# Google serves woff2 only to a browser-shaped agent; anything else gets ttf.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADER = """/* Public Sans and JetBrains Mono, latin subset, inlined as woff2.
 *
 * The page must not reach the network: a checklist carries screenshots and
 * notes about unreleased work, and `serve.py` binds to 127.0.0.1 precisely so
 * nothing leaves the machine. A <link> to fonts.googleapis.com would have
 * broken that promise on every page load — and left the page unstyled on a
 * machine that is offline.
 *
 * `build.py` inlines this file into the document head. Glyphs outside the
 * latin range fall back to the stack in `shell.html` per glyph, which is why
 * the subset can stay small.
 *
 * Both faces are SIL Open Font License 1.1. Regenerate with `refresh-fonts.py`
 * — do not hand-edit.
 */"""


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as answer:
        return answer.read()


def faces(css: str) -> list[str]:
    """The latin blocks, with their woff2 url swapped for a data: URI."""
    out = []
    for subset, block in re.findall(r"/\*\s*([a-z-]+)\s*\*/\s*(@font-face\s*\{[^}]*\})", css):
        if subset != "latin":
            continue
        url = re.search(r"src:\s*url\((https://[^)]+)\)", block).group(1)
        uri = "data:font/woff2;base64," + base64.b64encode(fetch(url)).decode("ascii")
        fields = [line.strip() for line in re.search(r"\{(.*)\}", block, re.DOTALL).group(1)
                  .strip().splitlines() if line.strip()]
        fields = [field.replace(url, uri) for field in fields]
        out.append("@font-face {\n" + "\n".join("  " + field for field in fields) + "\n}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(pathlib.Path(__file__).with_name("fonts.css")))
    args = parser.parse_args()

    blocks = faces(fetch(CSS).decode("utf-8"))
    if not blocks:
        raise SystemExit("no latin faces in the stylesheet — did the API change?")
    target = pathlib.Path(args.out)
    target.write_text(HEADER + "\n" + "\n".join(blocks) + "\n", encoding="utf-8")
    print(f"{target} — {len(blocks)} faces, {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()

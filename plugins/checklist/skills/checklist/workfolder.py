"""What a built checklist folder holds, shared by build.py, serve.py and read.py.

One copy of the names and of the one check that keeps a write inside the test
data: two copies of a symlink check drift apart, and the one that falls behind
is the one that writes through a planted link.

Imported as a sibling: the scripts run by path, so their own folder is
sys.path[0]; the tests put it there themselves.
"""

import codecs
import pathlib
import re

# Written by build.py, read by serve.py and read.py; absent in folders built
# before setups were frozen.
SETUPS_FILE = "setups.json"
# read.py drops this beside state.json once it has taken the results; serve.py
# goes when it sees it, and a rebuild clears it.
READ_MARK = ".read"
# Where build.py writes a spec's test data.
DATA_DIR = "data"
# The untouched copy of every file the build wrote into data/ — what /reset
# copies back, and the record of which files there are the build's own.
PRISTINE_DIR = ".pristine"
# One segment of a data name: nothing that climbs out (`..`) or hides (`.x`).
SEGMENT_RE = re.compile(r"^[0-9a-zA-Z][0-9a-zA-Z._-]*$")
DEFAULT_ENCODING = "utf-8"
# By codec name as codecs.lookup spells it, longest first: a UTF-32-LE mark
# starts with the UTF-16-LE one, so whoever matches a mark must try it first.
# The bare utf-16/utf-32 are missing on purpose: their byte order is whatever
# this machine has.
BOMS = {"utf-32-le": codecs.BOM_UTF32_LE, "utf-32-be": codecs.BOM_UTF32_BE,
        "utf-8": codecs.BOM_UTF8,
        "utf-16-le": codecs.BOM_UTF16_LE, "utf-16-be": codecs.BOM_UTF16_BE}


def contained(base: pathlib.Path, name: str) -> pathlib.Path | None:
    """`base/name` if a write there stays inside `base`, else None: a name that
    could climb out, a file that is a symlink, a folder on the way (or `base`
    itself) that resolves elsewhere — a write would land on whatever the link
    points at. The path need not exist yet; the caller says what None means."""
    if not all(SEGMENT_RE.match(part) for part in name.split("/")):
        return None
    # Only the folder above `base` is resolved: `base` itself must not be a link.
    home = base.parent.resolve() / base.name
    path = home / name
    if home.is_symlink() or path.is_symlink() or not path.parent.resolve().is_relative_to(home):
        return None
    return path


def replace_file(path: pathlib.Path, content: bytes) -> None:
    """A fresh file rather than a write into the old one: a hard link would
    carry the write to its other name, outside the folder."""
    path.unlink(missing_ok=True)
    path.write_bytes(content)

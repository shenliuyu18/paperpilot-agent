"""Console-safe stdout for PaperPilot's CLI scripts.

These scripts echo runtime text — a provider reply, a PDF heading, an error detail — into a stream opened with the
OS locale codec. On a Chinese Windows console that is GBK, which has no `•`, so `scripts/acceptance.py` aborted at
check 82 of 94 with UnicodeEncodeError: a passing build looked like a failing one.

`make_console_safe()` changes only the *error handler*, and only where it can still raise. The encoding is left
alone on purpose (forcing UTF-8 would render Chinese as mojibake in that console), and stderr is left alone because
Python already opens it with `backslashreplace`, which is lossless and reversible.

"surrogateescape" is the trap: Windows opens a redirected stdout with it, and it round-trips undecodable *bytes* but
raises on any character the codec cannot encode — `•` included. Only `replace`, `ignore` and `backslashreplace` are
safe handlers, so anything else is switched to `replace`.

Scripts are executed as files (`python scripts/<name>.py`), which puts this directory first on `sys.path`. If a
script ever has to be imported as `scripts.<name>`, use the two-form import in `scripts/mock_llm_server.py`.
"""
from __future__ import annotations

import sys


#: Error handlers that never raise on a character the console codec cannot encode.
SAFE_HANDLERS = frozenset({"replace", "ignore", "backslashreplace"})


def make_console_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        if getattr(stream, "errors", None) in SAFE_HANDLERS:
            continue
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def make_json_stdout() -> None:
    """Write a machine-readable JSON document as UTF-8, whatever the console says.

    These payloads carry real titles, quotes and replies (`ensure_ascii=False`), and `PYTHONIOENCODING` outranks
    `PYTHONUTF8`, so on a Chinese Windows console a redirected `--json` document would otherwise land in the pipe
    as GBK bytes the consumer cannot decode. Human-readable output keeps the console encoding; data does not.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass

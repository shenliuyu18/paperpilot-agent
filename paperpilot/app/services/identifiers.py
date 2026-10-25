"""Validation for identifiers that get joined onto a data directory.

Every one of these ids arrives from a URL segment or from an imported archive, and Windows treats a
backslash as a path separator: before this existed, ``GET /api/paper/..\\llm_settings.secrets/parsed``
returned the local install's stored API key as JSON — readable by any web page, because CORS is
``*`` and the request is a simple GET. The same shape let an imported dossier write ``*.json`` files
outside the directory they belonged to.

Real ids are narrow by construction (``2609.07059v3``, ``upload-0017adae``, ``crossref-10-1109-…``),
so a strict pattern costs nothing: it rejects separators, traversal, empty and oversized values, and
non-ASCII, and accepts everything the product actually produces.
"""
from __future__ import annotations

import re

MAX_ID_LENGTH = 120
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,%d}$" % (MAX_ID_LENGTH - 1))


def is_safe_id(value: object) -> bool:
    """True when ``value`` can be used as a single file-name component."""
    return isinstance(value, str) and bool(_SAFE_ID.fullmatch(value))


def require_safe_id(value: object, kind: str = "标识符") -> str:
    """Return ``value`` when it is safe, otherwise raise ``ValueError`` for the caller to map."""
    if not is_safe_id(value):
        raise ValueError(f"非法{kind}：只能包含字母、数字、点、下划线和连字符，且不超过 {MAX_ID_LENGTH} 个字符。")
    return value

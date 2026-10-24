"""Optional local OCR for image-only PDFs.

The product can parse selectable-text PDFs without any external service. A
scanned PDF has no text layer, so this module renders pages with PyMuPDF and
calls the system ``tesseract`` binary when it is available. Nothing is sent to
a cloud provider; if Tesseract is missing the upload path keeps its explicit
OCR hint instead of returning an empty paper.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

DEFAULT_LANGS = "eng+chi_sim"
DEFAULT_MAX_PAGES = 80
DEFAULT_DPI = 200


def _binary() -> str:
    configured = str(os.getenv("PAPERPILOT_TESSERACT", "") or "").strip()
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
    ]
    return next((item for item in candidates if Path(item).is_file()), "")


def enabled() -> bool:
    return str(os.getenv("PAPERPILOT_OCR", "auto")).strip().lower() not in {
        "0", "off", "false", "disabled",
    } and bool(_binary())


def status() -> dict:
    binary = _binary()
    mode = str(os.getenv("PAPERPILOT_OCR", "auto")).strip().lower()
    return {
        "enabled": bool(binary) and mode not in {"0", "off", "false", "disabled"},
        "mode": mode or "auto",
        "languages": str(os.getenv("PAPERPILOT_OCR_LANGS", DEFAULT_LANGS)),
        "max_pages": int(os.getenv("PAPERPILOT_OCR_MAX_PAGES", str(DEFAULT_MAX_PAGES)) or DEFAULT_MAX_PAGES),
    }


def _ocr_page(binary: str, image: Path, languages: str) -> str:
    commands = [languages]
    if languages != "eng":
        commands.append("eng")
    for langs in commands:
        try:
            result = subprocess.run(
                [binary, str(image), "stdout", "-l", langs],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return ""


def extract_pages(doc: pymupdf.Document, max_pages: int | None = None) -> list[str]:
    """Return OCR text for the first ``max_pages`` pages, or [] if unavailable."""
    if not enabled():
        return []
    binary = _binary()
    limit = max(1, min(int(max_pages or os.getenv("PAPERPILOT_OCR_MAX_PAGES", DEFAULT_MAX_PAGES)), doc.page_count))
    languages = str(os.getenv("PAPERPILOT_OCR_LANGS", DEFAULT_LANGS))
    pages: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pp-ocr-") as temp:
        directory = Path(temp)
        for index in range(limit):
            page = doc[index]
            image = directory / f"page-{index + 1}.png"
            try:
                pixmap = page.get_pixmap(dpi=DEFAULT_DPI, alpha=False)
                pixmap.save(str(image))
            except (RuntimeError, ValueError, OSError):
                pages.append("")
                continue
            pages.append(_ocr_page(binary, image, languages))
    return pages
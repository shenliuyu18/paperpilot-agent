from __future__ import annotations

"""Build PaperPilot_Project.zip: an allowlisted, credential-free delivery package.

Track 1 only — the SVD/track-2 directories at the workspace root are never
included, and runtime data (library, uploads, agent runs, projects, caches,
glossaries, search history, secrets) is excluded apart from the two demo papers.
"""

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "PaperPilot_Project.zip"

TOP_FILES = ["README.md", "CHANGELOG.md", ".gitignore"]
TOP_DIRS = ["paperpilot", "demo", "showcase", "deploy", "modelscope_space"]

EXCLUDE_DIR_NAMES = {
    ".git", ".secrets", ".wrangler", ".bench", ".ruff_cache", "__pycache__",
    "node_modules", "out", "out_qa", "uploads", "glossaries", "agent_runs",
    "projects", "translations", "citations",
}
EXCLUDE_FILE_NAMES = {
    ".env", "agent_runs.json", "projects.json", "library.json",
    "custom_sources.json", "custom_sources.secrets.json", "history.json",
    "search_history.json", "glossary.json", "llm_calls.json",
    "llm_settings.json", "llm_settings.secrets.json",
}
EXCLUDE_SUFFIXES = {".pyc", ".log", ".tmp"}
DATA_KEEP = {
    "data/eval_set.json",
    "data/eval_results.json",
    "data/cache/crossref_fixture.json",
    "data/demo_pdfs/2606.08947v1.pdf",
    "data/demo_pdfs/2609.24288v1.pdf",
    "data/parsed/2606.08947v1.json",
    "data/parsed/2606.08947v1.txt",
    "data/parsed/2609.24288v1.json",
    "data/parsed/2609.24288v1.txt",
}
FORBIDDEN_PREFIXES = (
    "paperpilot/data/agent_runs",
    "paperpilot/data/projects",
    "paperpilot/data/library.json",
    "paperpilot/data/uploads/",
    "paperpilot/data/glossaries/",
    "paperpilot/data/cache/",
    "paperpilot/data/translations/",
    "paperpilot/data/citations/",
    "paperpilot/data/parsed/upload-",
    "paperpilot/data/custom_sources",
    "paperpilot/data/llm_settings",
    "paperpilot/data/llm_calls.json",
    "paperpilot/data/search_history",
    "paperpilot/out_",
    "paperpilot/out/",
    ".secrets/",
    ".bench/",
    "track2_svd/",
    "track2_wave/",
)


def forbidden(name: str) -> bool:
    if name in {f"paperpilot/{kept}" for kept in DATA_KEEP}:
        return False
    if name in {".env", "paperpilot/.env"} or name.endswith("/.env"):
        return True
    return name.startswith(FORBIDDEN_PREFIXES)


def wanted(rel: Path) -> bool:
    posix = rel.as_posix()
    parts = rel.parts
    if any(part in EXCLUDE_DIR_NAMES or part.startswith(".modelscope_space_build_") for part in parts):
        return False
    if rel.name in EXCLUDE_FILE_NAMES or rel.suffix in EXCLUDE_SUFFIXES:
        return False
    if posix.startswith("paperpilot/out_"):
        return False
    if parts[0] == "paperpilot" and len(parts) > 1 and parts[1] == "data":
        kept = posix[len("paperpilot/"):]
        # Golden sets define the v5 quality gate; shipping them keeps the gate
        # reproducible from the delivered package (quotes only, no full text).
        if kept.startswith("data/eval/golden/"):
            return True
        return kept in DATA_KEEP
    return True


def collect() -> list[Path]:
    files: list[Path] = []
    for name in TOP_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for name in TOP_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if wanted(rel):
                files.append(path)
    return files


def main() -> None:
    files = collect()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(OUT) as archive:
        names = archive.namelist()
        bad_crc = archive.testzip()
        if bad_crc is not None:
            print(f"CRC FAILURE: {bad_crc}")
            raise SystemExit(1)
        offenders = [name for name in names if forbidden(name)]
        if offenders:
            print("FORBIDDEN CONTENT IN PACKAGE:")
            for name in offenders[:20]:
                print(f"  {name}")
            raise SystemExit(1)
    with OUT.open("rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    print(f"{OUT.name}: {OUT.stat().st_size:,} bytes / {len(files)} files")
    print(f"sha256 {digest}")
    print("clean: no secrets, uploads, libraries, runs or project data")


if __name__ == "__main__":
    main()

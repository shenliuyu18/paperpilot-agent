"""Keep a deployed Studio tree identical to the delivery package.

The ModelScope Studio is a Git repository the platform also writes to, so a
deploy that merges the remote history can resurrect files the delivery package
no longer contains. In practice a script deleted before v8.1.4 was still live
on the Studio after the v8.2.0 push, so the deployed tree silently drifted
away from the released package.

These helpers turn "push and hope" into "push exactly the package, then
verify the pushed tree". They are pure apart from the git calls, which are
injected as a runner so they can be tested without a repository.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable, Iterable

# The platform writes this file itself; it is not part of the delivery package
# and must survive a prune.
PLATFORM_OWNED = frozenset({".gitattributes"})

# Runtime state and the operator handbook never enter the Studio image. The
# demo PDFs do: without them the hosted demo cannot render an original page or
# highlight an evidence quote.
STAGE_EXCLUDED_PREFIXES = (
    "paperpilot/data/uploads/",
    "paperpilot/docs/",
)

# The Studio image is built from these two files at the stage root.
STAGE_ROOT_FILES = frozenset({"Dockerfile", "README.md"})

GitRunner = Callable[[list[str]], tuple[int, str]]


def expected_stage_paths(workspace_root: Path, package_files: Iterable[Path]) -> set[str]:
    """Relative posix paths the Studio stage must contain.

    Only the ``paperpilot`` subtree of the delivery package is deployed, plus
    the Dockerfile and the studio README copied from ``modelscope_space``.
    """
    paths: set[str] = set()
    for path in package_files:
        relative = path.relative_to(workspace_root).as_posix()
        if not relative.startswith("paperpilot/"):
            continue
        if relative.startswith(STAGE_EXCLUDED_PREFIXES):
            continue
        paths.add(relative)
    paths.update(STAGE_ROOT_FILES)
    return paths


def extra_paths(tracked: Iterable[str], expected: set[str]) -> list[str]:
    """Tracked paths the package does not contain, ignoring platform-owned ones."""
    return sorted(
        path for path in tracked if path not in expected and path not in PLATFORM_OWNED
    )


def missing_paths(tracked: Iterable[str], expected: set[str]) -> list[str]:
    """Expected paths that the tracked tree does not contain."""
    present = set(tracked)
    return sorted(path for path in expected if path not in present)


def list_index_paths(run_git: GitRunner) -> list[str]:
    """Paths currently tracked in the stage index."""
    code, out = run_git(["ls-files", "-z"])
    if code != 0:
        return []
    return [path for path in out.split("\0") if path]


def list_tree_paths(run_git: GitRunner, ref: str) -> list[str]:
    """Paths contained in a committed tree, used to verify what was pushed."""
    code, out = run_git(["ls-tree", "-r", "--name-only", ref])
    if code != 0:
        return []
    return [path for path in out.splitlines() if path.strip()]


def prune_extraneous(stage: Path, expected: set[str], run_git: GitRunner) -> list[str]:
    """Remove tracked files outside the package from the stage and its index."""
    stale = extra_paths(list_index_paths(run_git), expected)
    for relative in stale:
        run_git(["rm", "-r", "--cached", "--ignore-unmatch", "--", relative])
        target = stage / relative
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
    return stale


def diff_tree(tracked: Iterable[str], expected: set[str]) -> dict[str, list[str]]:
    """Compare a deployed tree with the delivery package."""
    tracked_paths = list(tracked)
    return {
        "missing": missing_paths(tracked_paths, expected),
        "extra": extra_paths(tracked_paths, expected),
    }

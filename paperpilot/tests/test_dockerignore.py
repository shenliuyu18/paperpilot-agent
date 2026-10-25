"""`docker build` must not bake a user's research data or keys into the image.

The Dockerfile ends with `COPY . .`, so whatever Docker sends as the build context becomes part of
the image. The data directory lives next to the Dockerfile and holds uploaded PDFs, parsed text,
projects, agent runs and — on a local install — the stored API key. The guard only works if the
file actually ships inside the delivery package, so that is asserted here too.
"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERIGNORE = ROOT / ".dockerignore"
DOCKERFILE = ROOT / "Dockerfile"


def _lines() -> list[str]:
    return [line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _excluded_by(patterns: list[str], target: str) -> bool:
    """Whether a dockerignore-style pattern list drops ``target`` (last match wins)."""
    hit = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if _matches(pattern[1:], target):
                hit = False
        elif _matches(pattern, target):
            hit = True
    return hit


def _matches(pattern: str, target: str) -> bool:
    import fnmatch

    pattern = pattern.rstrip("/")
    return fnmatch.fnmatch(target, pattern) or fnmatch.fnmatch(target, pattern + "/*")


class DockerignoreTests(unittest.TestCase):
    def test_dockerfile_still_copies_the_whole_tree(self) -> None:
        """The guard matters only because of this line; if it goes away, so should the premise."""
        self.assertIn("COPY . .", DOCKERFILE.read_text(encoding="utf-8"))

    def test_build_context_excludes_user_data_and_secrets(self) -> None:
        self.assertTrue(DOCKERIGNORE.is_file(), "缺少 .dockerignore：`COPY . .` 会把本地 data/ 与密钥烤进镜像")
        patterns = _lines()
        for target in (
            "data/uploads/some.pdf",
            "data/parsed/upload-abc12345.json",
            "data/llm_calls.json",
            "data/llm_settings.secrets.json",
            "data/projects/prj-1/draft.json",
            "data/agent_runs/run-1/report.md",
            "data/search_history.json",
            "data/cache/anything.json",
        ):
            with self.subTest(target=target):
                self.assertTrue(_excluded_by(patterns, target), f"{target} 仍会进入镜像")

    def test_demo_fixtures_are_not_collateral_damage(self) -> None:
        """Excluding data/ wholesale would break the bundled demo and the 评测 page."""
        for target in (
            "data/demo_pdfs/2609.24288v1.pdf",
            "data/parsed/2609.24288v1.json",
            "data/eval_set.json",
            "data/eval_results.json",
            "data/eval/golden/candidates/cim-adc.json",
            "data/cache/crossref_fixture.json",
        ):
            with self.subTest(target=target):
                self.assertFalse(_excluded_by(_lines(), target), f"{target} 被误排除，镜像里演示会坏")

    def test_the_guard_ships_inside_the_delivery_package(self) -> None:
        import sys

        scripts = ROOT / "scripts"
        sys.path.insert(0, str(scripts))
        try:
            from package_project import collect  # noqa: PLC0415
        finally:
            sys.path.remove(str(scripts))
        arcs = [str(path.relative_to(ROOT.parent)).replace("\\", "/") for path in collect()]
        self.assertIn("paperpilot/.dockerignore", arcs)


if __name__ == "__main__":
    unittest.main()

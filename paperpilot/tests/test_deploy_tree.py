"""The deployed Studio tree must equal the delivery package.

The ModelScope Studio is a Git repository the platform also writes to. Merging
its history back into a fresh deploy used to resurrect files the package had
dropped, so the live Studio kept a script that no longer exists in the
release. These tests pin the prune-and-verify contract.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = PACKAGE_ROOT / "deploy"
if str(DEPLOY_DIR) not in sys.path:
    sys.path.insert(0, str(DEPLOY_DIR))

import deploy_tree  # noqa: E402


class DeployTreeTests(unittest.TestCase):
    def test_expected_stage_paths_keeps_package_and_studio_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for relative in (
                "paperpilot/app/main.py",
                "paperpilot/scripts/evaluate.py",
                "README.md",
                "deploy/deploy_github.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
                files.append(path)
            expected = deploy_tree.expected_stage_paths(root, files)
            self.assertIn("paperpilot/app/main.py", expected)
            self.assertIn("paperpilot/scripts/evaluate.py", expected)
            self.assertIn("Dockerfile", expected)
            self.assertIn("README.md", expected)
            self.assertNotIn("deploy/deploy_github.py", expected)

    def test_expected_stage_paths_excludes_runtime_state_and_handbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for relative in (
                "paperpilot/app/main.py",
                "paperpilot/data/uploads/paper.pdf",
                "paperpilot/data/demo_pdfs/demo.pdf",
                "paperpilot/docs/说明文档.md",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
                files.append(path)
            expected = deploy_tree.expected_stage_paths(root, files)
            self.assertEqual(
                expected,
                {
                    "paperpilot/app/main.py",
                    "paperpilot/data/demo_pdfs/demo.pdf",
                    "Dockerfile",
                    "README.md",
                },
            )

    def test_demo_pdfs_ship_so_the_live_demo_can_render_pages(self):
        # The demo corpus PDFs are what make "evidence -> original page ->
        # highlight" work on the hosted instance; dropping them from the stage
        # silently removes the product's strongest proof.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "paperpilot" / "data" / "demo_pdfs" / "2606.08947v1.pdf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
            expected = deploy_tree.expected_stage_paths(root, [path])
            self.assertIn("paperpilot/data/demo_pdfs/2606.08947v1.pdf", expected)

    def test_extra_paths_keeps_platform_owned_files(self):
        expected = {"paperpilot/app/main.py", "README.md"}
        extra = deploy_tree.extra_paths(
            ["paperpilot/app/main.py", "README.md", ".gitattributes", "paperpilot/scripts/old.py"],
            expected,
        )
        self.assertEqual(extra, ["paperpilot/scripts/old.py"])

    def test_missing_paths_reports_absent_package_files(self):
        missing = deploy_tree.missing_paths(["README.md"], {"paperpilot/app/main.py", "README.md"})
        self.assertEqual(missing, ["paperpilot/app/main.py"])

    def test_prune_extraneous_removes_stale_file_from_index_and_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            stale = stage / "paperpilot" / "scripts" / "old.py"
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text("stale", encoding="utf-8")
            kept = stage / "paperpilot" / "app" / "main.py"
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_text("kept", encoding="utf-8")
            calls: list[list[str]] = []

            def run_git(args: list[str]) -> tuple[int, str]:
                calls.append(args)
                if args[:1] == ["ls-files"]:
                    return 0, "paperpilot/app/main.py\0paperpilot/scripts/old.py\0"
                return 0, ""

            pruned = deploy_tree.prune_extraneous(
                stage, {"paperpilot/app/main.py", "README.md"}, run_git
            )
            self.assertEqual(pruned, ["paperpilot/scripts/old.py"])
            self.assertFalse(stale.exists())
            self.assertTrue(kept.exists())
            self.assertIn(["rm", "-r", "--cached", "--ignore-unmatch", "--", "paperpilot/scripts/old.py"], calls)

    def test_diff_tree_reports_both_directions(self):
        difference = deploy_tree.diff_tree(
            ["paperpilot/app/main.py", "paperpilot/scripts/old.py"],
            {"paperpilot/app/main.py", "README.md"},
        )
        self.assertEqual(difference["missing"], ["README.md"])
        self.assertEqual(difference["extra"], ["paperpilot/scripts/old.py"])

    def test_model_scope_deploy_prunes_before_push_and_verifies_after(self):
        script = DEPLOY_DIR / "deploy_modelscope.py"
        self.assertTrue(script.exists(), "deploy/deploy_modelscope.py is part of the delivery package")
        text = script.read_text(encoding="utf-8")
        prune_at = text.find("prune_extraneous(")
        push_at = text.find('["push"')
        verify_at = text.find("diff_tree(")
        self.assertGreater(prune_at, 0, "the deploy must prune files outside the package")
        self.assertGreater(push_at, 0, "the deploy must push")
        self.assertGreater(verify_at, 0, "the deploy must verify the pushed tree")
        self.assertLess(prune_at, push_at, "pruning has to happen before the push")
        self.assertLess(push_at, verify_at, "the pushed tree has to be verified after the push")


if __name__ == "__main__":
    unittest.main()

"""Gate scripts must survive a Windows locale console.

`scripts/acceptance.py` aborted at check 82 of 94 on this machine because check details carry runtime text
(a model reply, an error string) and the console codec is GBK, which has no ``•``. The crash is in ``print``,
not in the product, so the gate silently reported failure for a passing build.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ``•`` is legal Unicode, common in provider replies, and absent from GBK/cp936.
UNENCODABLE = "\u2022"


def _run(code: str, encoding: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = encoding
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
    )


class ConsoleSafeStreamTests(unittest.TestCase):
    def test_unencodable_text_does_not_break_a_locale_console(self) -> None:
        """Writing runtime text on a GBK console must not raise."""
        proc = _run(
            "from scripts._console import make_console_safe\n"
            "make_console_safe()\n"
            f"print('PASS check | detail={UNENCODABLE}ok{UNENCODABLE}', flush=True)\n",
            "gbk",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertIn("ok", proc.stdout)

    def test_the_locale_encoding_is_kept(self) -> None:
        """Only the error handler changes; forcing UTF-8 would turn Chinese into mojibake in conhost."""
        proc = _run(
            "from scripts._console import make_console_safe\n"
            "import sys\n"
            "make_console_safe()\n"
            "print(sys.stdout.encoding.lower(), sys.stdout.errors)\n",
            "gbk",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertIn("gbk", proc.stdout)
        self.assertIn("replace", proc.stdout)

    def test_stderr_keeps_its_lossless_handler(self) -> None:
        """Python already renders undisplayable stderr bytes as \\uXXXX escapes; do not downgrade that."""
        proc = _run(
            "from scripts._console import make_console_safe\n"
            "import sys\n"
            "make_console_safe()\n"
            "print(sys.stdout.errors, sys.stderr.errors)\n",
            "gbk",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertEqual(proc.stdout.split(), ["replace", "backslashreplace"])

    def test_a_surrogateescape_console_is_made_safe(self) -> None:
        """Windows opens a redirected stdout with surrogateescape, which still raises on ``•``."""
        proc = _run(
            "from scripts._console import make_console_safe\n"
            "import sys\n"
            "make_console_safe()\n"
            "print(sys.stdout.errors)\n"
            f"print('PASS check | detail={UNENCODABLE}ok{UNENCODABLE}', flush=True)\n",
            "gbk:surrogateescape",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
        self.assertIn("replace", proc.stdout)
        self.assertIn("ok", proc.stdout)

    def test_the_bug_is_reproducible_without_the_helper(self) -> None:
        """If this stops failing, the two tests above assert nothing."""
        proc = _run(
            "import sys\n"
            f"print({UNENCODABLE!r})\n",
            "gbk",
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("UnicodeEncodeError", proc.stderr)


class GateScriptsDeclareSafeStdoutTests(unittest.TestCase):
    """Every script that writes to stdout has to opt in, or a new one reintroduces the crash."""

    def printed_scripts(self) -> list[Path]:
        out = []
        for path in sorted((ROOT / "scripts").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls = [
                n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "print"
            ]
            if calls:
                out.append(path)
        return out

    def test_at_least_the_known_printers_are_covered(self) -> None:
        covered = self.printed_scripts()
        assert covered, "no printing script found: the scan is broken, not the code"
        self.assertIn("acceptance.py", [p.name for p in covered])

    def test_printing_scripts_make_their_console_safe_before_the_first_print(self) -> None:
        """Calling the helper is not enough: a print that runs first still crashes."""
        violations = []
        for path in self.printed_scripts():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            first_print = helper = None
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if func == "print" and (first_print is None or node.lineno < first_print):
                    first_print = node.lineno
                if func == "make_console_safe" and (helper is None or node.lineno < helper):
                    helper = node.lineno
            if helper is None or (first_print is not None and helper > first_print):
                violations.append(f"{path.name}(helper={helper}, first_print={first_print})")
        self.assertEqual(violations, [], f"scripts print before the console is made safe: {violations}")


if __name__ == "__main__":
    unittest.main()

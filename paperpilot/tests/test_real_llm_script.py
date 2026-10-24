from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_real_llm  # noqa: E402


class RealLLMScriptTests(unittest.TestCase):
    def _quiet(self, fn, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*args)
    def test_check_without_key_exits_2(self) -> None:
        with patch.object(eval_real_llm.config, "LLM_ENABLED", False):
            self.assertEqual(self._quiet(eval_real_llm.check), 2)

    def test_check_with_key_exits_0(self) -> None:
        with patch.object(eval_real_llm.config, "LLM_ENABLED", True):
            self.assertEqual(self._quiet(eval_real_llm.check), 0)

    def test_claims_without_key_writes_no_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "report.json"
            with patch.object(eval_real_llm.config, "LLM_ENABLED", False):
                self.assertEqual(self._quiet(eval_real_llm.run_claims, 5, out), 2)
            self.assertFalse(out.exists())

    def test_contradictions_without_key_exits_2(self) -> None:
        with patch.object(eval_real_llm.config, "LLM_ENABLED", False):
            self.assertEqual(self._quiet(eval_real_llm.run_contradictions, "prj-test"), 2)


if __name__ == "__main__":
    unittest.main()

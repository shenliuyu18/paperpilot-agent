from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from app import config
from app.services import analysis


class ReadCacheTests(unittest.TestCase):
    def test_repeated_read_reuses_extraction_without_shared_mutation(self) -> None:
        paper = {
            "id": "cache-" + uuid.uuid4().hex,
            "title": "Low-Power ADC for Compute-in-Memory",
            "abstract": (
                "Compute-in-memory macros require efficient ADC readout. "
                "This paper proposes a low-power SAR ADC and evaluates energy and area. "
                "The main limitation is that only simulation results are reported."
            ),
            "authors": ["Alice Alpha"],
            "year": 2024,
            "venue": "Test Journal",
            "sections": [
                {"title": "Abstract", "page": 1, "text": "Compute-in-memory macros require efficient ADC readout."},
                {"title": "Results", "page": 2, "text": "The ADC reduces energy by 40% in simulation."},
            ],
            "full_text": True,
        }
        with patch.object(config, "LLM_ENABLED", False), \
             patch.object(analysis, "_llm_read", return_value=(None, "")), \
             patch.object(analysis, "_heuristic_fields", wraps=analysis._heuristic_fields) as spy:
            first = analysis.read_paper(paper, "5min")
            second = analysis.read_paper(paper, "5min")
            self.assertEqual(spy.call_count, 1)
            self.assertEqual(first["problem"], second["problem"])
            first["novelty"].append("MUTATED")
            third = analysis.read_paper(paper, "5min")
            self.assertNotIn("MUTATED", third["novelty"])


if __name__ == "__main__":
    unittest.main()

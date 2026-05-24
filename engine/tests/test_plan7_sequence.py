from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_plan7_sequence import build_summary, write_result


class Plan7SequenceTests(unittest.TestCase):
    def test_write_result_preserves_diagnostic_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir)
            payload = {
                "chapter_no": 6,
                "book_id": "plan6-seq-run",
                "final_outcome": "exported",
                "final_stage": "exported",
                "revision_rounds_executed": 1,
                "comparison_history": [{"decision": "keep"}],
                "last_gate_reasons": ["gate:passed"],
                "last_truth_delta_notes": ["truth-note"],
                "style_drift_axes": ["dialogue_density"],
                "audit_diagnostics_history": [{"mode_used": "text_fallback"}],
                "truth_diagnostics_history": [{"mode_used": "text_fallback"}],
                "error": None,
            }
            path = write_result(run_dir, 6, payload)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["chapter_no"], 6)
            self.assertEqual(loaded["last_gate_reasons"], ["gate:passed"])
            self.assertEqual(loaded["last_truth_delta_notes"], ["truth-note"])
            self.assertEqual(loaded["audit_diagnostics_history"][0]["mode_used"], "text_fallback")

    def test_build_summary_keeps_per_chapter_results(self) -> None:
        results = [
            {
                "chapter_no": 6,
                "book_id": "plan6-seq-run",
                "final_outcome": "exported",
                "final_stage": "exported",
                "revision_rounds_executed": 0,
                "style_drift_axes": [],
                "error": None,
            },
            {
                "chapter_no": 7,
                "book_id": "plan6-seq-run",
                "final_outcome": "provider_fault",
                "final_stage": "settled",
                "revision_rounds_executed": 0,
                "style_drift_axes": ["metaphor_density"],
                "error": "HTTP Error 504: Gateway Time-out",
            },
        ]
        summary, summary_md = build_summary(results)
        self.assertEqual(summary["book_id"], "plan6-seq-run")
        self.assertEqual(len(summary["chapters"]), 2)
        self.assertIn("chapter_no", summary["chapters"][0])
        self.assertIn("| 7 | provider_fault | settled |", summary_md)


if __name__ == "__main__":
    unittest.main()

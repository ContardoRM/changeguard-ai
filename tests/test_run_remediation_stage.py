#!/usr/bin/env python3
"""Unit tests for scripts/run_remediation_stage.py's status rollup.

Covers the Phase 8B observability correction: distinguish "remediated"
(all succeeded), "partial" (some succeeded, some did not), "failed" (none
succeeded), and "noop" (nothing to remediate) instead of conflating
partial and fully-failed outcomes. Uses only the Python 3 standard
library `unittest` module; does not invoke any real `kiro-cli` subprocess.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import run_remediation_stage  # noqa: E402


class SummarizeStatusTestCase(unittest.TestCase):
    def test_no_results_is_noop(self):
        self.assertEqual(run_remediation_stage._summarize_status([]), "noop")

    def test_all_remediated_is_remediated(self):
        results = [{"status": "remediated"}, {"status": "remediated"}]
        self.assertEqual(run_remediation_stage._summarize_status(results), "remediated")

    def test_all_failed_is_failed(self):
        results = [{"status": "remediation_failed"}, {"status": "refused"}]
        self.assertEqual(run_remediation_stage._summarize_status(results), "failed")

    def test_mixed_outcomes_is_partial(self):
        results = [{"status": "remediated"}, {"status": "remediation_failed"}]
        self.assertEqual(run_remediation_stage._summarize_status(results), "partial")


class RunRemediationStageSkipTestCase(unittest.TestCase):
    def test_missing_blocked_input_skips_without_invoking_agent(self):
        result = run_remediation_stage.run_remediation_stage(
            "/nonexistent/change-blocked-result.json", "terraform", 5.0
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["results"], [])


if __name__ == "__main__":
    unittest.main()

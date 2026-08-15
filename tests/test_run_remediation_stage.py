#!/usr/bin/env python3
"""Unit tests for scripts/run_remediation_stage.py's status rollup and
fail-closed exit-code contract.

Covers the Phase 8B observability correction: distinguish "remediated"
(all succeeded), "partial" (some succeeded, some did not), "failed" (none
succeeded), and "noop" (nothing to remediate) instead of conflating
partial and fully-failed outcomes.

Also covers the Phase 8B fail-open BUG CORRECTION discovered via a real
live remediated run: `main()` previously returned exit 0 unconditionally
regardless of `result["status"]`, so a Crew DAG node treated a failed
remediation as a passed task and the workflow proceeded to
`remediated-plan`/re-review/SAFE_TO_SHIP anyway. These tests assert the
corrected exit-code contract (exit 0 only for "skipped"/"remediated";
non-zero for every other status) and the corrected `_extract_json_object`
JSON-envelope validation (exactly one JSON value, ambiguous/malformed
agent stdout rejected -- reproducing the exact real "Extra data: line 2
column 1" failure mode observed live).

Uses only the Python 3 standard library `unittest` module; does not
invoke any real `kiro-cli` subprocess.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

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


class ExtractJsonObjectTestCase(unittest.TestCase):
    """Covers the exact real failure mode observed live: a valid JSON
    result object followed by a second, ambiguous JSON-shaped block."""

    def test_single_clean_json_object_is_accepted(self):
        stdout = '{"status": "remediated", "rule_id": "REL-001", "resource": "x", "restored_value": 3}'
        value = run_remediation_stage._extract_json_object(stdout)
        self.assertEqual(value["status"], "remediated")

    def test_json_object_with_only_trailing_whitespace_is_accepted(self):
        stdout = '{"status": "remediated"}\n\n   \n'
        value = run_remediation_stage._extract_json_object(stdout)
        self.assertEqual(value["status"], "remediated")

    def test_second_json_object_on_next_line_is_rejected(self):
        # Reproduces the real observed failure: a valid 111-character
        # result object on line 1, followed by a second brace-delimited
        # block on line 2 -- json.JSONDecodeError: Extra data: line 2
        # column 1 (char 112), exactly as seen in the live run.
        stdout = (
            '{"status": "remediated", "rule_id": "REL-001", '
            '"resource": "aws_ecs_service.payments_api", "restored_value": 3}\n'
            '{"note": "done"}'
        )
        with self.assertRaises(ValueError) as ctx:
            run_remediation_stage._extract_json_object(stdout)
        self.assertIn("more than one JSON value", str(ctx.exception))

    def test_trailing_prose_after_valid_json_is_rejected(self):
        stdout = (
            '{"status": "remediated", "rule_id": "REL-001", "resource": "x", "restored_value": 3}\n\n'
            "The remediation is complete."
        )
        with self.assertRaises(ValueError):
            run_remediation_stage._extract_json_object(stdout)

    def test_no_json_object_at_all_is_rejected(self):
        with self.assertRaises(ValueError):
            run_remediation_stage._extract_json_object("no JSON here at all")

    def test_malformed_json_is_rejected(self):
        with self.assertRaises(ValueError):
            run_remediation_stage._extract_json_object('{"status": "remediated"')


class MainExitCodeContractTestCase(unittest.TestCase):
    """Covers the fail-open BUG CORRECTION: main() must exit non-zero for
    every status other than "skipped"/"remediated", so Crew's own DAG
    failure propagation blocks downstream nodes on a failed remediation."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="run_remediation_stage_main_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.output_path = os.path.join(self.tmp_dir, "remediation-result.json")

    def _run_main_with_stage_result(self, stage_result):
        with mock.patch.object(run_remediation_stage, "run_remediation_stage", return_value=stage_result):
            return run_remediation_stage.main(
                ["--blocked-input", "/nonexistent.json", "--output", self.output_path]
            )

    def test_exit_zero_for_skipped(self):
        exit_code = self._run_main_with_stage_result({"status": "skipped", "results": []})
        self.assertEqual(exit_code, 0)

    def test_exit_zero_for_remediated(self):
        exit_code = self._run_main_with_stage_result(
            {"status": "remediated", "results": [{"status": "remediated"}]}
        )
        self.assertEqual(exit_code, 0)

    def test_exit_nonzero_for_failed(self):
        # This is the exact real observed case: run_remediation_stage()
        # returned {"status": "failed", ...} and the old main() still
        # returned 0. Corrected main() must return non-zero.
        exit_code = self._run_main_with_stage_result(
            {
                "status": "failed",
                "results": [
                    {
                        "status": "remediation_failed",
                        "rule_id": "REL-001",
                        "resource": "aws_ecs_service.payments_api",
                        "error": "agent stdout did not contain valid JSON: Extra data: line 2 column 1 (char 112)",
                    }
                ],
            }
        )
        self.assertNotEqual(exit_code, 0)

    def test_exit_nonzero_for_partial(self):
        exit_code = self._run_main_with_stage_result(
            {"status": "partial", "results": [{"status": "remediated"}, {"status": "remediation_failed"}]}
        )
        self.assertNotEqual(exit_code, 0)

    def test_exit_nonzero_for_noop(self):
        exit_code = self._run_main_with_stage_result({"status": "noop", "results": []})
        self.assertNotEqual(exit_code, 0)

    def test_output_artifact_is_still_written_even_on_failure(self):
        # The result must still be persisted atomically even though exit
        # is non-zero -- final_verdict.py needs to read it to produce a
        # REMEDIATION_FAILED verdict with a real reason.
        self._run_main_with_stage_result(
            {"status": "failed", "results": [{"status": "remediation_failed"}]}
        )
        self.assertTrue(os.path.isfile(self.output_path))
        with open(self.output_path) as f:
            written = json.load(f)
        self.assertEqual(written["status"], "failed")


if __name__ == "__main__":
    unittest.main()

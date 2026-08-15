#!/usr/bin/env python3
"""Unit tests for scripts/final_verdict.py's fail-closed remediation-result
requirement.

Covers the Phase 8B fail-open BUG CORRECTION discovered via a real live
approved-remediation run: `final_verdict.py` previously never consulted
`run_remediation_stage.py`'s own result artifact at all, so a failed/
malformed remediation-result.json could coexist with an already-mutated,
PASS/PASS-reviewed Terraform state and still produce SAFE_TO_SHIP. These
tests assert the corrected requirement: SAFE_TO_SHIP requires
remediation-result.json to exist, parse, and report
status == "remediated", checked before plan status or either reviewer,
independently and unconditionally.

Uses only the Python 3 standard library `unittest` module.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _REPO_ROOT)
# final_verdict.py imports aggregate_review as a same-directory sibling
# module (`from aggregate_review import ...`), so scripts/ itself must
# also be importable, not just the repo root.
sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))

from scripts import final_verdict  # noqa: E402


class BuildFinalVerdictTestCase(unittest.TestCase):
    """Direct unit tests of build_final_verdict()'s decision logic."""

    PASS_RESULT = {"status": "PASS", "findings": []}

    def test_remediation_failed_with_both_reviewers_pass_is_not_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=True,
            remediation_status="failed",
            remediation_error=None,
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["status"], "REMEDIATION_FAILED")

    def test_remediation_missing_with_both_reviewers_pass_is_not_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=True,
            remediation_status=None,
            remediation_error="remediation result file not found: artifacts/remediation-result.json",
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["status"], "REMEDIATION_FAILED")

    def test_remediation_malformed_with_both_reviewers_pass_is_not_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=True,
            remediation_status=None,
            remediation_error="remediation result file could not be read as JSON: ...",
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["status"], "REMEDIATION_FAILED")

    def test_remediation_partial_with_both_reviewers_pass_is_not_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=True,
            remediation_status="partial",
            remediation_error=None,
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["status"], "REMEDIATION_FAILED")

    def test_remediated_plus_pass_plus_pass_plus_plan_success_is_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=True,
            remediation_status="remediated",
            remediation_error=None,
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["findings"], [])

    def test_remediated_but_plan_failed_is_not_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=False,
            remediation_status="remediated",
            remediation_error=None,
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")

    def test_remediated_and_plan_success_but_a_reviewer_fails_is_not_safe_to_ship(self):
        fail_result = {"status": "FAIL", "findings": [{"rule_id": "REL-001"}]}
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, fail_result,
            plan_succeeded=True,
            remediation_status="remediated",
            remediation_error=None,
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["status"], "CHANGE_BLOCKED")

    def test_scope_note_present_on_safe_to_ship(self):
        verdict = final_verdict.build_final_verdict(
            self.PASS_RESULT, self.PASS_RESULT,
            plan_succeeded=True,
            remediation_status="remediated",
            remediation_error=None,
            scope=["SEC-001", "SEC-002", "REL-001", "BR-001"],
        )
        self.assertIn("scope_note", verdict)
        self.assertIn("not mean the infrastructure is", verdict["scope_note"])
        self.assertEqual(verdict["scope"], ["SEC-001", "SEC-002", "REL-001", "BR-001"])


class LoadRemediationResultTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="final_verdict_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _write(self, name, content):
        path = os.path.join(self.tmp_dir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_missing_file_returns_none_with_error(self):
        status, error = final_verdict._load_remediation_result(os.path.join(self.tmp_dir, "nope.json"))
        self.assertIsNone(status)
        self.assertIsNotNone(error)

    def test_malformed_json_returns_none_with_error(self):
        path = self._write("bad.json", "{not valid json")
        status, error = final_verdict._load_remediation_result(path)
        self.assertIsNone(status)
        self.assertIsNotNone(error)

    def test_missing_status_field_returns_none_with_error(self):
        path = self._write("no_status.json", json.dumps({"results": []}))
        status, error = final_verdict._load_remediation_result(path)
        self.assertIsNone(status)
        self.assertIsNotNone(error)

    def test_valid_remediated_status_is_extracted(self):
        path = self._write("ok.json", json.dumps({"status": "remediated", "results": []}))
        status, error = final_verdict._load_remediation_result(path)
        self.assertEqual(status, "remediated")
        self.assertIsNone(error)

    def test_valid_failed_status_is_extracted_verbatim(self):
        # This reproduces the real observed artifact content.
        path = self._write(
            "failed.json",
            json.dumps(
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
            ),
        )
        status, error = final_verdict._load_remediation_result(path)
        self.assertEqual(status, "failed")
        self.assertIsNone(error)


class MainIntegrationTestCase(unittest.TestCase):
    """End-to-end CLI-level reproduction of the exact real failure scenario:
    remediation-result.json status=failed, both reviewers PASS, plan
    succeeded -- must not produce SAFE_TO_SHIP."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="final_verdict_main_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _write(self, name, payload):
        path = os.path.join(self.tmp_dir, name)
        with open(path, "w") as f:
            json.dump(payload, f)
        return path

    def test_real_observed_failure_scenario_does_not_produce_safe_to_ship(self):
        security_path = self._write("security.json", {"status": "PASS", "findings": []})
        reliability_path = self._write("reliability.json", {"status": "PASS", "findings": []})
        remediation_path = self._write(
            "remediation-result.json",
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
            },
        )
        output_path = os.path.join(self.tmp_dir, "final-verdict.json")

        exit_code = final_verdict.main(
            [
                "--security", security_path,
                "--reliability", reliability_path,
                "--plan-status", "success",
                "--remediation-result", remediation_path,
                "--output", output_path,
            ]
        )

        self.assertEqual(exit_code, 0)  # the script itself always exits 0; the VERDICT is what must be blocked
        with open(output_path) as f:
            verdict = json.load(f)
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")
        self.assertEqual(verdict["status"], "REMEDIATION_FAILED")

    def test_missing_remediation_result_does_not_produce_safe_to_ship(self):
        security_path = self._write("security.json", {"status": "PASS", "findings": []})
        reliability_path = self._write("reliability.json", {"status": "PASS", "findings": []})
        output_path = os.path.join(self.tmp_dir, "final-verdict.json")

        exit_code = final_verdict.main(
            [
                "--security", security_path,
                "--reliability", reliability_path,
                "--plan-status", "success",
                "--remediation-result", os.path.join(self.tmp_dir, "does-not-exist.json"),
                "--output", output_path,
            ]
        )

        self.assertEqual(exit_code, 0)
        with open(output_path) as f:
            verdict = json.load(f)
        self.assertNotEqual(verdict["status"], "SAFE_TO_SHIP")

    def test_successful_remediation_with_pass_pass_produces_safe_to_ship(self):
        security_path = self._write("security.json", {"status": "PASS", "findings": []})
        reliability_path = self._write("reliability.json", {"status": "PASS", "findings": []})
        remediation_path = self._write("remediation-result.json", {"status": "remediated", "results": []})
        output_path = os.path.join(self.tmp_dir, "final-verdict.json")

        exit_code = final_verdict.main(
            [
                "--security", security_path,
                "--reliability", reliability_path,
                "--plan-status", "success",
                "--remediation-result", remediation_path,
                "--output", output_path,
            ]
        )

        self.assertEqual(exit_code, 0)
        with open(output_path) as f:
            verdict = json.load(f)
        self.assertEqual(verdict["status"], "SAFE_TO_SHIP")


if __name__ == "__main__":
    unittest.main()

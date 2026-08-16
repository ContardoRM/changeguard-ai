#!/usr/bin/env python3
"""Unit tests for scripts/write_review_result.py.

Covers the Phase 8D transport correction: a reviewer's `kiro-cli chat`
stdout is not an authoritative result transport (it legitimately contains
the evidence-extraction tool's own JSON output alongside the reviewer's
final ReviewResult JSON), so the reviewer's result is instead persisted
through this narrowly-scoped, deterministic artifact-transport script,
analogous to `apply_remediation.py`'s `--result-file` mechanism for the
Remediator.

This script validates STRUCTURE ONLY (agent identity, status enum,
findings shape, permitted rule_id scope for that agent) -- it never
evaluates a Terraform value or decides whether any condition violates a
rule. These tests assert exactly that boundary.

Uses only the Python 3 standard library `unittest` module.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import write_review_result  # noqa: E402


class ValidateReviewResultSchemaTestCase(unittest.TestCase):
    """Direct unit tests of validate_review_result_schema()'s structural
    validation -- no filesystem, no subprocess."""

    def test_valid_security_pass_is_accepted(self):
        payload = {"agent": "security-reviewer", "status": "PASS", "findings": []}
        result = write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertEqual(result, payload)

    def test_valid_security_fail_with_sec001_is_accepted(self):
        payload = {
            "agent": "security-reviewer",
            "status": "FAIL",
            "findings": [
                {
                    "rule_id": "SEC-001",
                    "severity": "CRITICAL",
                    "resource": "aws_security_group.payments_sg",
                    "baseline_value": ["10.0.0.0/8"],
                    "candidate_value": ["0.0.0.0/0"],
                    "reason": "TCP port 22 becomes publicly reachable from 0.0.0.0/0.",
                    "proposed_remediation": "Restore the exact baseline CIDR value.",
                }
            ],
        }
        result = write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertEqual(result, payload)

    def test_valid_reliability_fail_with_rel001_is_accepted(self):
        payload = {
            "agent": "reliability-reviewer",
            "status": "FAIL",
            "findings": [
                {
                    "rule_id": "REL-001",
                    "severity": "HIGH",
                    "resource": "aws_ecs_service.payments_api",
                    "baseline_value": 3,
                    "candidate_value": 1,
                    "reason": "ECS desired_count is reduced to a single task, removing workload redundancy.",
                    "proposed_remediation": "Restore desired_count to 3.",
                }
            ],
        }
        result = write_review_result.validate_review_result_schema(payload, "reliability-reviewer")
        self.assertEqual(result, payload)

    def test_valid_incomplete_is_accepted(self):
        payload = {
            "agent": "security-reviewer",
            "status": "INCOMPLETE",
            "findings": [],
            "error": "candidate TCP/22 evidence: MALFORMED",
        }
        result = write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertEqual(result, payload)

    def test_wrong_agent_identity_is_rejected(self):
        # A reliability-reviewer result masquerading as a security-reviewer
        # result must be rejected -- identity check.
        payload = {"agent": "reliability-reviewer", "status": "PASS", "findings": []}
        with self.assertRaises(write_review_result.ReviewResultValidationError) as ctx:
            write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertIn("agent", str(ctx.exception))

    def test_unsupported_rule_id_for_security_reviewer_is_rejected(self):
        # REL-001 is not in security-reviewer's permitted scope.
        payload = {
            "agent": "security-reviewer",
            "status": "FAIL",
            "findings": [
                {
                    "rule_id": "REL-001",
                    "severity": "HIGH",
                    "resource": "aws_ecs_service.payments_api",
                    "baseline_value": 3,
                    "candidate_value": 1,
                }
            ],
        }
        with self.assertRaises(write_review_result.ReviewResultValidationError) as ctx:
            write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertIn("rule_id", str(ctx.exception))

    def test_unsupported_rule_id_for_reliability_reviewer_is_rejected(self):
        # SEC-002 is not in reliability-reviewer's permitted scope.
        payload = {
            "agent": "reliability-reviewer",
            "status": "FAIL",
            "findings": [{"rule_id": "SEC-002", "severity": "CRITICAL", "resource": "x"}],
        }
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result.validate_review_result_schema(payload, "reliability-reviewer")

    def test_pass_with_nonempty_findings_is_rejected(self):
        # PASS requires findings == [] -- internally inconsistent otherwise.
        payload = {
            "agent": "security-reviewer",
            "status": "PASS",
            "findings": [{"rule_id": "SEC-001", "severity": "CRITICAL", "resource": "x"}],
        }
        with self.assertRaises(write_review_result.ReviewResultValidationError) as ctx:
            write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertIn("PASS", str(ctx.exception))

    def test_incomplete_without_error_is_rejected(self):
        payload = {"agent": "security-reviewer", "status": "INCOMPLETE", "findings": []}
        with self.assertRaises(write_review_result.ReviewResultValidationError) as ctx:
            write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertIn("error", str(ctx.exception))

    def test_incomplete_with_nonempty_findings_is_rejected(self):
        payload = {
            "agent": "security-reviewer",
            "status": "INCOMPLETE",
            "findings": [{"rule_id": "SEC-001", "severity": "CRITICAL", "resource": "x"}],
            "error": "something",
        }
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result.validate_review_result_schema(payload, "security-reviewer")

    def test_fail_with_empty_findings_is_rejected(self):
        payload = {"agent": "security-reviewer", "status": "FAIL", "findings": []}
        with self.assertRaises(write_review_result.ReviewResultValidationError) as ctx:
            write_review_result.validate_review_result_schema(payload, "security-reviewer")
        self.assertIn("FAIL", str(ctx.exception))

    def test_invalid_status_value_is_rejected(self):
        payload = {"agent": "security-reviewer", "status": "MAYBE", "findings": []}
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result.validate_review_result_schema(payload, "security-reviewer")

    def test_non_dict_payload_is_rejected(self):
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result.validate_review_result_schema(["not", "a", "dict"], "security-reviewer")

    def test_findings_not_a_list_is_rejected(self):
        payload = {"agent": "security-reviewer", "status": "PASS", "findings": "not-a-list"}
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result.validate_review_result_schema(payload, "security-reviewer")


class OutputPathConfinementTestCase(unittest.TestCase):
    """--output must resolve strictly inside --artifacts-dir with the
    fixed '.review-result-<id>.json' filename pattern. Mirrors
    apply_remediation.py's ResultFilePathConfinementTests."""

    def setUp(self):
        self.artifacts_dir = tempfile.mkdtemp(prefix="write_review_result_artifacts_")
        self.addCleanup(shutil.rmtree, self.artifacts_dir, ignore_errors=True)

    def _valid_path(self):
        return os.path.join(self.artifacts_dir, ".review-result-abc123.json")

    def test_valid_internal_path_is_accepted(self):
        resolved = write_review_result._validate_output_path(self._valid_path(), self.artifacts_dir)
        self.assertEqual(os.path.dirname(resolved), os.path.realpath(self.artifacts_dir))

    def test_absolute_path_outside_artifacts_dir_is_rejected(self):
        outside_dir = tempfile.mkdtemp(prefix="write_review_result_outside_")
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        outside_path = os.path.join(outside_dir, ".review-result-evil.json")
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result._validate_output_path(outside_path, self.artifacts_dir)

    def test_traversal_path_escaping_artifacts_dir_is_rejected(self):
        traversal_path = os.path.join(self.artifacts_dir, "..", "..", "etc", ".review-result-evil.json")
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result._validate_output_path(traversal_path, self.artifacts_dir)

    def test_wrong_filename_pattern_is_rejected(self):
        wrong_name_path = os.path.join(self.artifacts_dir, "security-review-result.json")
        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result._validate_output_path(wrong_name_path, self.artifacts_dir)

    def test_symlink_escaping_artifacts_dir_is_rejected(self):
        outside_dir = tempfile.mkdtemp(prefix="write_review_result_symlink_target_")
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        real_outside_target = os.path.join(outside_dir, "real-target.json")

        symlink_path = os.path.join(self.artifacts_dir, ".review-result-link.json")
        try:
            os.symlink(real_outside_target, symlink_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform/filesystem")

        with self.assertRaises(write_review_result.ReviewResultValidationError):
            write_review_result._validate_output_path(symlink_path, self.artifacts_dir)
        self.assertFalse(os.path.exists(real_outside_target))


class MainEndToEndTestCase(unittest.TestCase):
    """main() exercised end-to-end via stdin, with a real temp artifacts
    directory -- no subprocess, no kiro-cli."""

    def setUp(self):
        self.artifacts_dir = tempfile.mkdtemp(prefix="write_review_result_main_test_")
        self.addCleanup(shutil.rmtree, self.artifacts_dir, ignore_errors=True)
        self.output_path = os.path.join(self.artifacts_dir, ".review-result-xyz789.json")

    def _run_main(self, agent, stdin_text):
        old_stdin = sys.stdin
        sys.stdin = _FakeStdin(stdin_text)
        try:
            return write_review_result.main(
                ["--agent", agent, "--output", self.output_path, "--artifacts-dir", self.artifacts_dir]
            )
        finally:
            sys.stdin = old_stdin

    def test_valid_pass_writes_output_and_exits_zero(self):
        stdin_text = json.dumps({"agent": "security-reviewer", "status": "PASS", "findings": []})
        exit_code = self._run_main("security-reviewer", stdin_text)
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.isfile(self.output_path))
        with open(self.output_path) as f:
            self.assertEqual(json.load(f), {"agent": "security-reviewer", "status": "PASS", "findings": []})

    def test_malformed_stdin_json_writes_nothing_and_exits_nonzero(self):
        exit_code = self._run_main("security-reviewer", "{not valid json")
        self.assertNotEqual(exit_code, 0)
        self.assertFalse(os.path.isfile(self.output_path))

    def test_schema_violation_writes_nothing_and_exits_nonzero(self):
        # Wrong agent identity.
        stdin_text = json.dumps({"agent": "reliability-reviewer", "status": "PASS", "findings": []})
        exit_code = self._run_main("security-reviewer", stdin_text)
        self.assertNotEqual(exit_code, 0)
        self.assertFalse(os.path.isfile(self.output_path))

    def test_stdout_containing_evidence_json_and_final_result_does_not_affect_this_script(self):
        # This script only ever sees exactly what is passed on stdin --
        # it has no visibility into a reviewer's chat stdout at all, so
        # the exact real observed "evidence JSON + final ReviewResult
        # JSON in one stream" shape is structurally irrelevant to it.
        # Simulate by passing ONLY the final ReviewResult on stdin, as
        # the reviewer agent's write_review_result.py invocation would.
        stdin_text = json.dumps(
            {
                "agent": "reliability-reviewer",
                "status": "FAIL",
                "findings": [
                    {
                        "rule_id": "REL-001",
                        "severity": "HIGH",
                        "resource": "aws_ecs_service.payments_api",
                        "baseline_value": 3,
                        "candidate_value": 1,
                        "reason": "ECS desired_count is reduced to a single task, removing workload redundancy.",
                        "proposed_remediation": "Restore desired_count to 3.",
                    }
                ],
            }
        )
        exit_code = self._run_main("reliability-reviewer", stdin_text)
        self.assertEqual(exit_code, 0)
        with open(self.output_path) as f:
            written = json.load(f)
        self.assertEqual(written["status"], "FAIL")
        self.assertEqual(written["findings"][0]["rule_id"], "REL-001")


class _FakeStdin:
    """Minimal stand-in for sys.stdin exposing only .read()."""

    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


if __name__ == "__main__":
    unittest.main()

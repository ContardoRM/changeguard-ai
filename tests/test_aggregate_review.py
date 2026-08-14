#!/usr/bin/env python3
"""Unit tests for scripts/aggregate_review.py.

Covers the Phase 8B correction: a safe (PASS+PASS) result must remove any
pre-existing stale artifacts/change-blocked-result.json, and a blocked
result must atomically replace whatever was previously at that path.
Uses only the Python 3 standard library `unittest` module.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import aggregate_review  # noqa: E402


class AggregateReviewArtifactHygieneTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="aggregate_review_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.security_path = os.path.join(self.tmp_dir, "security-review-result.json")
        self.reliability_path = os.path.join(self.tmp_dir, "reliability-review-result.json")
        self.pass_output = os.path.join(self.tmp_dir, "final-verdict.json")
        self.blocked_output = os.path.join(self.tmp_dir, "change-blocked-result.json")

    def _write_json(self, path, payload):
        with open(path, "w") as f:
            json.dump(payload, f)

    def _run(self):
        return aggregate_review.main(
            [
                "--security", self.security_path,
                "--reliability", self.reliability_path,
                "--pass-output", self.pass_output,
                "--blocked-output", self.blocked_output,
            ]
        )

    def test_safe_pass_removes_stale_blocked_artifact(self):
        # Simulate a stale CHANGE_BLOCKED artifact left over from a
        # previous, different run.
        self._write_json(self.blocked_output, {"status": "CHANGE_BLOCKED", "findings": [{"rule_id": "REL-001"}]})
        self._write_json(self.security_path, {"status": "PASS", "findings": []})
        self._write_json(self.reliability_path, {"status": "PASS", "findings": []})

        exit_code = self._run()

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.isfile(self.pass_output))
        self.assertFalse(
            os.path.isfile(self.blocked_output),
            "a safe PASS+PASS result must remove any stale change-blocked-result.json",
        )
        with open(self.pass_output) as f:
            verdict = json.load(f)
        self.assertEqual(verdict["status"], "SAFE_TO_SHIP")

    def test_safe_pass_with_no_stale_artifact_is_not_an_error(self):
        # blocked_output deliberately does not exist beforehand.
        self._write_json(self.security_path, {"status": "PASS", "findings": []})
        self._write_json(self.reliability_path, {"status": "PASS", "findings": []})

        exit_code = self._run()

        self.assertEqual(exit_code, 0)
        self.assertFalse(os.path.isfile(self.blocked_output))

    def test_blocked_result_replaces_stale_blocked_artifact(self):
        self._write_json(self.blocked_output, {"status": "CHANGE_BLOCKED", "findings": [{"rule_id": "STALE-999"}]})
        self._write_json(self.security_path, {"status": "FAIL", "findings": [{"rule_id": "SEC-001"}]})
        self._write_json(self.reliability_path, {"status": "PASS", "findings": []})

        exit_code = self._run()

        self.assertEqual(exit_code, 0)
        self.assertFalse(os.path.isfile(self.pass_output))
        with open(self.blocked_output) as f:
            blocked = json.load(f)
        self.assertEqual(blocked["status"], "CHANGE_BLOCKED")
        rule_ids = [finding.get("rule_id") for finding in blocked["findings"]]
        self.assertIn("SEC-001", rule_ids)
        self.assertNotIn("STALE-999", rule_ids, "the stale finding must not survive into the new blocked result")


if __name__ == "__main__":
    unittest.main()

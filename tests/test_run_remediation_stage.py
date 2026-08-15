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


class ValidateExecutionArtifactTestCase(unittest.TestCase):
    """Phase 8B transport correction: the authoritative per-finding result
    now comes from validating apply_remediation.py's --result-file
    artifact directly, independent of kiro-cli chat stdout."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="validate_execution_artifact_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.artifact_path = os.path.join(self.tmp_dir, "result.json")
        self.finding = {
            "rule_id": "REL-001",
            "severity": "HIGH",
            "resource": "aws_ecs_service.payments_api",
            "baseline_value": 3,
            "candidate_value": 1,
        }

    def _write_artifact(self, payload):
        with open(self.artifact_path, "w") as f:
            json.dump(payload, f)

    def test_missing_artifact_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)
        self.assertIn("did not produce", str(ctx.exception))

    def test_malformed_json_artifact_is_rejected(self):
        with open(self.artifact_path, "w") as f:
            f.write("{not valid json")
        with self.assertRaises(ValueError):
            run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)

    def test_non_remediated_status_is_rejected(self):
        self._write_artifact(
            {"status": "remediation_failed", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}
        )
        with self.assertRaises(ValueError):
            run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)

    def test_mismatched_rule_id_is_rejected(self):
        self._write_artifact(
            {"status": "remediated", "rule_id": "SEC-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}
        )
        with self.assertRaises(ValueError) as ctx:
            run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)
        self.assertIn("rule_id", str(ctx.exception))

    def test_mismatched_resource_is_rejected(self):
        self._write_artifact(
            {"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.other_api", "restored_value": 3}
        )
        with self.assertRaises(ValueError) as ctx:
            run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)
        self.assertIn("resource", str(ctx.exception))

    def test_mismatched_restored_value_is_rejected(self):
        self._write_artifact(
            {"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 99}
        )
        with self.assertRaises(ValueError) as ctx:
            run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)
        self.assertIn("restored_value", str(ctx.exception))

    def test_exact_match_is_accepted(self):
        self._write_artifact(
            {"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}
        )
        payload = run_remediation_stage._validate_execution_artifact(self.artifact_path, self.finding)
        self.assertEqual(payload["status"], "remediated")


class InvokeRemediatorTransportTestCase(unittest.TestCase):
    """End-to-end (subprocess mocked) reproduction of the exact real
    observed multi-JSON stdout shape, proving the corrected transport
    still succeeds because it validates the --result-file artifact
    rather than parsing that ambiguous stdout as authoritative."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="invoke_remediator_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.artifact_path = os.path.join(self.tmp_dir, "result.json")
        self.finding = {
            "rule_id": "REL-001",
            "severity": "HIGH",
            "resource": "aws_ecs_service.payments_api",
            "baseline_value": 3,
            "candidate_value": 1,
        }

    def test_valid_artifact_succeeds_despite_ambiguous_duplicate_json_stdout(self):
        # Exact real observed stdout shape: a valid result object,
        # ANSI-styled progress text, then a second JSON-shaped block.
        ambiguous_stdout = (
            'I will run the following command: python3 scripts/apply_remediation.py ... (using tool: shell)\n'
            '{"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}\n'
            '\x1b[38;5;244m - Completed in 0.75s\x1b[0m\n\n'
            '\x1b[38;5;141m> \x1b[0m{"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}'
        )
        with open(self.artifact_path, "w") as f:
            json.dump(
                {"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3},
                f,
            )

        fake_result = mock.Mock(returncode=0, stdout=ambiguous_stdout, stderr="")
        with mock.patch("subprocess.run", return_value=fake_result):
            payload = run_remediation_stage._invoke_remediator(self.finding, "terraform", 30.0, self.artifact_path)

        self.assertEqual(payload["status"], "remediated")
        self.assertEqual(payload["restored_value"], 3)

    def test_missing_artifact_fails_even_if_stdout_looks_successful(self):
        # The agent's chat stdout claims success, but apply_remediation.py
        # never actually produced the --result-file artifact -- this must
        # be rejected. Terraform side effects / stdout claims are never
        # treated as proof of success.
        clean_stdout = '{"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}'
        fake_result = mock.Mock(returncode=0, stdout=clean_stdout, stderr="")
        with mock.patch("subprocess.run", return_value=fake_result):
            with self.assertRaises(ValueError):
                run_remediation_stage._invoke_remediator(self.finding, "terraform", 30.0, self.artifact_path)

    def test_nonzero_exit_code_fails_before_checking_artifact(self):
        fake_result = mock.Mock(returncode=1, stdout="", stderr="some error")
        with mock.patch("subprocess.run", return_value=fake_result):
            with self.assertRaises(ValueError):
                run_remediation_stage._invoke_remediator(self.finding, "terraform", 30.0, self.artifact_path)


class RunRemediationStageEndToEndTestCase(unittest.TestCase):
    """run_remediation_stage() with subprocess mocked end-to-end,
    confirming a failed remediation-result (final_verdict.py's authority)
    correctly yields overall_status='failed' even under the ambiguous
    stdout shape, and that a genuinely successful remediation with the
    same ambiguous stdout still succeeds via the artifact contract."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="run_remediation_stage_e2e_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        # An isolated fake terraform directory inside tmp_dir, sibling to
        # a fake artifacts/ directory -- so _make_result_file_path's
        # sibling-of-terraform-dir convention (Phase 8C hardening) never
        # touches the real repository's terraform/ or artifacts/
        # directories during this test.
        self.terraform_dir = os.path.join(self.tmp_dir, "terraform")
        os.makedirs(self.terraform_dir, exist_ok=True)
        self.blocked_input_path = os.path.join(self.tmp_dir, "change-blocked-result.json")
        with open(self.blocked_input_path, "w") as f:
            json.dump(
                {
                    "status": "CHANGE_BLOCKED",
                    "findings": [
                        {
                            "rule_id": "REL-001",
                            "severity": "HIGH",
                            "resource": "aws_ecs_service.payments_api",
                            "baseline_value": 3,
                            "candidate_value": 1,
                        }
                    ],
                },
                f,
            )

    def test_successful_artifact_yields_remediated_status_despite_ambiguous_stdout(self):
        ambiguous_stdout = (
            '{"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}\n'
            '{"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}'
        )

        def fake_subprocess_run(argv_list, **kwargs):
            # Locate the --result-file path this invocation was given and
            # write the successful artifact there, exactly as
            # apply_remediation.py would on real success.
            prompt = argv_list[-1]
            result_file_line = [line for line in prompt.splitlines() if line.startswith("Result file: ")][0]
            result_file_path = result_file_line[len("Result file: "):]
            with open(result_file_path, "w") as f:
                json.dump(
                    {"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3},
                    f,
                )
            return mock.Mock(returncode=0, stdout=ambiguous_stdout, stderr="")

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            result = run_remediation_stage.run_remediation_stage(
                self.blocked_input_path, self.terraform_dir, 30.0
            )

        self.assertEqual(result["status"], "remediated")
        self.assertEqual(result["results"][0]["status"], "remediated")
        # The internal per-invocation artifact must be cleaned up after a
        # successful validated read -- it must never survive under the
        # isolated fake artifacts/ directory once the stage completes.
        fake_artifacts_dir = os.path.join(self.tmp_dir, "artifacts")
        if os.path.isdir(fake_artifacts_dir):
            self.assertEqual(os.listdir(fake_artifacts_dir), [])

    def test_missing_artifact_yields_failed_status(self):
        def fake_subprocess_run(argv_list, **kwargs):
            # kiro-cli exits 0 and claims success in stdout, but never
            # actually writes the --result-file artifact.
            return mock.Mock(
                returncode=0,
                stdout='{"status": "remediated", "rule_id": "REL-001", "resource": "aws_ecs_service.payments_api", "restored_value": 3}',
                stderr="",
            )

        with mock.patch("subprocess.run", side_effect=fake_subprocess_run):
            result = run_remediation_stage.run_remediation_stage(
                self.blocked_input_path, self.terraform_dir, 30.0
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["results"][0]["status"], "remediation_failed")


if __name__ == "__main__":
    unittest.main()

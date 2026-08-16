#!/usr/bin/env python3
"""Unit tests for scripts/run_agent_and_save.py's agent allow-list and its
reviewer artifact-based result transport (Phase 8D transport correction).

Covers the root cause discovered via a live Control Room smoke test: a
reviewer's `kiro-cli chat` stdout legitimately contains more than one
JSON-shaped fragment (the evidence-extraction tool's own JSON output,
progress text, and the reviewer's final ReviewResult JSON), so the
original "first `{` to last `}`" stdout-parsing heuristic decoded an
invalid combined span and failed. `--agent security-reviewer`/
`--agent reliability-reviewer` invocations now derive their result from a
dedicated internal artifact (`write_review_result.py`'s output),
independent of chat stdout -- these tests assert that transport directly,
with `subprocess.run` mocked so no real `kiro-cli` process is ever
started.

Uses only the Python 3 standard library `unittest` module.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import run_agent_and_save  # noqa: E402


class AgentAllowListTestCase(unittest.TestCase):
    def test_unsupported_agent_rejected_before_subprocess(self):
        with mock.patch("subprocess.run") as run_mock:
            exit_code = run_agent_and_save.main(
                ["--agent", "not-a-real-agent", "--prompt", "hi", "--output", "/tmp/should_not_be_written.json"]
            )
        self.assertEqual(exit_code, 1)
        run_mock.assert_not_called()

    def test_allowed_agents_are_exactly_the_three_reviewers_and_remediator(self):
        self.assertEqual(
            run_agent_and_save.ALLOWED_AGENTS,
            frozenset({"security-reviewer", "reliability-reviewer", "remediator"}),
        )

    def test_reviewer_agents_are_exactly_security_and_reliability(self):
        self.assertEqual(
            run_agent_and_save.REVIEWER_AGENTS,
            frozenset({"security-reviewer", "reliability-reviewer"}),
        )


class ReviewerArtifactTransportTestCase(unittest.TestCase):
    """End-to-end (subprocess mocked) tests of the reviewer artifact-based
    transport, reproducing the exact real observed stdout shape."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="run_agent_and_save_reviewer_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.output_path = os.path.join(self.tmp_dir, "security-review-result.json")

    def _fake_subprocess_that_writes_artifact(self, agent, review_result):
        """Return a fake `subprocess.run` side effect that locates the
        internal artifact path embedded in the prompt (mirroring what a
        real reviewer agent invoking write_review_result.py would do)
        and writes `review_result` there directly -- simulating a
        successful write_review_result.py invocation without actually
        spawning kiro-cli."""

        def fake_run(argv_list, **kwargs):
            prompt = argv_list[-1]
            marker = f"python3 scripts/write_review_result.py --agent {agent} --output "
            start = prompt.index(marker) + len(marker)
            end = prompt.index("\n", start)
            internal_path = prompt[start:end].strip()
            with open(internal_path, "w") as f:
                json.dump(review_result, f)
            # Exact real observed shape: evidence-extraction JSON, then
            # progress text, then the final ReviewResult JSON -- all in
            # one stdout stream.
            ambiguous_stdout = (
                '{"resource": "aws_security_group.payments_sg", "baseline": '
                '{"22": {"status": "AVAILABLE", "value": {"cidr_blocks": ["10.0.0.0/8"]}}}}\n'
                "Evaluating SEC-001 and SEC-002...\n"
                + json.dumps(review_result)
            )
            return mock.Mock(returncode=0, stdout=ambiguous_stdout, stderr="")

        return fake_run

    def test_ambiguous_stdout_with_multiple_json_fragments_does_not_prevent_success(self):
        # This reproduces the exact real observed failure mode: stdout
        # contains the evidence tool's own JSON plus the final
        # ReviewResult JSON. Because the internal artifact is valid, the
        # result must still be published successfully.
        review_result = {"agent": "security-reviewer", "status": "PASS", "findings": []}
        fake_run = self._fake_subprocess_that_writes_artifact("security-reviewer", review_result)

        with mock.patch("subprocess.run", side_effect=fake_run):
            exit_code = run_agent_and_save.main(
                [
                    "--agent", "security-reviewer",
                    "--prompt", "Compare baseline vs candidate.",
                    "--output", self.output_path,
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.isfile(self.output_path))
        with open(self.output_path) as f:
            self.assertEqual(json.load(f), review_result)

    def test_reliability_fail_finding_published_correctly_despite_ambiguous_stdout(self):
        review_result = {
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
        fake_run = self._fake_subprocess_that_writes_artifact("reliability-reviewer", review_result)
        output_path = os.path.join(self.tmp_dir, "reliability-review-result.json")

        with mock.patch("subprocess.run", side_effect=fake_run):
            exit_code = run_agent_and_save.main(
                [
                    "--agent", "reliability-reviewer",
                    "--prompt", "Compare baseline vs candidate.",
                    "--output", output_path,
                ]
            )

        self.assertEqual(exit_code, 0)
        with open(output_path) as f:
            written = json.load(f)
        self.assertEqual(written["status"], "FAIL")
        self.assertEqual(written["findings"][0]["rule_id"], "REL-001")

    def test_valid_looking_stdout_without_artifact_is_not_success(self):
        # The agent's chat stdout claims success (a clean, well-formed
        # ReviewResult JSON), but the internal artifact was never
        # actually written -- this must be rejected. Chat stdout claims
        # are never treated as proof of a persisted result.
        clean_stdout = json.dumps({"agent": "security-reviewer", "status": "PASS", "findings": []})
        fake_result = mock.Mock(returncode=0, stdout=clean_stdout, stderr="")

        with mock.patch("subprocess.run", return_value=fake_result):
            exit_code = run_agent_and_save.main(
                [
                    "--agent", "security-reviewer",
                    "--prompt", "Compare baseline vs candidate.",
                    "--output", self.output_path,
                ]
            )

        self.assertNotEqual(exit_code, 0)
        self.assertFalse(os.path.isfile(self.output_path))

    def test_malformed_internal_artifact_is_rejected(self):
        def fake_run(argv_list, **kwargs):
            prompt = argv_list[-1]
            marker = "python3 scripts/write_review_result.py --agent security-reviewer --output "
            start = prompt.index(marker) + len(marker)
            end = prompt.index("\n", start)
            internal_path = prompt[start:end].strip()
            with open(internal_path, "w") as f:
                f.write("{not valid json")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            exit_code = run_agent_and_save.main(
                [
                    "--agent", "security-reviewer",
                    "--prompt", "Compare baseline vs candidate.",
                    "--output", self.output_path,
                ]
            )

        self.assertNotEqual(exit_code, 0)
        self.assertFalse(os.path.isfile(self.output_path))

    def test_nonzero_kiro_cli_exit_does_not_short_circuit_artifact_check(self):
        # Even if kiro-cli's own exit code is non-zero (e.g. a later,
        # unrelated failure in the same chat turn), a successfully
        # written, valid internal artifact must still be published --
        # the exit code is diagnostic only, never authoritative.
        review_result = {"agent": "security-reviewer", "status": "PASS", "findings": []}

        def fake_run(argv_list, **kwargs):
            prompt = argv_list[-1]
            marker = "python3 scripts/write_review_result.py --agent security-reviewer --output "
            start = prompt.index(marker) + len(marker)
            end = prompt.index("\n", start)
            internal_path = prompt[start:end].strip()
            with open(internal_path, "w") as f:
                json.dump(review_result, f)
            return mock.Mock(returncode=1, stdout="", stderr="some unrelated later error")

        with mock.patch("subprocess.run", side_effect=fake_run):
            exit_code = run_agent_and_save.main(
                [
                    "--agent", "security-reviewer",
                    "--prompt", "Compare baseline vs candidate.",
                    "--output", self.output_path,
                ]
            )

        self.assertEqual(exit_code, 0)
        with open(self.output_path) as f:
            self.assertEqual(json.load(f), review_result)

    def test_internal_artifact_is_cleaned_up_after_success(self):
        review_result = {"agent": "security-reviewer", "status": "PASS", "findings": []}
        captured_internal_path = {}

        def fake_run(argv_list, **kwargs):
            prompt = argv_list[-1]
            marker = "python3 scripts/write_review_result.py --agent security-reviewer --output "
            start = prompt.index(marker) + len(marker)
            end = prompt.index("\n", start)
            internal_path = prompt[start:end].strip()
            captured_internal_path["path"] = internal_path
            with open(internal_path, "w") as f:
                json.dump(review_result, f)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            run_agent_and_save.main(
                [
                    "--agent", "security-reviewer",
                    "--prompt", "Compare baseline vs candidate.",
                    "--output", self.output_path,
                ]
            )

        self.assertFalse(os.path.isfile(captured_internal_path["path"]))


if __name__ == "__main__":
    unittest.main()

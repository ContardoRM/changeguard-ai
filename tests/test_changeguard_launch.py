#!/usr/bin/env python3
"""Unit tests for scripts/changeguard_launch.py.

All Gateway HTTP calls are mocked (via unittest.mock.patch on
`_http_json`) -- no live `kirocrew gateway` process is started or
contacted by this test module. Uses only the Python 3 standard library
`unittest` module.

Covers the mandatory scenarios from the Phase 8B corrections:
    - the launcher never calls execute before the force_approval update
      is confirmed;
    - the launcher does not execute if force_approval verification fails
      (response doesn't echo force_approval == true);
    - the launcher refuses to plan/execute Stage B at all when
      artifacts/change-blocked-result.json does not exist;
    - ambiguous (>1) or missing (0) remediation-node matches fail closed
      without ever calling execute.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import changeguard_launch  # noqa: E402


class PlanAndExecuteSupplyCrewRunnerAgentTestCase(unittest.TestCase):
    """Stage A and Stage B must explicitly supply agent=crew-runner on
    both the plan and execute calls -- never rely on Crew's default
    kirocrew-lite persona (confirmed live: TaskRunner has exactly one
    per-run agent for every task)."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="changeguard_launch_agent_test_")
        self.addCleanup(self._cleanup)
        self.workflow_path = os.path.join(self.tmp_dir, "workflow.yaml")
        with open(self.workflow_path, "w") as f:
            f.write("agents: {}\n")

    def _cleanup(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_agent_constant_is_crew_runner(self):
        self.assertEqual(changeguard_launch.CREW_RUNNER_AGENT, "crew-runner")

    def test_plan_workflow_defaults_agent_field_to_crew_runner(self):
        captured = {}

        def fake_http_json(url, method, payload, timeout, **kwargs):
            captured["payload"] = payload
            return {"task_id": "t1", "steps": []}

        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            changeguard_launch.plan_workflow("http://gw", self.workflow_path, 30.0)

        self.assertEqual(captured["payload"].get("agent"), "crew-runner")

    def test_execute_plan_defaults_agent_field_to_crew_runner(self):
        captured = {}

        def fake_http_json(url, method, payload, timeout, **kwargs):
            captured["payload"] = payload
            return {"ok": True}

        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            changeguard_launch.execute_plan("http://gw", "t1", 30.0)

        self.assertEqual(captured["payload"].get("agent"), "crew-runner")

    def test_stage_a_plan_and_execute_both_supply_crew_runner_agent(self):
        seen_agents = []

        def fake_http_json(url, method, payload, timeout, **kwargs):
            if isinstance(payload, dict) and "agent" in payload:
                seen_agents.append(payload["agent"])
            if url.endswith("/api/taskrunner/plan"):
                return {"task_id": "stage-a-1", "steps": []}
            if url.endswith("/execute"):
                return {"ok": True}
            raise AssertionError(f"unexpected call: {method} {url}")

        args = changeguard_launch.parse_args(
            [
                "--gateway-url", "http://gw",
                "--stage", "review",
                "--review-workflow", self.workflow_path,
                "--skip-cleanup",
            ]
        )
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_review_stage(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(seen_agents, ["crew-runner", "crew-runner"])


class FindTaskByNodeNameTestCase(unittest.TestCase):
    def test_matches_exactly_one_task(self):
        plan_response = {
            "steps": [
                {"index": 1, "description": "candidate-plan: ..."},
                {"index": 2, "description": "remediation: GATED NODE ..."},
            ]
        }
        index = changeguard_launch.find_task_by_node_name(plan_response, "remediation")
        self.assertEqual(index, 2)

    def test_no_match_raises(self):
        plan_response = {"steps": [{"index": 1, "description": "candidate-plan: ..."}]}
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(plan_response, "remediation")

    def test_ambiguous_match_raises(self):
        plan_response = {
            "steps": [
                {"index": 1, "description": "remediation: first"},
                {"index": 2, "description": "remediation: second"},
            ]
        }
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(plan_response, "remediation")

    def test_missing_steps_field_raises(self):
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name({}, "remediation")

    def test_matched_task_missing_index_raises(self):
        plan_response = {"steps": [{"description": "remediation: no index"}]}
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(plan_response, "remediation")


class DefaultRemediationNodeSelectorTestCase(unittest.TestCase):
    """Regression coverage for the live-smoke-test bug: the word
    'remediation' is NOT a safe default discriminator, because it also
    appears in the final-verdict task's description (which reads
    artifacts/remediation-result.json and describes itself as running
    "post-remediation" re-review). These tests use a decomposed-plan shape
    that mirrors the real .kiro/crew/changeguard-workflow-remediation.yaml
    task descriptions (as observed in a live plan response), not a
    simplified fixture, so a regression in either the default value or in
    decompose_yaml()'s own wording would be caught here."""

    # Descriptions intentionally mirror the real workflow's decomposed
    # task text closely enough to reproduce the ambiguity: every task
    # names an `artifacts/remediation-result.json`-adjacent word somewhere
    # except the ones that don't invoke run_remediation_stage.py.
    REMEDIATION_TASK_DESCRIPTION = (
        "Agent: crew-runner\nTimeout: 600\n\n"
        "Execute exactly this command and no other command: "
        "python3 scripts/run_remediation_stage.py "
        "--blocked-input artifacts/change-blocked-result.json "
        "--output artifacts/remediation-result.json --terraform-dir terraform"
    )
    REMEDIATED_PLAN_TASK_DESCRIPTION = (
        "Agent: crew-runner\nTimeout: 300\n\n"
        "Execute exactly this command and no other command: "
        "python3 scripts/run_tf_plan.py --terraform-dir terraform "
        "--output artifacts/remediated-plan.json"
    )
    SECURITY_RE_REVIEW_TASK_DESCRIPTION = (
        "Agent: crew-runner\nTimeout: 300\n\n"
        "Execute exactly this command and no other command: "
        "python3 scripts/run_agent_and_save.py --agent security-reviewer "
        '--prompt "Compare artifacts/baseline-plan.json against '
        'artifacts/remediated-plan.json..." '
        "--output artifacts/security-remediated-review-result.json"
    )
    RELIABILITY_RE_REVIEW_TASK_DESCRIPTION = (
        "Agent: crew-runner\nTimeout: 300\n\n"
        "Execute exactly this command and no other command: "
        "python3 scripts/run_agent_and_save.py --agent reliability-reviewer "
        '--prompt "Compare artifacts/baseline-plan.json against '
        'artifacts/remediated-plan.json..." '
        "--output artifacts/reliability-remediated-review-result.json"
    )
    FINAL_VERDICT_TASK_DESCRIPTION = (
        "Agent: crew-runner\nTimeout: 60\n\n"
        "Execute exactly this command and no other command: "
        "python3 scripts/final_verdict.py "
        "--security artifacts/security-remediated-review-result.json "
        "--reliability artifacts/reliability-remediated-review-result.json "
        "--plan-status success "
        "--remediation-result artifacts/remediation-result.json "
        "--output artifacts/final-verdict.json"
    )

    def _full_plan_response(self):
        return {
            "task_id": "task-42",
            "steps": [
                {"index": 1, "description": self.REMEDIATION_TASK_DESCRIPTION},
                {"index": 2, "description": self.REMEDIATED_PLAN_TASK_DESCRIPTION},
                {"index": 3, "description": self.SECURITY_RE_REVIEW_TASK_DESCRIPTION},
                {"index": 4, "description": self.RELIABILITY_RE_REVIEW_TASK_DESCRIPTION},
                {"index": 5, "description": self.FINAL_VERDICT_TASK_DESCRIPTION},
            ],
        }

    def test_default_selector_constant_is_the_script_name(self):
        # Locks in the fixed default so a future edit cannot silently
        # regress it back to the ambiguous "remediation" word without
        # this test failing.
        args = changeguard_launch.parse_args(
            ["--gateway-url", "http://gw", "--stage", "remediation"]
        )
        self.assertEqual(args.remediation_node, "run_remediation_stage.py")

    def test_default_selector_matches_only_the_remediation_task(self):
        """The word 'remediation' alone is ambiguous (matches the gated
        node AND final-verdict), but the CLI's actual default value
        ('run_remediation_stage.py') must resolve to exactly the gated
        remediation task, index 1, and nothing else."""
        plan_response = self._full_plan_response()
        index = changeguard_launch.find_task_by_node_name(
            plan_response, "run_remediation_stage.py"
        )
        self.assertEqual(index, 1)

    def test_bare_remediation_word_is_ambiguous_against_the_real_shape(self):
        """Demonstrates the exact bug found in the live smoke test: matching
        on the bare word 'remediation' against this realistic decomposed
        plan is ambiguous (remediation task + final-verdict task both
        contain it) and must fail closed, never guess."""
        plan_response = self._full_plan_response()
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(plan_response, "remediation")

    def test_final_verdict_task_alone_does_not_match_default_selector(self):
        """The final-verdict task's own description, in isolation, must
        not match the fixed script-name discriminator -- proving the new
        default does not merely get lucky when the ambiguous task is
        present, it is genuinely specific to the remediation task."""
        plan_response = {
            "steps": [{"index": 5, "description": self.FINAL_VERDICT_TASK_DESCRIPTION}]
        }
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(
                plan_response, "run_remediation_stage.py"
            )

    def test_zero_matches_still_fails_closed(self):
        plan_response = {
            "steps": [{"index": 2, "description": self.REMEDIATED_PLAN_TASK_DESCRIPTION}]
        }
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(
                plan_response, "run_remediation_stage.py"
            )

    def test_multiple_matches_of_the_new_selector_still_fail_closed(self):
        """Even with the more specific discriminator, if some future DAG
        edit reintroduces ambiguity (e.g. two tasks both naming the
        script), the function must still refuse to guess -- coverage that
        the fail-closed behavior is not weakened by narrowing the
        default string."""
        plan_response = {
            "steps": [
                {"index": 1, "description": self.REMEDIATION_TASK_DESCRIPTION},
                {
                    "index": 6,
                    "description": (
                        "Agent: crew-runner\nTimeout: 10\n\n"
                        "Execute exactly this command and no other command: "
                        "python3 scripts/run_remediation_stage.py --dry-run"
                    ),
                },
            ]
        }
        with self.assertRaises(RuntimeError):
            changeguard_launch.find_task_by_node_name(
                plan_response, "run_remediation_stage.py"
            )

    def test_end_to_end_stage_b_applies_force_approval_only_to_remediation_task(self):
        """Full run_remediation_stage() sequence against the realistic
        five-task plan: force_approval must be PATCHed to task index 1
        (the real remediation task) and to no other index, and execute
        must only be called after that PATCH is verified true."""
        tmp_dir = tempfile.mkdtemp(prefix="changeguard_launch_default_selector_test_")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp_dir, ignore_errors=True))
        blocked_path = os.path.join(tmp_dir, "change-blocked-result.json")
        with open(blocked_path, "w") as f:
            f.write('{"status": "CHANGE_BLOCKED", "findings": []}')
        workflow_path = os.path.join(tmp_dir, "remediation-workflow.yaml")
        with open(workflow_path, "w") as f:
            f.write("agents: {}\n")

        plan_response = self._full_plan_response()
        patched_indices = []
        call_order = []

        def fake_http_json(url, method, payload, timeout, **kwargs):
            call_order.append((method, url))
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            if method == "PATCH":
                # URL shape: .../api/taskrunner/{task_id}/tasks/{index}
                patched_indices.append(int(url.rstrip("/").split("/")[-1]))
                return {"force_approval": True}
            if method == "POST" and url.endswith("/execute"):
                return {"ok": True}
            raise AssertionError(f"unexpected call: {method} {url}")

        args = changeguard_launch.parse_args(
            [
                "--gateway-url", "http://gw",
                "--stage", "remediation",
                "--blocked-artifact", blocked_path,
                "--remediation-workflow", workflow_path,
            ]
        )
        self.assertEqual(args.remediation_node, "run_remediation_stage.py")

        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(patched_indices, [1])  # only the real remediation task, index 1
        methods_in_order = [entry[0] for entry in call_order]
        patch_pos = methods_in_order.index("PATCH")
        execute_pos = next(i for i, (m, u) in enumerate(call_order) if u.endswith("/execute"))
        self.assertLess(patch_pos, execute_pos)


class SetAndVerifyForceApprovalTestCase(unittest.TestCase):
    def test_verified_true_returns_response(self):
        with mock.patch.object(changeguard_launch, "_http_json", return_value={"force_approval": True}) as mocked:
            response = changeguard_launch.set_and_verify_force_approval("http://gw", "task1", 2, 30.0)
        self.assertTrue(response["force_approval"])
        mocked.assert_called_once_with(
            "http://gw/api/taskrunner/task1/tasks/2", "PATCH", {"force_approval": True}, 30.0,
            internal_secret="",
        )

    def test_verification_failure_raises_and_does_not_confirm(self):
        with mock.patch.object(changeguard_launch, "_http_json", return_value={"force_approval": False}):
            with self.assertRaises(RuntimeError):
                changeguard_launch.set_and_verify_force_approval("http://gw", "task1", 2, 30.0)

    def test_missing_field_in_response_raises(self):
        with mock.patch.object(changeguard_launch, "_http_json", return_value={}):
            with self.assertRaises(RuntimeError):
                changeguard_launch.set_and_verify_force_approval("http://gw", "task1", 2, 30.0)


class RunRemediationStageGateTestCase(unittest.TestCase):
    """Verify the full Stage B sequence never calls execute before a
    verified force_approval, and refuses entirely without a blocked
    artifact."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="changeguard_launch_test_")
        self.addCleanup(self._cleanup)
        self.blocked_path = os.path.join(self.tmp_dir, "change-blocked-result.json")
        # plan_workflow() reads this file's text before ever contacting the
        # gateway, so it must exist even though its YAML content is
        # irrelevant to these tests (the gateway response is mocked).
        self.workflow_path = os.path.join(self.tmp_dir, "remediation-workflow.yaml")
        with open(self.workflow_path, "w") as f:
            f.write("agents: {}\n")

    def _cleanup(self):
        import shutil

        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_args(self, **overrides):
        args = changeguard_launch.parse_args(
            [
                "--gateway-url",
                "http://gw",
                "--stage",
                "remediation",
                "--blocked-artifact",
                self.blocked_path,
                "--remediation-workflow",
                self.workflow_path,
            ]
        )
        for key, value in overrides.items():
            setattr(args, key, value)
        return args

    def test_refuses_without_blocked_artifact(self):
        # blocked_path deliberately does not exist.
        args = self._make_args()
        with mock.patch.object(changeguard_launch, "plan_workflow") as plan_mock, \
             mock.patch.object(changeguard_launch, "execute_plan") as execute_mock:
            exit_code = changeguard_launch.run_remediation_stage(args)
        self.assertEqual(exit_code, 1)
        plan_mock.assert_not_called()
        execute_mock.assert_not_called()

    def test_execute_never_called_before_verified_force_approval(self):
        with open(self.blocked_path, "w") as f:
            f.write('{"status": "CHANGE_BLOCKED", "findings": []}')

        plan_response = {
            "task_id": "task-42",
            "steps": [
                {
                    "index": 1,
                    "description": (
                        "remediation: gated node -- runs "
                        "python3 scripts/run_remediation_stage.py"
                    ),
                }
            ],
        }
        call_order = []

        def fake_http_json(url, method, payload, timeout, **kwargs):
            call_order.append((method, url))
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            if method == "PATCH":
                return {"force_approval": True}
            if method == "POST" and url.endswith("/execute"):
                return {"ok": True}
            raise AssertionError(f"unexpected call: {method} {url}")

        args = self._make_args()
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 0)
        # The PATCH (force_approval update) must occur strictly before the
        # POST .../execute call.
        methods_in_order = [entry[0] for entry in call_order]
        patch_index = methods_in_order.index("PATCH")
        execute_index = next(i for i, (m, u) in enumerate(call_order) if u.endswith("/execute"))
        self.assertLess(patch_index, execute_index)

    def test_execute_not_called_when_verification_fails(self):
        with open(self.blocked_path, "w") as f:
            f.write('{"status": "CHANGE_BLOCKED", "findings": []}')

        plan_response = {
            "task_id": "task-42",
            "steps": [{"index": 1, "description": "remediation: gated node"}],
        }

        def fake_http_json(url, method, payload, timeout, **kwargs):
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            if method == "PATCH":
                return {"force_approval": False}  # verification will fail
            raise AssertionError(f"execute must never be called: {method} {url}")

        args = self._make_args()
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 1)

    def test_stage_b_plan_and_execute_both_supply_crew_runner_agent(self):
        with open(self.blocked_path, "w") as f:
            f.write('{"status": "CHANGE_BLOCKED", "findings": []}')

        plan_response = {
            "task_id": "task-42",
            "steps": [
                {
                    "index": 1,
                    "description": (
                        "remediation: gated node -- runs "
                        "python3 scripts/run_remediation_stage.py"
                    ),
                }
            ],
        }
        seen_agents = []

        def fake_http_json(url, method, payload, timeout, **kwargs):
            if isinstance(payload, dict) and "agent" in payload:
                seen_agents.append(payload["agent"])
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            if method == "PATCH":
                return {"force_approval": True}
            if method == "POST" and url.endswith("/execute"):
                return {"ok": True}
            raise AssertionError(f"unexpected call: {method} {url}")

        args = self._make_args()
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(seen_agents, ["crew-runner", "crew-runner"])

    def test_execute_not_called_when_no_task_matches(self):
        with open(self.blocked_path, "w") as f:
            f.write('{"status": "CHANGE_BLOCKED", "findings": []}')

        plan_response = {"task_id": "task-42", "steps": [{"index": 1, "description": "candidate-plan: no match here"}]}

        def fake_http_json(url, method, payload, timeout, **kwargs):
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            raise AssertionError(f"must not reach PATCH/execute: {method} {url}")

        args = self._make_args()
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()

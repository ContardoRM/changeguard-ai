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


class SetAndVerifyForceApprovalTestCase(unittest.TestCase):
    def test_verified_true_returns_response(self):
        with mock.patch.object(changeguard_launch, "_http_json", return_value={"force_approval": True}) as mocked:
            response = changeguard_launch.set_and_verify_force_approval("http://gw", "task1", 2, 30.0)
        self.assertTrue(response["force_approval"])
        mocked.assert_called_once_with(
            "http://gw/api/taskrunner/task1/tasks/2", "PATCH", {"force_approval": True}, 30.0
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
            "steps": [{"index": 1, "description": "remediation: gated node"}],
        }
        call_order = []

        def fake_http_json(url, method, payload, timeout):
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

        def fake_http_json(url, method, payload, timeout):
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            if method == "PATCH":
                return {"force_approval": False}  # verification will fail
            raise AssertionError(f"execute must never be called: {method} {url}")

        args = self._make_args()
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 1)

    def test_execute_not_called_when_no_task_matches(self):
        with open(self.blocked_path, "w") as f:
            f.write('{"status": "CHANGE_BLOCKED", "findings": []}')

        plan_response = {"task_id": "task-42", "steps": [{"index": 1, "description": "candidate-plan: no match here"}]}

        def fake_http_json(url, method, payload, timeout):
            if method == "POST" and url.endswith("/api/taskrunner/plan"):
                return plan_response
            raise AssertionError(f"must not reach PATCH/execute: {method} {url}")

        args = self._make_args()
        with mock.patch.object(changeguard_launch, "_http_json", side_effect=fake_http_json):
            exit_code = changeguard_launch.run_remediation_stage(args)

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()

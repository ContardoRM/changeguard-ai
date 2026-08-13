#!/usr/bin/env python3
"""Unit tests for scripts/run_tf_plan.py's allow-list guard and error handling.

Covers:
    - Command allow-list enforcement (subprocess never invoked for a
      disallowed subcommand).
    - A failed pipeline step prevents any write to --output, and leaves a
      pre-existing --output file completely untouched.
    - A successful run overwrites a pre-existing --output file byte-for-byte
      with the final `terraform show -json` stdout.
    - No subprocess.run call in the pipeline ever uses a shell string or
      shell=True.
    - The temporary binary plan file is removed after both a successful and
      a failed run.
    - The success-path stdout message has the documented JSON shape.

Uses only the Python 3 standard library `unittest` module, per Requirement
12.1. All Terraform subprocess calls are mocked; no real Terraform binary is
invoked by this test module.

_Requirements: 2.3, 2.4, 11.6_
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

# The project has no packaging setup, so make the project root importable
# as a plain path insertion rather than relying on an installed package.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import run_tf_plan  # noqa: E402


def _completed(returncode=0, stdout="", stderr=""):
    """Build a stand-in for subprocess.CompletedProcess used by mocks."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


class RunTerraformCommandAllowListTests(unittest.TestCase):
    """Requirement 11.6: the allow-list guard rejects disallowed subcommands
    before subprocess.run is ever called."""

    def test_disallowed_subcommand_raises_before_invocation(self):
        for subcommand in ("apply", "destroy", "totally-made-up-subcommand"):
            with self.subTest(subcommand=subcommand):
                with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                    with self.assertRaises(run_tf_plan.DisallowedSubcommandError):
                        run_tf_plan.run_terraform_command(["terraform", subcommand])
                    mock_run.assert_not_called()

    def test_run_terraform_command_direct_call_never_reaches_subprocess(self):
        """Reinforces the allow-list test above by calling
        run_terraform_command directly (not through main()) with a
        disallowed subcommand and asserting subprocess.run was never
        invoked."""
        with patch.object(run_tf_plan.subprocess, "run") as mock_run:
            with self.assertRaises(run_tf_plan.DisallowedSubcommandError):
                run_tf_plan.run_terraform_command(["terraform", "apply", "-auto-approve"])
            mock_run.assert_not_called()

    def test_allowed_subcommands_do_reach_subprocess(self):
        """Sanity check: every member of the allow-list is actually passed
        through to subprocess.run (i.e. the guard isn't overly strict)."""
        for subcommand in sorted(run_tf_plan.ALLOWED_SUBCOMMANDS):
            with self.subTest(subcommand=subcommand):
                with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                    mock_run.return_value = _completed(returncode=0)
                    run_tf_plan.run_terraform_command(["terraform", subcommand])
                    mock_run.assert_called_once()


class MainCommandFailureTests(unittest.TestCase):
    """Requirements 2.3/2.4: a failed subcommand aborts before any write to
    --output, whether or not an output file already existed."""

    def test_failure_with_no_pre_existing_output_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "candidate-plan.json")
            self.assertFalse(os.path.exists(output_path))

            with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                mock_run.return_value = _completed(returncode=1, stderr="init failed")
                rc = run_tf_plan.main(
                    ["--terraform-dir", tmp_dir, "--output", output_path]
                )

            self.assertNotEqual(rc, 0)
            self.assertFalse(os.path.exists(output_path))

    def test_failure_with_pre_existing_output_leaves_it_untouched(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "candidate-plan.json")
            stale_content = '{"stale": "prior-run-content"}'
            with open(output_path, "w") as f:
                f.write(stale_content)

            with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                mock_run.return_value = _completed(returncode=1, stderr="init failed")
                rc = run_tf_plan.main(
                    ["--terraform-dir", tmp_dir, "--output", output_path]
                )

            self.assertNotEqual(rc, 0)
            with open(output_path) as f:
                self.assertEqual(f.read(), stale_content)


class MainOverwriteTests(unittest.TestCase):
    """Artifact Lifecycle overwrite behavior: a successful run overwrites a
    pre-existing --output file byte-for-byte with the final `terraform show
    -json` stdout."""

    def test_successful_run_overwrites_existing_output(self):
        show_json_stdout = '{"format_version": "1.2", "resource_changes": []}'

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "candidate-plan.json")
            with open(output_path, "w") as f:
                f.write('{"stale": "prior-run-content"}')

            with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                mock_run.return_value = _completed(
                    returncode=0, stdout=show_json_stdout
                )
                rc = run_tf_plan.main(
                    ["--terraform-dir", tmp_dir, "--output", output_path]
                )

            self.assertEqual(rc, 0)
            with open(output_path) as f:
                self.assertEqual(f.read(), show_json_stdout)


class MainNoShellExecutionTests(unittest.TestCase):
    """No subprocess.run call anywhere in the pipeline uses a shell string
    or shell=True."""

    def test_no_call_uses_shell_or_a_string_argv(self):
        show_json_stdout = '{"format_version": "1.2"}'

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "candidate-plan.json")

            with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                mock_run.return_value = _completed(
                    returncode=0, stdout=show_json_stdout
                )
                rc = run_tf_plan.main(
                    ["--terraform-dir", tmp_dir, "--output", output_path]
                )

            self.assertEqual(rc, 0)
            self.assertTrue(mock_run.call_args_list, "expected subprocess.run to be called")
            for call in mock_run.call_args_list:
                args, kwargs = call
                self.assertTrue(args, "subprocess.run must receive argv as a positional arg")
                self.assertIsInstance(
                    args[0], list, "subprocess.run's argv must be a list, never a string"
                )
                self.assertFalse(
                    kwargs.get("shell", False),
                    "subprocess.run must never be called with shell=True",
                )


class MainTempPlanFileCleanupTests(unittest.TestCase):
    """The temporary binary plan file created via tempfile.mkstemp is
    removed after both a successful and a failed run."""

    def _run_with_captured_temp_path(self, returncode, stdout=""):
        captured = {}
        original_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            fd, path = original_mkstemp(*args, **kwargs)
            captured["path"] = path
            return fd, path

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "candidate-plan.json")
            with patch.object(
                run_tf_plan.tempfile, "mkstemp", side_effect=spy_mkstemp
            ), patch.object(run_tf_plan.subprocess, "run") as mock_run:
                mock_run.return_value = _completed(returncode=returncode, stdout=stdout)
                rc = run_tf_plan.main(
                    ["--terraform-dir", tmp_dir, "--output", output_path]
                )
        return rc, captured.get("path")

    def test_temp_plan_file_removed_after_success(self):
        rc, temp_path = self._run_with_captured_temp_path(returncode=0, stdout="{}")
        self.assertEqual(rc, 0)
        self.assertIsNotNone(temp_path)
        self.assertFalse(os.path.exists(temp_path))

    def test_temp_plan_file_removed_after_failure(self):
        rc, temp_path = self._run_with_captured_temp_path(returncode=1)
        self.assertNotEqual(rc, 0)
        self.assertIsNotNone(temp_path)
        self.assertFalse(os.path.exists(temp_path))


class MainSuccessStdoutShapeTests(unittest.TestCase):
    """The success-path stdout message has the documented JSON shape:
    {"status": "success", "plan": "<output path>"}."""

    def test_success_stdout_json_message_shape(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = os.path.join(tmp_dir, "candidate-plan.json")

            captured_stdout = io.StringIO()
            with patch.object(run_tf_plan.subprocess, "run") as mock_run:
                mock_run.return_value = _completed(returncode=0, stdout="{}")
                with redirect_stdout(captured_stdout):
                    rc = run_tf_plan.main(
                        ["--terraform-dir", tmp_dir, "--output", output_path]
                    )

            self.assertEqual(rc, 0)
            printed = captured_stdout.getvalue().strip()
            message = json.loads(printed)
            self.assertEqual(message, {"status": "success", "plan": output_path})


if __name__ == "__main__":
    unittest.main()

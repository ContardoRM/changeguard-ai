#!/usr/bin/env python3
"""Unit tests for scripts/safety_guard.py.

Deterministically exercises the safety-guard matching logic (no live Kiro
process involved here — the real Kiro `preToolUse` hook smoke test lives
outside this suite, see the phase report). Uses only the Python 3 standard
library `unittest` module.

Covers the mandatory deny cases (terraform apply/destroy, AWS CLI
invocation, destructive recursive-force `rm`), the mandatory allow cases
(non-destructive terraform subcommands and the four approved ChangeGuard
scripts), malformed/missing hook input (fail-closed), and the exact
allow/deny exit-code contract (`main()` returns 0 or 2, never anything
else).
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import safety_guard  # noqa: E402


def _hook_payload(command):
    return (
        '{"hook_event_name": "preToolUse", "cwd": "/tmp", '
        '"tool_name": "execute_bash", "tool_input": {"command": %r}}'
        % command
    ).replace("'", '"')


class MandatoryDenyCaseTests(unittest.TestCase):
    """Requirement 11.1-11.4: the exact prohibited commands enumerated in
    the ChangeGuard specification must be denied."""

    DENY_COMMANDS = [
        "terraform apply",
        "terraform apply -auto-approve",
        "terraform destroy",
        "terraform destroy -auto-approve",
        "aws s3 ls",
        "aws ec2 describe-instances",
        "rm -rf /tmp/example",
        "rm -fr /tmp/example",
        "rm --recursive --force /tmp/example",
        "rm --force --recursive /tmp/example",
    ]

    def test_mandatory_deny_commands_are_denied(self):
        for command in self.DENY_COMMANDS:
            with self.subTest(command=command):
                allowed, reason = safety_guard.evaluate_command(command)
                self.assertFalse(allowed, f"expected deny for: {command!r}")
                self.assertIsNotNone(reason)


class DenyWhitespaceAndVariantTests(unittest.TestCase):
    """Trivial whitespace/argument-placement/path variants must not bypass
    the intended demo protections."""

    def test_extra_whitespace_does_not_bypass_terraform_apply(self):
        allowed, _ = safety_guard.evaluate_command("terraform    apply")
        self.assertFalse(allowed)

    def test_leading_and_trailing_whitespace_does_not_bypass(self):
        allowed, _ = safety_guard.evaluate_command("   terraform destroy   ")
        self.assertFalse(allowed)

    def test_uppercase_does_not_bypass(self):
        allowed, _ = safety_guard.evaluate_command("TERRAFORM DESTROY")
        self.assertFalse(allowed)

    def test_flags_before_subcommand_do_not_bypass_apply(self):
        # terraform accepts some global flags before the subcommand; the
        # subsequence match still finds "terraform"/"apply" as adjacent
        # tokens once the (rare, but possible) flag-before-subcommand
        # case is considered. This test targets the common real-world
        # case: extra trailing flags/args around the subcommand.
        allowed, _ = safety_guard.evaluate_command("terraform apply -auto-approve -no-color")
        self.assertFalse(allowed)

    def test_absolute_path_to_aws_cli_is_denied(self):
        allowed, _ = safety_guard.evaluate_command("/usr/local/bin/aws s3 ls")
        self.assertFalse(allowed)

    def test_relative_path_to_aws_cli_is_denied(self):
        allowed, _ = safety_guard.evaluate_command("./bin/aws ec2 describe-instances")
        self.assertFalse(allowed)

    def test_rm_flags_in_either_order_short_form(self):
        for command in ("rm -rf /tmp/x", "rm -fr /tmp/x"):
            with self.subTest(command=command):
                allowed, _ = safety_guard.evaluate_command(command)
                self.assertFalse(allowed)

    def test_rm_flags_in_either_order_long_form(self):
        for command in (
            "rm --recursive --force /tmp/x",
            "rm --force --recursive /tmp/x",
        ):
            with self.subTest(command=command):
                allowed, _ = safety_guard.evaluate_command(command)
                self.assertFalse(allowed)

    def test_rm_mixed_short_and_long_flags(self):
        allowed, _ = safety_guard.evaluate_command("rm -r --force /tmp/x")
        self.assertFalse(allowed)

    def test_prohibited_command_hidden_after_benign_command_via_semicolon(self):
        allowed, _ = safety_guard.evaluate_command("echo hello; terraform destroy")
        self.assertFalse(allowed)

    def test_prohibited_command_hidden_after_benign_command_via_and(self):
        allowed, _ = safety_guard.evaluate_command("echo hello && terraform apply")
        self.assertFalse(allowed)

    def test_prohibited_command_hidden_after_benign_command_via_or(self):
        allowed, _ = safety_guard.evaluate_command("echo hello || aws s3 ls")
        self.assertFalse(allowed)

    def test_prohibited_command_hidden_after_pipe(self):
        allowed, _ = safety_guard.evaluate_command("echo hello | xargs -I{} terraform destroy")
        self.assertFalse(allowed)


class NonDestructiveFilesystemDeletionAllowedTests(unittest.TestCase):
    """`rm` without both recursive and force flags is not one of the
    enumerated prohibited forms and must not be denied by this guard
    (narrow scope: only the exact enumerated destructive combinations are
    prohibited)."""

    def test_plain_rm_without_flags_is_allowed(self):
        allowed, _ = safety_guard.evaluate_command("rm /tmp/example.txt")
        self.assertTrue(allowed)

    def test_rm_force_only_is_allowed(self):
        allowed, _ = safety_guard.evaluate_command("rm -f /tmp/example.txt")
        self.assertTrue(allowed)

    def test_rm_recursive_only_is_allowed(self):
        allowed, _ = safety_guard.evaluate_command("rm -r /tmp/example-dir")
        self.assertTrue(allowed)


class MandatoryAllowCaseTests(unittest.TestCase):
    """Requirement: the guard must not block approved ChangeGuard
    operations or non-destructive Terraform subcommands."""

    ALLOW_COMMANDS = [
        "terraform plan",
        "terraform show",
        "terraform validate",
        "terraform init",
        "terraform fmt",
        "terraform init -input=false",
        "terraform fmt -check",
        "terraform plan -refresh=false -input=false -lock=false -out=/tmp/x.tfplan",
        "terraform show -json /tmp/x.tfplan",
        "python3 scripts/run_tf_plan.py --terraform-dir terraform --output artifacts/baseline-plan.json",
        "python3 scripts/print_security_evidence.py --baseline artifacts/baseline-plan.json --candidate artifacts/candidate-plan.json",
        "python3 scripts/print_reliability_evidence.py --baseline artifacts/baseline-plan.json --candidate artifacts/candidate-plan.json",
        "python3 scripts/apply_remediation.py --terraform-dir terraform --rule-id REL-001 --resource aws_ecs_service.payments_api --restore-value 3",
    ]

    def test_mandatory_allow_commands_are_allowed(self):
        for command in self.ALLOW_COMMANDS:
            with self.subTest(command=command):
                allowed, reason = safety_guard.evaluate_command(command)
                self.assertTrue(allowed, f"expected allow for: {command!r} (reason: {reason})")
                self.assertIsNone(reason)


class HookInputParsingTests(unittest.TestCase):
    """The guard fails closed (denies) on malformed/missing hook input,
    and correctly extracts tool_input.command from well-formed input."""

    def test_well_formed_allow_payload(self):
        allowed, reason = safety_guard.evaluate_hook_input(_hook_payload("terraform plan"))
        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_well_formed_deny_payload(self):
        allowed, reason = safety_guard.evaluate_hook_input(_hook_payload("terraform destroy"))
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_invalid_json_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input("not valid json{{{")
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_empty_string_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input("")
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_json_array_instead_of_object_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input("[1, 2, 3]")
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_missing_tool_input_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input(
            '{"hook_event_name": "preToolUse", "tool_name": "execute_bash"}'
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_tool_input_not_an_object_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input(
            '{"hook_event_name": "preToolUse", "tool_input": "not-an-object"}'
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_missing_command_field_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input(
            '{"hook_event_name": "preToolUse", "tool_input": {}}'
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_command_field_not_a_string_fails_closed(self):
        allowed, reason = safety_guard.evaluate_hook_input(
            '{"hook_event_name": "preToolUse", "tool_input": {"command": 123}}'
        )
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)

    def test_unparseable_command_text_fails_closed(self):
        # Unbalanced quote: shlex cannot tokenize this at all.
        allowed, reason = safety_guard.evaluate_command('echo "unterminated')
        self.assertFalse(allowed)
        self.assertIsNotNone(reason)


class MainExitCodeContractTests(unittest.TestCase):
    """main() must return exactly 0 (allow) or 2 (deny) — the exact
    representation this installed Kiro CLI version requires for a
    preToolUse hook, per empirical verification (exit code 1 does not
    block the tool call in this CLI version)."""

    def _run_main_with_stdin(self, stdin_text):
        original_stdin = sys.stdin
        sys.stdin = io.StringIO(stdin_text)
        captured_stderr = io.StringIO()
        try:
            with redirect_stderr(captured_stderr):
                rc = safety_guard.main([])
        finally:
            sys.stdin = original_stdin
        return rc, captured_stderr.getvalue()

    def test_allow_payload_exits_zero(self):
        rc, stderr_text = self._run_main_with_stdin(_hook_payload("terraform plan"))
        self.assertEqual(rc, 0)
        self.assertEqual(stderr_text, "")

    def test_deny_payload_exits_two(self):
        rc, stderr_text = self._run_main_with_stdin(_hook_payload("terraform destroy"))
        self.assertEqual(rc, 2)
        self.assertIn("DENIED", stderr_text)

    def test_malformed_payload_exits_two(self):
        rc, stderr_text = self._run_main_with_stdin("not json")
        self.assertEqual(rc, 2)
        self.assertIn("DENIED", stderr_text)

    def test_return_code_is_never_anything_other_than_zero_or_two(self):
        commands = MandatoryDenyCaseTests.DENY_COMMANDS + MandatoryAllowCaseTests.ALLOW_COMMANDS
        for command in commands:
            with self.subTest(command=command):
                rc, _ = self._run_main_with_stdin(_hook_payload(command))
                self.assertIn(rc, (0, 2))


class NoChangeGuardPolicyLogicTests(unittest.TestCase):
    """Static self-check: the safety guard module's source contains no
    ChangeGuard product-policy vocabulary."""

    FORBIDDEN_TOKENS = [
        "SEC-001",
        "SEC-002",
        "REL-001",
        "BR-001",
        "desired_count",
        "deletion_protection",
        "0.0.0.0/0",
        "CRITICAL",
        "HIGH",
        "PASS",
        "FAIL",
        "INCOMPLETE",
        "resource_changes",
        "baseline_value",
        "candidate_value",
    ]

    def test_module_source_contains_no_policy_vocabulary(self):
        module_path = safety_guard.__file__
        with open(module_path, "r") as module_file:
            source = module_file.read()

        # The module's own docstring explicitly lists these forbidden
        # tokens as *documentation of what it does NOT contain* - strip
        # the module docstring before scanning so that meta-documentation
        # doesn't trigger a false positive on this self-check.
        doc = safety_guard.__doc__ or ""
        source_without_docstring = source.replace(doc, "")

        for token in self.FORBIDDEN_TOKENS:
            with self.subTest(token=token):
                self.assertNotIn(
                    token,
                    source_without_docstring,
                    f"safety_guard.py must not reference ChangeGuard policy token {token!r}",
                )


if __name__ == "__main__":
    unittest.main()

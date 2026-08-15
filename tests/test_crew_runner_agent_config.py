#!/usr/bin/env python3
"""Deterministic configuration tests for .kiro/agents/crew-runner.json.

Covers the architecture correction required by the confirmed live Kiro
Crew 0.2.0 execution semantics (SHELL_IS_LLM_INTERPRETED_PROMPT_TEXT):
every ChangeGuard DAG task now executes as an LLM/ACP turn against the
run-scoped `crew-runner` Kiro CLI agent, so that agent's own permission
configuration is the safety boundary for what actually gets executed.
Uses only the Python 3 standard library `unittest` module and static
JSON/text inspection -- no live kiro-cli/gateway process is started.
"""

import json
import os
import re
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AGENT_CONFIG_PATH = os.path.join(REPO_ROOT, ".kiro", "agents", "crew-runner.json")
AGENT_PROMPT_PATH = os.path.join(REPO_ROOT, ".kiro", "agents", "crew-runner-prompt.md")

STAGE_A_YAML_PATH = os.path.join(REPO_ROOT, ".kiro", "crew", "changeguard-workflow.yaml")
STAGE_B_YAML_PATH = os.path.join(REPO_ROOT, ".kiro", "crew", "changeguard-workflow-remediation.yaml")

ALLOWED_TRANSPORT_SCRIPTS = (
    "python3 scripts/run_tf_plan.py",
    "python3 scripts/run_agent_and_save.py",
    "python3 scripts/aggregate_review.py",
    "python3 scripts/run_remediation_stage.py",
    "python3 scripts/final_verdict.py",
    "python3 scripts/cleanup_run_artifacts.py",
)


def _load_agent_config():
    with open(AGENT_CONFIG_PATH) as f:
        return json.load(f)


class CrewRunnerAgentExistsTestCase(unittest.TestCase):
    def test_agent_config_file_exists_and_is_valid_json(self):
        self.assertTrue(os.path.isfile(AGENT_CONFIG_PATH))
        config = _load_agent_config()
        self.assertEqual(config.get("name"), "crew-runner")

    def test_agent_prompt_file_exists(self):
        self.assertTrue(os.path.isfile(AGENT_PROMPT_PATH))


class CrewRunnerToolsTestCase(unittest.TestCase):
    def setUp(self):
        self.config = _load_agent_config()

    def test_tools_is_shell_only(self):
        self.assertEqual(self.config.get("tools"), ["shell"])
        self.assertEqual(self.config.get("allowedTools"), ["shell"])

    def test_generic_fs_write_is_denied(self):
        rules = self.config["permissions"]["rules"]
        fs_write_rules = [r for r in rules if r.get("capability") == "fs_write"]
        self.assertTrue(fs_write_rules, "expected at least one fs_write rule")
        for rule in fs_write_rules:
            self.assertEqual(rule.get("effect"), "deny")
            self.assertIn("*", rule.get("match", []))


class CrewRunnerAllowListTestCase(unittest.TestCase):
    def setUp(self):
        self.config = _load_agent_config()
        self.rules = self.config["permissions"]["rules"]
        self.shell_rules = [r for r in self.rules if r.get("capability") == "shell"]

    def test_allowed_command_patterns_are_narrowly_scoped(self):
        allow_rules = [r for r in self.shell_rules if r.get("effect") == "allow"]
        allow_patterns = [pattern for rule in allow_rules for pattern in rule.get("match", [])]

        # Every allow pattern must be one of the exact ChangeGuard
        # transport script prefixes (or the one narrow baseline-existence
        # check) -- never a bare wildcard, never apply_remediation.py.
        for pattern in allow_patterns:
            self.assertNotEqual(pattern, "*", "an allow rule must never be a bare wildcard")
            self.assertNotIn("apply_remediation.py", pattern)
            is_known_transport_script = any(pattern.startswith(prefix) for prefix in ALLOWED_TRANSPORT_SCRIPTS)
            is_baseline_check = pattern.startswith("test -f artifacts/baseline-plan.json")
            self.assertTrue(
                is_known_transport_script or is_baseline_check,
                f"unexpected allow pattern outside the known ChangeGuard transport scripts: {pattern!r}",
            )

    def test_direct_apply_remediation_is_not_allowed(self):
        for rule in self.shell_rules:
            if "apply_remediation.py" in " ".join(rule.get("match", [])):
                self.assertEqual(
                    rule.get("effect"), "deny",
                    "any rule mentioning apply_remediation.py must deny it, never allow it",
                )
        # And there must be at least one explicit deny rule naming it.
        deny_patterns = [
            pattern
            for rule in self.shell_rules
            if rule.get("effect") == "deny"
            for pattern in rule.get("match", [])
        ]
        self.assertTrue(
            any("apply_remediation.py" in pattern for pattern in deny_patterns),
            "expected an explicit deny rule naming apply_remediation.py",
        )

    def test_terraform_aws_rm_are_not_allowed(self):
        allow_rules = [r for r in self.shell_rules if r.get("effect") == "allow"]
        allow_patterns = " ".join(
            pattern for rule in allow_rules for pattern in rule.get("match", [])
        )
        for forbidden in ("terraform apply", "terraform destroy", "aws ", "rm -rf", "rm -fr"):
            self.assertNotIn(forbidden, allow_patterns)

    def test_trailing_wildcard_deny_rule_exists(self):
        # A final catch-all shell deny must exist so any command not
        # explicitly allow-listed above is refused by default.
        deny_all = [
            r for r in self.shell_rules
            if r.get("effect") == "deny" and "*" in r.get("match", [])
        ]
        self.assertTrue(deny_all, "expected a trailing wildcard shell deny rule")


class CrewRunnerSafetyHookTestCase(unittest.TestCase):
    def test_pretooluse_safety_guard_is_attached(self):
        config = _load_agent_config()
        hooks = config.get("hooks", {}).get("preToolUse", [])
        self.assertTrue(hooks, "expected at least one preToolUse hook")
        commands = [hook.get("command", "") for hook in hooks]
        self.assertTrue(
            any("safety_guard.py" in command for command in commands),
            "expected the validated safety_guard.py hook to be attached",
        )


class YamlDagNoLongerClaimsLiteralShellExecutionTestCase(unittest.TestCase):
    """The DAG files must no longer misleadingly suggest that Crew itself
    executes shell commands deterministically -- confirmed false by the
    live semantics probe. They must use `prompt:` (not `shell:`) and each
    task's prompt text must instruct the run-scoped agent to execute
    exactly one named command."""

    def _read(self, path):
        with open(path) as f:
            return f.read()

    def test_stage_a_uses_prompt_key_not_shell_key(self):
        text = self._read(STAGE_A_YAML_PATH)
        self.assertNotIn("\n    shell:", text)
        self.assertIn("prompt:", text)

    def test_stage_b_uses_prompt_key_not_shell_key(self):
        text = self._read(STAGE_B_YAML_PATH)
        self.assertNotIn("\n    shell:", text)
        self.assertIn("prompt:", text)

    def test_every_task_prompt_names_exactly_one_approved_command_family(self):
        for path in (STAGE_A_YAML_PATH, STAGE_B_YAML_PATH):
            text = self._read(path)
            # Each node's prompt block runs until the next top-level node
            # key or end of file; split on the "Execute exactly this
            # command" marker to inspect each occurrence independently.
            segments = re.split(r"Execute exactly this (?:second )?command and no other command:", text)[1:]
            self.assertTrue(segments, f"expected at least one 'Execute exactly this command' instruction in {path}")
            for segment in segments:
                matched_families = [
                    family for family in ALLOWED_TRANSPORT_SCRIPTS
                    if family in segment
                ]
                baseline_check = "test -f artifacts/baseline-plan.json" in segment
                self.assertTrue(
                    len(matched_families) >= 1 or baseline_check,
                    f"prompt segment in {path} names no approved command family: {segment[:200]!r}",
                )

    def test_stage_a_uses_crew_runner_agent(self):
        text = self._read(STAGE_A_YAML_PATH)
        self.assertNotIn("agent: security-reviewer\n", text)
        self.assertNotIn("agent: reliability-reviewer\n", text)
        self.assertNotIn("agent: remediator\n", text)
        self.assertNotIn("agent: orchestrator\n", text)
        self.assertIn("agent: crew-runner", text)

    def test_stage_b_uses_crew_runner_agent(self):
        text = self._read(STAGE_B_YAML_PATH)
        self.assertNotIn("agent: security-reviewer\n", text)
        self.assertNotIn("agent: reliability-reviewer\n", text)
        self.assertNotIn("agent: remediator\n", text)
        self.assertNotIn("agent: orchestrator\n", text)
        self.assertIn("agent: crew-runner", text)


class PolicyBoundaryTestCase(unittest.TestCase):
    """crew-runner must contain no infrastructure policy logic at all."""

    FORBIDDEN_STRINGS = ("0.0.0.0/0", "desired_count", "deletion_protection")

    def test_no_policy_strings_in_agent_files(self):
        for path in (AGENT_CONFIG_PATH, AGENT_PROMPT_PATH):
            with open(path) as f:
                text = f.read()
            for forbidden in self.FORBIDDEN_STRINGS:
                self.assertNotIn(forbidden, text, f"found forbidden policy string {forbidden!r} in {path}")


if __name__ == "__main__":
    unittest.main()

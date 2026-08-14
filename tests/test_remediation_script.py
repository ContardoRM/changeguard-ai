#!/usr/bin/env python3
"""Integration tests for scripts/apply_remediation.py (Requirement 12.7, 12.9).

Runs the actual `apply_remediation.py` script as a subprocess against a
temporary copy of the repository's `terraform/main.tf` for each of the four
supported rule IDs, and verifies the unsupported-rule-ID rejection path.
Never reads from or writes to the repository's real `terraform/main.tf` —
every test operates exclusively on a fresh temporary copy.

Per design.md's Testing Strategy, this integration test module is guarded
with `unittest.skipUnless(shutil.which("terraform"), ...)` so the suite
degrades gracefully in an environment where the Terraform binary isn't
available.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REAL_MAIN_TF = os.path.join(REPO_ROOT, "terraform", "main.tf")
APPLY_REMEDIATION_SCRIPT = os.path.join(REPO_ROOT, "scripts", "apply_remediation.py")


@unittest.skipUnless(
    shutil.which("terraform"),
    "terraform binary not available; skipping remediation-script integration tests",
)
class RemediationScriptIntegrationTests(unittest.TestCase):
    """Runs apply_remediation.py as a real subprocess against a temporary
    copy of the repository's terraform/main.tf for each supported rule ID,
    and against the unsupported-rule-ID rejection path."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="test_remediation_script_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.main_tf_path = os.path.join(self.tmp_dir, "main.tf")
        with open(REAL_MAIN_TF) as f:
            self.pristine_content = f.read()
        shutil.copyfile(REAL_MAIN_TF, self.main_tf_path)

    def _read_main_tf(self):
        with open(self.main_tf_path) as f:
            return f.read()

    def _write_main_tf(self, content):
        with open(self.main_tf_path, "w") as f:
            f.write(content)

    def _replace_after_anchor(self, content, anchor, old, new):
        """Replace the first occurrence of `old` that appears after
        `anchor` in `content`. Used to target one of two otherwise
        identical `cidr_blocks = [...]` lines (port 22 vs. port 5432) by
        the unique description text preceding each ingress block."""
        anchor_index = content.index(anchor)
        old_index = content.index(old, anchor_index)
        return content[:old_index] + new + content[old_index + len(old):]

    def _run_apply_remediation(self, rule_id, resource, restore_value):
        result = subprocess.run(
            [
                sys.executable,
                APPLY_REMEDIATION_SCRIPT,
                "--terraform-dir",
                self.tmp_dir,
                "--rule-id",
                rule_id,
                "--resource",
                resource,
                "--restore-value",
                restore_value,
            ],
            capture_output=True,
            text=True,
        )
        return result

    # -----------------------------------------------------------------
    # Positive scenarios: one per supported rule ID.
    #
    # Each test starts from the pristine baseline content, mutates only
    # the one attribute/ingress CIDR the rule concerns to an "unsafe"
    # candidate value, runs the real script with a restore value equal to
    # the original pristine value, and asserts the resulting file is
    # byte-for-byte identical to the pristine baseline. Byte-for-byte
    # equality to the pristine original is only possible if exactly the
    # targeted value was corrected and nothing else in the file changed.
    # -----------------------------------------------------------------

    def test_sec001_restores_port_22_cidr_and_nothing_else_changes(self):
        unsafe_content = self._replace_after_anchor(
            self.pristine_content,
            "Internal SSH access",
            'cidr_blocks = ["10.0.0.0/8"]',
            'cidr_blocks = ["0.0.0.0/0"]',
        )
        self.assertNotEqual(unsafe_content, self.pristine_content)
        self._write_main_tf(unsafe_content)

        result = self._run_apply_remediation(
            "SEC-001", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._read_main_tf(), self.pristine_content)

    def test_sec002_restores_port_5432_cidr_and_nothing_else_changes(self):
        unsafe_content = self._replace_after_anchor(
            self.pristine_content,
            "Internal Postgres access",
            'cidr_blocks = ["10.0.0.0/8"]',
            'cidr_blocks = ["0.0.0.0/0"]',
        )
        self.assertNotEqual(unsafe_content, self.pristine_content)
        self._write_main_tf(unsafe_content)

        result = self._run_apply_remediation(
            "SEC-002", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._read_main_tf(), self.pristine_content)

    def test_rel001_restores_desired_count_and_nothing_else_changes(self):
        unsafe_content = self.pristine_content.replace(
            "desired_count       = 3", "desired_count       = 1"
        )
        self.assertNotEqual(unsafe_content, self.pristine_content)
        self._write_main_tf(unsafe_content)

        result = self._run_apply_remediation(
            "REL-001", "aws_ecs_service.payments_api", "3"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._read_main_tf(), self.pristine_content)

    def test_br001_restores_deletion_protection_and_nothing_else_changes(self):
        unsafe_content = self.pristine_content.replace(
            "deletion_protection = true", "deletion_protection = false"
        )
        self.assertNotEqual(unsafe_content, self.pristine_content)
        self._write_main_tf(unsafe_content)

        result = self._run_apply_remediation(
            "BR-001", "aws_db_instance.payments_db", "true"
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(self._read_main_tf(), self.pristine_content)

    # -----------------------------------------------------------------
    # Negative scenario: unsupported rule ID (Requirement 12.9).
    # -----------------------------------------------------------------

    def test_unsupported_rule_id_is_rejected_with_no_file_modification(self):
        before = self._read_main_tf()
        result = self._run_apply_remediation(
            "SEC-999", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._read_main_tf(), before)
        self.assertEqual(self._read_main_tf(), self.pristine_content)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for scripts/apply_remediation.py.

All tests operate on a temporary copy of terraform/main.tf and never modify
the repository baseline. Uses only the Python 3 standard library `unittest`
module, per Requirement 12.1.

Covers the mandatory positive scenarios (SEC-001, SEC-002, REL-001, BR-001)
and the mandatory negative scenarios (unsupported rule ID, wrong resource,
malformed restore value, missing target resource, missing target
attribute/block, no-op/already-remediated target, ambiguous targets) from
the ChangeGuard remediation-script test plan. Every negative scenario
asserts `main.tf` content is byte-for-byte identical before and after the
rejected call.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import apply_remediation  # noqa: E402


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REAL_MAIN_TF = os.path.join(REPO_ROOT, "terraform", "main.tf")


class RemediationTestCase(unittest.TestCase):
    """Base class that copies the real terraform/main.tf into a fresh
    temporary directory for each test, and never touches the repository
    baseline."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="apply_remediation_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.main_tf_path = os.path.join(self.tmp_dir, "main.tf")
        shutil.copyfile(REAL_MAIN_TF, self.main_tf_path)

    def _write_main_tf(self, content):
        with open(self.main_tf_path, "w") as f:
            f.write(content)

    def _read_main_tf(self):
        with open(self.main_tf_path) as f:
            return f.read()

    def _run(self, rule_id, resource, restore_value):
        return apply_remediation.main(
            [
                "--terraform-dir",
                self.tmp_dir,
                "--rule-id",
                rule_id,
                "--resource",
                resource,
                "--restore-value",
                restore_value,
            ]
        )

    def _assert_unchanged(self, before):
        self.assertEqual(
            self._read_main_tf(), before, "main.tf must be byte-for-byte unchanged"
        )


# ---------------------------------------------------------------------------
# Mandatory positive scenarios
# ---------------------------------------------------------------------------


class Sec001RemediationTests(RemediationTestCase):
    """SEC-001: starting from TCP/22 -> 0.0.0.0/0, TCP/5432 -> 10.0.0.0/8,
    remediation restores only TCP/22 and leaves TCP/5432 untouched."""

    def setUp(self):
        super().setUp()
        self._write_main_tf(
            """resource "aws_security_group" "payments_sg" {
  name        = "payments-sg"
  description = "ChangeGuard demo security group"

  ingress {
    description = "Internal SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Internal Postgres access"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  tags = {
    Name = "payments-sg"
  }
}

resource "aws_ecs_service" "payments_api" {
  name = "payments-api"

  cluster         = "arn:aws:ecs:us-east-1:000000000000:cluster/changeguard-demo"
  task_definition = "arn:aws:ecs:us-east-1:000000000000:task-definition/payments-api:1"

  desired_count       = 3
  scheduling_strategy = "REPLICA"
}

resource "aws_db_instance" "payments_db" {
  identifier = "payments-db"

  engine            = "postgres"
  engine_version    = "17"
  instance_class    = "db.t3.micro"
  allocated_storage = 20
  storage_type      = "gp2"

  db_name  = "payments"
  username = "changeguard"
  password = "changeguard-demo-password"

  deletion_protection = true
  skip_final_snapshot = true
}
"""
        )

    def test_restores_only_port_22(self):
        rc = self._run(
            "SEC-001", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertEqual(rc, 0)
        content = self._read_main_tf()

        # TCP/22 restored.
        self.assertIn(
            'from_port   = 22\n    to_port     = 22\n    protocol    = "tcp"\n'
            '    cidr_blocks = ["10.0.0.0/8"]',
            content,
        )
        # TCP/5432 block must be byte-for-byte unchanged.
        self.assertIn(
            'from_port   = 5432\n    to_port     = 5432\n    protocol    = "tcp"\n'
            '    cidr_blocks = ["10.0.0.0/8"]',
            content,
        )
        self.assertNotIn("0.0.0.0/0", content)


class Sec002RemediationTests(RemediationTestCase):
    """SEC-002: starting from TCP/22 -> 10.0.0.0/8, TCP/5432 -> 0.0.0.0/0,
    remediation restores only TCP/5432."""

    def setUp(self):
        super().setUp()
        self._write_main_tf(
            """resource "aws_security_group" "payments_sg" {
  name        = "payments-sg"
  description = "ChangeGuard demo security group"

  ingress {
    description = "Internal SSH access"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "Internal Postgres access"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "payments-sg"
  }
}

resource "aws_ecs_service" "payments_api" {
  name = "payments-api"

  desired_count       = 3
  scheduling_strategy = "REPLICA"
}

resource "aws_db_instance" "payments_db" {
  identifier = "payments-db"

  deletion_protection = true
  skip_final_snapshot = true
}
"""
        )

    def test_restores_only_port_5432(self):
        rc = self._run(
            "SEC-002", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertEqual(rc, 0)
        content = self._read_main_tf()

        self.assertIn(
            'from_port   = 5432\n    to_port     = 5432\n    protocol    = "tcp"\n'
            '    cidr_blocks = ["10.0.0.0/8"]',
            content,
        )
        self.assertIn(
            'from_port   = 22\n    to_port     = 22\n    protocol    = "tcp"\n'
            '    cidr_blocks = ["10.0.0.0/8"]',
            content,
        )
        self.assertNotIn("0.0.0.0/0", content)


class Rel001RemediationTests(RemediationTestCase):
    """REL-001: starting from desired_count = 1, remediation restores
    desired_count = 3 and leaves unrelated content unchanged."""

    def setUp(self):
        super().setUp()
        self.original_content = self._read_main_tf().replace(
            "desired_count       = 3", "desired_count       = 1"
        )
        self.assertIn("desired_count       = 1", self.original_content)
        self._write_main_tf(self.original_content)

    def test_restores_desired_count_and_leaves_rest_unchanged(self):
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")
        self.assertEqual(rc, 0)
        content = self._read_main_tf()

        self.assertIn("desired_count       = 3", content)
        self.assertNotIn("desired_count       = 1", content)

        expected_unchanged = self.original_content.replace(
            "desired_count       = 1", "desired_count       = 3"
        )
        self.assertEqual(content, expected_unchanged)


class Br001RemediationTests(RemediationTestCase):
    """BR-001: starting from deletion_protection = false, remediation
    restores deletion_protection = true and leaves unrelated content
    unchanged."""

    def setUp(self):
        super().setUp()
        self.original_content = self._read_main_tf().replace(
            "deletion_protection = true", "deletion_protection = false"
        )
        self.assertIn("deletion_protection = false", self.original_content)
        self._write_main_tf(self.original_content)

    def test_restores_deletion_protection_and_leaves_rest_unchanged(self):
        rc = self._run("BR-001", "aws_db_instance.payments_db", "true")
        self.assertEqual(rc, 0)
        content = self._read_main_tf()

        self.assertIn("deletion_protection = true", content)
        self.assertNotIn("deletion_protection = false", content)

        expected_unchanged = self.original_content.replace(
            "deletion_protection = false", "deletion_protection = true"
        )
        self.assertEqual(content, expected_unchanged)


class RealBaselineRoundTripTests(RemediationTestCase):
    """Sanity check against the actual repository baseline copy: since the
    baseline is already safe (no unsafe value present), every supported
    rule must be rejected as a no-op, and main.tf must stay unchanged."""

    def test_all_four_rules_are_no_ops_against_the_safe_baseline(self):
        before = self._read_main_tf()
        cases = [
            ("SEC-001", "aws_security_group.payments_sg", "10.0.0.0/8"),
            ("SEC-002", "aws_security_group.payments_sg", "10.0.0.0/8"),
            ("REL-001", "aws_ecs_service.payments_api", "3"),
            ("BR-001", "aws_db_instance.payments_db", "true"),
        ]
        for rule_id, resource, restore_value in cases:
            with self.subTest(rule_id=rule_id):
                rc = self._run(rule_id, resource, restore_value)
                self.assertNotEqual(rc, 0)
                self._assert_unchanged(before)


# ---------------------------------------------------------------------------
# Mandatory negative scenarios
# ---------------------------------------------------------------------------


class UnsupportedRuleIdTests(RemediationTestCase):
    def test_unsupported_rule_id_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("SEC-003", "aws_security_group.payments_sg", "10.0.0.0/8")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_completely_unknown_rule_id_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("NOT-A-RULE", "aws_ecs_service.payments_api", "3")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class WrongResourceForRuleTests(RemediationTestCase):
    def test_rel001_against_wrong_resource_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_db_instance.payments_db", "3")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_sec001_against_wrong_resource_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("SEC-001", "aws_ecs_service.payments_api", "10.0.0.0/8")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_br001_against_wrong_resource_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("BR-001", "aws_security_group.payments_sg", "true")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class MalformedRestoreValueTests(RemediationTestCase):
    def test_rel001_rejects_boolean_restore_value(self):
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "true")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_rel001_rejects_non_integer_string(self):
        before = self._read_main_tf()
        rc = self._run(
            "REL-001", "aws_ecs_service.payments_api", "three"
        )
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_rel001_rejects_negative_integer(self):
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "-1")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_br001_rejects_non_boolean_string(self):
        before = self._read_main_tf()
        rc = self._run("BR-001", "aws_db_instance.payments_db", "yes")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_br001_rejects_integer_for_boolean(self):
        before = self._read_main_tf()
        rc = self._run("BR-001", "aws_db_instance.payments_db", "1")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_sec001_rejects_non_cidr_string(self):
        before = self._read_main_tf()
        rc = self._run(
            "SEC-001", "aws_security_group.payments_sg", "not-a-cidr"
        )
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_sec001_rejects_non_strict_cidr(self):
        # 10.0.0.5/8 is not the strict network address for a /8 (host bits
        # set); ipaddress.IPv4Network(..., strict=True) rejects this.
        before = self._read_main_tf()
        rc = self._run(
            "SEC-001", "aws_security_group.payments_sg", "10.0.0.5/8"
        )
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class TargetResourceMissingTests(RemediationTestCase):
    def setUp(self):
        super().setUp()
        # Remove the ECS resource block entirely.
        content = self._read_main_tf()
        start = content.index('resource "aws_ecs_service"')
        end = content.index("\n}\n", start) + len("\n}\n")
        self._write_main_tf(content[:start] + content[end:])

    def test_missing_target_resource_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class TargetAttributeMissingTests(RemediationTestCase):
    def setUp(self):
        super().setUp()
        content = self._read_main_tf()
        # Remove the desired_count line from the ECS block.
        content = content.replace("\n  desired_count       = 3", "")
        self._write_main_tf(content)

    def test_missing_target_attribute_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class TargetIngressBlockMissingTests(RemediationTestCase):
    def setUp(self):
        super().setUp()
        content = self._read_main_tf()
        # Remove the port-5432 ingress block entirely.
        start = content.index('    description = "Internal Postgres access"')
        # Back up to the start of the "ingress {" line.
        start = content.rindex("ingress {", 0, start)
        end = content.index("\n  }\n", start) + len("\n  }\n")
        self._write_main_tf(content[:start] + content[end:])

    def test_missing_target_ingress_block_is_rejected_with_no_write(self):
        before = self._read_main_tf()
        rc = self._run(
            "SEC-002", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class AlreadyRemediatedNoOpTests(RemediationTestCase):
    """The target already equals the requested restore value: there is no
    unsafe/current state to correct, so the script must refuse."""

    def test_rel001_no_op_when_already_at_restore_value(self):
        before = self._read_main_tf()
        self.assertIn("desired_count       = 3", before)
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_br001_no_op_when_already_at_restore_value(self):
        before = self._read_main_tf()
        self.assertIn("deletion_protection = true", before)
        rc = self._run("BR-001", "aws_db_instance.payments_db", "true")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_sec001_no_op_when_already_at_restore_value(self):
        before = self._read_main_tf()
        rc = self._run(
            "SEC-001", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class AmbiguousTargetTests(RemediationTestCase):
    """Multiple candidate targets instead of exactly one expected target
    must be rejected, not resolved by guessing."""

    def test_duplicate_resource_block_is_rejected_with_no_write(self):
        content = self._read_main_tf()
        # Duplicate the ECS resource block so two blocks match the same
        # resource type+name.
        start = content.index('resource "aws_ecs_service"')
        end = content.index("\n}\n", start) + len("\n}\n")
        duplicate = content[start:end]
        content_with_dup = content[:end] + "\n" + duplicate + content[end:]
        self._write_main_tf(content_with_dup)

        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_duplicate_ingress_block_for_same_port_is_rejected_with_no_write(self):
        content = self._read_main_tf()
        # Duplicate the port-22 ingress block inside the security group
        # resource so two ingress sub-blocks match port 22.
        start = content.rindex(
            "ingress {", 0, content.index('from_port   = 22')
        )
        end = content.index("\n  }\n", start) + len("\n  }\n")
        duplicate = content[start:end]
        content_with_dup = content[:end] + duplicate + content[end:]
        self._write_main_tf(content_with_dup)

        before = self._read_main_tf()
        rc = self._run(
            "SEC-001", "aws_security_group.payments_sg", "10.0.0.0/8"
        )
        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)


class OutputContractTests(RemediationTestCase):
    """The success-path stdout message has the documented JSON shape and
    no reasoning prose is emitted."""

    def test_success_stdout_json_shape(self):
        content = self._read_main_tf().replace(
            "desired_count       = 3", "desired_count       = 1"
        )
        self._write_main_tf(content)

        import io
        import json
        from contextlib import redirect_stdout

        captured = io.StringIO()
        with redirect_stdout(captured):
            rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")

        self.assertEqual(rc, 0)
        printed = captured.getvalue().strip()
        message = json.loads(printed)
        self.assertEqual(
            message,
            {
                "status": "remediated",
                "rule_id": "REL-001",
                "resource": "aws_ecs_service.payments_api",
                "restored_value": 3,
            },
        )


class CliArgumentAcceptanceTests(unittest.TestCase):
    """The CLI accepts exactly --terraform-dir, --rule-id, --resource, and
    --restore-value; it does not accept an arbitrary extra path argument."""

    def test_unknown_argument_is_rejected(self):
        with self.assertRaises(SystemExit):
            apply_remediation.parse_args(
                [
                    "--terraform-dir",
                    "terraform",
                    "--rule-id",
                    "REL-001",
                    "--resource",
                    "aws_ecs_service.payments_api",
                    "--restore-value",
                    "3",
                    "--extra-file",
                    "/etc/passwd",
                ]
            )


if __name__ == "__main__":
    unittest.main()

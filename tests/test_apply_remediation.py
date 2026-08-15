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

import json
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

    def _run(self, rule_id, resource, restore_value, result_file=None):
        argv = [
            "--terraform-dir",
            self.tmp_dir,
            "--rule-id",
            rule_id,
            "--resource",
            resource,
            "--restore-value",
            restore_value,
        ]
        if result_file is not None:
            argv += ["--result-file", result_file]
        return apply_remediation.main(argv)

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


class ResultFileTests(RemediationTestCase):
    """Phase 8B transport correction: --result-file is written atomically
    ONLY on a fully successful, validated mutation, and any stale
    pre-existing file at that path is cleared before the attempt so it can
    never be mistaken for this invocation's outcome.

    Uses a path conforming to the Phase 8C path-confinement contract
    (strictly inside the artifacts/ directory sibling to --terraform-dir,
    named with the required '.remediation-execution-' prefix) so these
    transport-behavior tests remain valid alongside the newer
    ResultFilePathConfinementTests, which cover the confinement contract
    itself."""

    def setUp(self):
        super().setUp()
        self.artifacts_dir = os.path.join(os.path.dirname(self.tmp_dir), "artifacts")
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.artifacts_dir, ignore_errors=True)

    def _result_file_path(self):
        return os.path.join(self.artifacts_dir, ".remediation-execution-test.json")

    def test_successful_remediation_writes_exact_result_file(self):
        content = self._read_main_tf().replace(
            "desired_count       = 3", "desired_count       = 1"
        )
        self._write_main_tf(content)
        result_path = self._result_file_path()

        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=result_path)

        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(result_path))
        with open(result_path) as f:
            payload = json.load(f)
        self.assertEqual(
            payload,
            {
                "status": "remediated",
                "rule_id": "REL-001",
                "resource": "aws_ecs_service.payments_api",
                "restored_value": 3,
            },
        )

    def test_stale_preexisting_result_file_is_cleared_before_a_failing_attempt(self):
        # Pre-seed a stale "success" artifact from an unrelated prior
        # invocation, then run a call that must fail (unsupported rule
        # ID). The stale file must not survive to be mistaken for this
        # invocation's outcome.
        result_path = self._result_file_path()
        with open(result_path, "w") as f:
            json.dump(
                {
                    "status": "remediated",
                    "rule_id": "STALE-999",
                    "resource": "stale.resource",
                    "restored_value": "stale",
                },
                f,
            )

        before = self._read_main_tf()
        rc = self._run("NOT-A-RULE", "aws_ecs_service.payments_api", "3", result_file=result_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)
        self.assertFalse(
            os.path.isfile(result_path),
            "a stale pre-existing result file must be cleared, never left in place, on a failing attempt",
        )

    def test_failed_remediation_does_not_write_result_file(self):
        # Unsupported rule ID -- main.tf untouched, and no result file
        # should ever be written for a call that never reaches the
        # mutation step.
        result_path = self._result_file_path()
        before = self._read_main_tf()

        rc = self._run("NOT-A-RULE", "aws_ecs_service.payments_api", "3", result_file=result_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)
        self.assertFalse(os.path.isfile(result_path))

    def test_no_op_remediation_target_already_correct_does_not_write_result_file(self):
        # desired_count is already 3 (the baseline fixture's own value) --
        # the no-op guard should refuse before any write, including the
        # result file.
        result_path = self._result_file_path()
        before = self._read_main_tf()

        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=result_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)
        self.assertFalse(os.path.isfile(result_path))

    def test_omitting_result_file_preserves_stdout_only_behavior(self):
        content = self._read_main_tf().replace(
            "desired_count       = 3", "desired_count       = 1"
        )
        self._write_main_tf(content)

        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3")

        self.assertEqual(rc, 0)


class ResultFilePathConfinementTests(RemediationTestCase):
    """Phase 8C hardening: --result-file must resolve strictly inside the
    artifacts/ directory associated with --terraform-dir, and its
    filename must match the fixed '.remediation-execution-<id>.json'
    pattern. Any violation must be rejected fail-closed, BEFORE any
    delete or write is attempted -- including never deleting a
    pre-existing file outside the allowed directory."""

    def setUp(self):
        super().setUp()
        # self.tmp_dir is the fake --terraform-dir. The allowed
        # artifacts/ directory is its sibling.
        self.artifacts_dir = os.path.join(os.path.dirname(self.tmp_dir), "artifacts")
        os.makedirs(self.artifacts_dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.artifacts_dir, ignore_errors=True)

        # A remediable target so a validation-only rejection can be
        # distinguished from an unrelated remediation failure.
        content = self._read_main_tf().replace(
            "desired_count       = 3", "desired_count       = 1"
        )
        self._write_main_tf(content)

    def _valid_result_path(self):
        return os.path.join(self.artifacts_dir, ".remediation-execution-abc123.json")

    def test_valid_internal_path_is_accepted(self):
        result_path = self._valid_result_path()
        before = self._read_main_tf()
        self.assertNotEqual(before, "")

        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=result_path)

        self.assertEqual(rc, 0)
        self.assertTrue(os.path.isfile(result_path))
        with open(result_path) as f:
            payload = json.load(f)
        self.assertEqual(payload["status"], "remediated")

    def test_absolute_path_outside_workspace_is_rejected_with_no_write(self):
        outside_dir = tempfile.mkdtemp(prefix="outside_workspace_")
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        outside_path = os.path.join(outside_dir, ".remediation-execution-evil.json")

        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=outside_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)
        self.assertFalse(os.path.isfile(outside_path))

    def test_traversal_path_escaping_artifacts_dir_is_rejected_with_no_write(self):
        traversal_path = os.path.join(
            self.artifacts_dir, "..", "..", "etc", ".remediation-execution-evil.json"
        )
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=traversal_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_arbitrary_filename_inside_artifacts_dir_is_rejected_with_no_write(self):
        # Correct directory, wrong filename pattern (missing the required
        # '.remediation-execution-' prefix) -- e.g. an attempt to target
        # the durable public artifact by name.
        wrong_name_path = os.path.join(self.artifacts_dir, "remediation-result.json")
        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=wrong_name_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)

    def test_rejected_validation_does_not_delete_a_preexisting_external_file(self):
        # A decoy file sits outside the allowed artifacts/ directory. A
        # rejected --result-file pointing at it must never be deleted or
        # overwritten, even though this call also fails main.tf
        # remediation validation.
        outside_dir = tempfile.mkdtemp(prefix="decoy_dir_")
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        decoy_path = os.path.join(outside_dir, "important-file.json")
        with open(decoy_path, "w") as f:
            json.dump({"do": "not touch me"}, f)

        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=decoy_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)
        self.assertTrue(os.path.isfile(decoy_path))
        with open(decoy_path) as f:
            self.assertEqual(json.load(f), {"do": "not touch me"})

    def test_symlink_escaping_artifacts_dir_is_rejected_with_no_write(self):
        outside_dir = tempfile.mkdtemp(prefix="symlink_target_dir_")
        self.addCleanup(shutil.rmtree, outside_dir, ignore_errors=True)
        real_outside_target = os.path.join(outside_dir, "real-target.json")

        symlink_path = os.path.join(self.artifacts_dir, ".remediation-execution-link.json")
        try:
            os.symlink(real_outside_target, symlink_path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform/filesystem")
        self.addCleanup(lambda: os.path.exists(symlink_path) and os.remove(symlink_path))

        before = self._read_main_tf()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=symlink_path)

        self.assertNotEqual(rc, 0)
        self._assert_unchanged(before)
        self.assertFalse(os.path.exists(real_outside_target))

    def test_normal_valid_remediation_with_new_path_convention_still_succeeds(self):
        result_path = self._valid_result_path()
        rc = self._run("REL-001", "aws_ecs_service.payments_api", "3", result_file=result_path)
        self.assertEqual(rc, 0)
        content = self._read_main_tf()
        self.assertIn("desired_count       = 3", content)
        with open(result_path) as f:
            payload = json.load(f)
        self.assertEqual(
            payload,
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

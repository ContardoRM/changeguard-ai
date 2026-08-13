#!/usr/bin/env python3
"""Unit tests for scripts/reliability_rules.py's evidence-extraction functions.

These tests verify only deterministic, structural evidence extraction:
what values are present in the baseline and candidate Terraform plan JSON
for `aws_ecs_service.payments_api`'s `desired_count` and
`aws_db_instance.payments_db`'s `deletion_protection`. No reliability or
blast-radius policy rule ID is referenced or asserted anywhere in this
file, and no test here checks whether any extracted value is acceptable
- only whether it was extracted correctly, and whether its
absence/malformation is correctly reported.

Uses only the Python 3 standard library `unittest` module.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import reliability_rules  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    return os.path.join(FIXTURES_DIR, name)


BASELINE = _fixture("baseline_plan.json")
CANDIDATE_SAFE = _fixture("candidate_safe.json")
CANDIDATE_REL001 = _fixture("candidate_rel001.json")
CANDIDATE_BR001 = _fixture("candidate_br001.json")
CANDIDATE_MISSING_RESOURCE = _fixture("candidate_missing_resource_reliability.json")
CANDIDATE_MISSING_FIELD = _fixture("candidate_missing_field_reliability.json")
CANDIDATE_MALFORMED = _fixture("candidate_malformed_reliability.json")
CANDIDATE_MALFORMED_INT_FOR_BOOL = _fixture(
    "candidate_malformed_reliability_int_for_bool.json"
)
INVALID_JSON = _fixture("invalid_json.json")


class EcsDesiredCountExtractionTests(unittest.TestCase):
    """ECS baseline/candidate desired_count is extracted correctly."""

    def test_baseline_desired_count_extracted(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_SAFE
        )
        result = evidence.baseline
        self.assertEqual(result.status, reliability_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value, 3)
        self.assertIsInstance(result.value, int)

    def test_candidate_desired_count_extracted(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_REL001
        )
        result = evidence.candidate
        self.assertEqual(result.status, reliability_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value, 1)
        self.assertIsInstance(result.value, int)


class RdsDeletionProtectionExtractionTests(unittest.TestCase):
    """RDS baseline/candidate deletion_protection is extracted correctly."""

    def test_baseline_deletion_protection_extracted(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_SAFE
        )
        result = evidence.baseline
        self.assertEqual(result.status, reliability_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value, True)
        self.assertIsInstance(result.value, bool)

    def test_candidate_deletion_protection_extracted(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_BR001
        )
        result = evidence.candidate
        self.assertEqual(result.status, reliability_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value, False)
        self.assertIsInstance(result.value, bool)


class StrictTypeValidationTests(unittest.TestCase):
    """int vs bool types are validated strictly for each field."""

    def test_bool_rejected_for_desired_count(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        result = evidence.candidate
        self.assertEqual(result.status, reliability_rules.EvidenceStatus.MALFORMED)
        self.assertIsNone(result.value)

    def test_int_rejected_for_deletion_protection(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MALFORMED_INT_FOR_BOOL
        )
        result = evidence.candidate
        self.assertEqual(result.status, reliability_rules.EvidenceStatus.MALFORMED)
        self.assertIsNone(result.value)


class MissingResourceTests(unittest.TestCase):
    """A missing ECS or RDS resource produces an evidence-unavailable result."""

    def test_missing_ecs_resource_is_reported(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MISSING_RESOURCE
        )
        result = evidence.candidate
        self.assertEqual(
            result.status, reliability_rules.EvidenceStatus.MISSING_RESOURCE
        )
        self.assertIsNone(result.value)

    def test_missing_rds_resource_is_reported(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MISSING_RESOURCE
        )
        result = evidence.candidate
        self.assertEqual(
            result.status, reliability_rules.EvidenceStatus.MISSING_RESOURCE
        )
        self.assertIsNone(result.value)


class MissingFieldTests(unittest.TestCase):
    """Missing required fields produce an evidence-unavailable result."""

    def test_missing_desired_count_field_is_reported(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MISSING_FIELD
        )
        result = evidence.candidate
        self.assertEqual(
            result.status, reliability_rules.EvidenceStatus.MISSING_FIELD
        )
        self.assertIsNone(result.value)

    def test_missing_deletion_protection_field_is_reported(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MISSING_FIELD
        )
        result = evidence.candidate
        self.assertEqual(
            result.status, reliability_rules.EvidenceStatus.MISSING_FIELD
        )
        self.assertIsNone(result.value)


class WrongTypedValueTests(unittest.TestCase):
    """Wrong-typed values produce a malformed-evidence result."""

    def test_wrong_typed_desired_count_is_malformed(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MALFORMED
        )

    def test_wrong_typed_deletion_protection_is_malformed(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MALFORMED
        )

    def test_invalid_json_is_reported_as_malformed(self):
        evidence = reliability_rules.extract_reliability_evidence(
            BASELINE, INVALID_JSON
        )
        self.assertEqual(
            evidence.ecs_service.candidate.status,
            reliability_rules.EvidenceStatus.MALFORMED,
        )
        self.assertEqual(
            evidence.rds_instance.candidate.status,
            reliability_rules.EvidenceStatus.MALFORMED,
        )


if __name__ == "__main__":
    unittest.main()

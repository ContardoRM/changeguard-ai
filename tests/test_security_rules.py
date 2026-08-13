#!/usr/bin/env python3
"""Unit tests for scripts/security_rules.py's evidence-extraction functions.

These tests verify only deterministic, structural evidence extraction:
what values are present in the baseline and candidate Terraform plan JSON
for `aws_security_group.payments_sg`'s TCP/22 and TCP/5432 ingress
entries. No security policy rule ID is referenced or asserted anywhere in
this file, and no test here checks whether any extracted value is
acceptable — only whether it was extracted correctly, and whether its
absence/malformation is correctly reported.

Uses only the Python 3 standard library `unittest` module.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import security_rules  # noqa: E402

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name):
    return os.path.join(FIXTURES_DIR, name)


BASELINE = _fixture("baseline_plan.json")
CANDIDATE_SAFE = _fixture("candidate_safe.json")
CANDIDATE_SEC001 = _fixture("candidate_sec001.json")
CANDIDATE_SEC002 = _fixture("candidate_sec002.json")
CANDIDATE_MISSING_RESOURCE = _fixture("candidate_missing_resource_security.json")
CANDIDATE_MISSING_FIELD = _fixture("candidate_missing_field_security.json")
CANDIDATE_MALFORMED = _fixture("candidate_malformed_security.json")
INVALID_JSON = _fixture("invalid_json.json")


class BaselineIngressExtractionTests(unittest.TestCase):
    """Baseline evidence is extracted correctly for both tracked ports."""

    def test_baseline_port_22_extracted(self):
        evidence = security_rules.extract_ingress_evidence(
            BASELINE, CANDIDATE_SAFE, 22
        )
        result = evidence.baseline
        self.assertEqual(result.status, security_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value.protocol, "tcp")
        self.assertEqual(result.value.from_port, 22)
        self.assertEqual(result.value.to_port, 22)
        self.assertEqual(result.value.cidr_blocks, ["10.0.0.0/8"])

    def test_baseline_port_5432_extracted(self):
        evidence = security_rules.extract_ingress_evidence(
            BASELINE, CANDIDATE_SAFE, 5432
        )
        result = evidence.baseline
        self.assertEqual(result.status, security_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value.protocol, "tcp")
        self.assertEqual(result.value.from_port, 5432)
        self.assertEqual(result.value.to_port, 5432)
        self.assertEqual(result.value.cidr_blocks, ["10.0.0.0/8"])


class CandidateIngressExtractionTests(unittest.TestCase):
    """Candidate evidence is extracted correctly for both tracked ports."""

    def test_candidate_port_22_extracted(self):
        evidence = security_rules.extract_ingress_evidence(
            BASELINE, CANDIDATE_SEC001, 22
        )
        result = evidence.candidate
        self.assertEqual(result.status, security_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value.cidr_blocks, ["0.0.0.0/0"])

    def test_candidate_port_5432_extracted(self):
        evidence = security_rules.extract_ingress_evidence(
            BASELINE, CANDIDATE_SEC002, 5432
        )
        result = evidence.candidate
        self.assertEqual(result.status, security_rules.EvidenceStatus.AVAILABLE)
        self.assertEqual(result.value.cidr_blocks, ["0.0.0.0/0"])


class CidrBlocksRemainListsTests(unittest.TestCase):
    """cidr_blocks is always a list, never flattened or interpreted."""

    def test_cidr_blocks_is_a_list_not_a_string(self):
        evidence = security_rules.extract_security_group_evidence(
            BASELINE, CANDIDATE_SAFE
        )
        for port_evidence in (evidence.baseline["22"], evidence.candidate["22"]):
            self.assertIsInstance(port_evidence.value.cidr_blocks, list)
        for port_evidence in (evidence.baseline["5432"], evidence.candidate["5432"]):
            self.assertIsInstance(port_evidence.value.cidr_blocks, list)


class TypedFieldValidationTests(unittest.TestCase):
    """protocol/from_port/to_port are validated with the expected types."""

    def test_field_types_are_correct(self):
        evidence = security_rules.extract_ingress_evidence(
            BASELINE, CANDIDATE_SAFE, 22
        )
        value = evidence.baseline.value
        self.assertIsInstance(value.protocol, str)
        self.assertIsInstance(value.from_port, int)
        self.assertIsInstance(value.to_port, int)
        self.assertNotIsInstance(value.from_port, bool)
        self.assertNotIsInstance(value.to_port, bool)


class MissingResourceTests(unittest.TestCase):
    """A missing security group resource is reported, not fabricated."""

    def test_missing_resource_is_reported(self):
        evidence = security_rules.extract_security_group_evidence(
            BASELINE, CANDIDATE_MISSING_RESOURCE
        )
        result = evidence.candidate["22"]
        self.assertEqual(
            result.status, security_rules.EvidenceStatus.MISSING_RESOURCE
        )
        self.assertIsNone(result.value)


class MissingFieldTests(unittest.TestCase):
    """Missing ingress evidence (no entry covering the port) is reported."""

    def test_missing_ingress_entry_is_reported(self):
        evidence = security_rules.extract_security_group_evidence(
            BASELINE, CANDIDATE_MISSING_FIELD
        )
        result = evidence.candidate["22"]
        self.assertEqual(result.status, security_rules.EvidenceStatus.MISSING_FIELD)
        self.assertIsNone(result.value)

    def test_present_port_alongside_missing_port_is_unaffected(self):
        # Port 5432 is still present in the same fixture; each port's
        # extraction must be independent of the other.
        evidence = security_rules.extract_security_group_evidence(
            BASELINE, CANDIDATE_MISSING_FIELD
        )
        result = evidence.candidate["5432"]
        self.assertEqual(result.status, security_rules.EvidenceStatus.AVAILABLE)


class MalformedEvidenceTests(unittest.TestCase):
    """Malformed ingress values produce a malformed-evidence result."""

    def test_malformed_cidr_blocks_is_reported(self):
        evidence = security_rules.extract_security_group_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        result = evidence.candidate["22"]
        self.assertEqual(result.status, security_rules.EvidenceStatus.MALFORMED)
        self.assertIsNone(result.value)

    def test_invalid_json_is_reported_as_malformed(self):
        evidence = security_rules.extract_security_group_evidence(
            BASELINE, INVALID_JSON
        )
        result = evidence.candidate["22"]
        self.assertEqual(result.status, security_rules.EvidenceStatus.MALFORMED)
        self.assertIsNone(result.value)


if __name__ == "__main__":
    unittest.main()

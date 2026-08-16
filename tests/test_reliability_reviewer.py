#!/usr/bin/env python3
"""Tests for the Reliability Reviewer (evidence extraction + agent judgment).

This module has two layers, matching the actual repository architecture
(read `.kiro/agents/reliability-reviewer.json`,
`.kiro/agents/reliability-reviewer-prompt.md`, `scripts/reliability_rules.py`,
and `scripts/print_reliability_evidence.py` before changing anything here):

1. **Evidence-extraction layer** (deterministic, plain Python, no LLM):
   `scripts/reliability_rules.py`'s `extract_ecs_desired_count_evidence`,
   `extract_rds_deletion_protection_evidence`, and
   `extract_reliability_evidence` are directly-importable pure functions.
   They are unit-tested here directly, exhaustively, and deterministically
   via stdlib `unittest` against the fixtures under `tests/fixtures/`. These
   functions never return a verdict (`PASS`/`FAIL`/`INCOMPLETE`) and never
   return a `Finding` — only a plain evidence record or one of the
   structural `EvidenceStatus` outcomes (`MISSING_RESOURCE`, `MISSING_FIELD`,
   `MALFORMED`). That is asserted throughout this layer's tests.

2. **Judgment layer** (`PASS`/`FAIL`/`INCOMPLETE` verdicts and `Finding`
   records): per Task 8.2's completed implementation note, the
   REL-001/BR-001 rule-satisfaction judgment for the Reliability Reviewer
   was implemented as `.kiro/agents/reliability-reviewer.json` +
   `.kiro/agents/reliability-reviewer-prompt.md` — i.e. the judgment lives
   entirely in the agent's LLM system prompt, not in any plain,
   directly-importable Python function. (This differs from what an earlier
   planning note in tasks.md speculated the shape would be; the actual
   completed Task 8.2 note is authoritative and confirms no such Python
   judgment function exists to import.) There is therefore no
   `judge_reliability(...)`-style function this test module can call
   in-process for verdict-level assertions.

   Consistent with how the sibling test module for the Security Reviewer
   (Task 4.3, `tests/test_security_reviewer.py`) handles the identical
   situation for SEC-001/SEC-002, this module's verdict-level tests invoke
   the *live* Reliability Reviewer agent as a subprocess:

       kiro-cli chat --agent reliability-reviewer --no-interactive "<prompt>"

   and parse the single trailing JSON object the agent's prompt contract
   requires it to emit (see "Output contract" in
   `reliability-reviewer-prompt.md`). Because this depends on a `kiro-cli`
   binary being installed and configured (network/model access, agent
   definitions on `PATH`-relative working directory, etc.) which will not
   be true in every environment this suite runs in, every live-agent test
   is guarded with `unittest.skipUnless(_KIRO_CLI_AVAILABLE, ...)`, checked
   once at import time via `shutil.which("kiro-cli")`. When `kiro-cli` is
   not on `PATH`, these tests are skipped rather than failed, so the rest of
   the suite (including the deterministic evidence-extraction tests in
   layer 1 above, which never require a live agent) remains fully runnable
   in any environment. This keeps Requirement 12.10's mandatory
   judgment-behavior coverage real and automatable without inventing a
   Python function the actual implementation does not have.

Uses only the Python 3 standard library (`unittest`, `subprocess`, `json`,
`re`), per Requirement 12.1.
"""

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import reliability_rules  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
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

ALL_FIXTURE_PAIRS = [
    (BASELINE, CANDIDATE_SAFE),
    (BASELINE, CANDIDATE_REL001),
    (BASELINE, CANDIDATE_BR001),
    (BASELINE, CANDIDATE_MISSING_RESOURCE),
    (BASELINE, CANDIDATE_MISSING_FIELD),
    (BASELINE, CANDIDATE_MALFORMED),
    (BASELINE, CANDIDATE_MALFORMED_INT_FOR_BOOL),
]

VERDICT_STATUSES = {"PASS", "FAIL", "INCOMPLETE"}
ALLOWED_RULE_IDS = {"REL-001", "BR-001"}


# ---------------------------------------------------------------------------
# Layer 1: evidence-extraction unit tests (deterministic, no LLM/agent)
# ---------------------------------------------------------------------------


class Rel001EvidenceExtractionTests(unittest.TestCase):
    """`extract_ecs_desired_count_evidence` (Task 5.1) returns a plain
    evidence record or an evidence-unavailable/malformed signal only,
    never a verdict."""

    def test_well_formed_pair_returns_available_evidence_record(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_REL001
        )
        self.assertEqual(evidence.resource, "aws_ecs_service.payments_api")
        self.assertEqual(
            evidence.baseline.status, reliability_rules.EvidenceStatus.AVAILABLE
        )
        self.assertEqual(evidence.baseline.value, 3)
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.AVAILABLE
        )
        self.assertEqual(evidence.candidate.value, 1)

    def test_missing_resource_fixture_returns_missing_resource_signal(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MISSING_RESOURCE
        )
        self.assertEqual(
            evidence.candidate.status,
            reliability_rules.EvidenceStatus.MISSING_RESOURCE,
        )
        self.assertIsNone(evidence.candidate.value)

    def test_missing_field_fixture_returns_missing_field_signal(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MISSING_FIELD
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MISSING_FIELD
        )
        self.assertIsNone(evidence.candidate.value)

    def test_malformed_fixture_returns_malformed_signal(self):
        evidence = reliability_rules.extract_ecs_desired_count_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MALFORMED
        )
        self.assertIsNone(evidence.candidate.value)

    def test_extraction_never_returns_a_verdict_or_finding(self):
        for baseline_path, candidate_path in ALL_FIXTURE_PAIRS:
            with self.subTest(candidate=os.path.basename(candidate_path)):
                evidence = reliability_rules.extract_ecs_desired_count_evidence(
                    baseline_path, candidate_path
                )
                for result in (evidence.baseline, evidence.candidate):
                    self.assertFalse(hasattr(result, "rule_id"))
                    self.assertNotIn(
                        result.status.value, {"PASS", "FAIL", "INCOMPLETE"}
                    )


class Br001EvidenceExtractionTests(unittest.TestCase):
    """`extract_rds_deletion_protection_evidence` (Task 5.2) returns a plain
    evidence record or an evidence-unavailable/malformed signal only,
    never a verdict."""

    def test_well_formed_pair_returns_available_evidence_record(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_BR001
        )
        self.assertEqual(evidence.resource, "aws_db_instance.payments_db")
        self.assertEqual(
            evidence.baseline.status, reliability_rules.EvidenceStatus.AVAILABLE
        )
        self.assertIs(evidence.baseline.value, True)
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.AVAILABLE
        )
        self.assertIs(evidence.candidate.value, False)

    def test_missing_resource_fixture_returns_missing_resource_signal(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MISSING_RESOURCE
        )
        self.assertEqual(
            evidence.candidate.status,
            reliability_rules.EvidenceStatus.MISSING_RESOURCE,
        )
        self.assertIsNone(evidence.candidate.value)

    def test_missing_field_fixture_returns_missing_field_signal(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MISSING_FIELD
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MISSING_FIELD
        )
        self.assertIsNone(evidence.candidate.value)

    def test_malformed_fixture_returns_malformed_signal(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MALFORMED
        )
        self.assertIsNone(evidence.candidate.value)

    def test_int_for_bool_fixture_returns_malformed_signal(self):
        evidence = reliability_rules.extract_rds_deletion_protection_evidence(
            BASELINE, CANDIDATE_MALFORMED_INT_FOR_BOOL
        )
        self.assertEqual(
            evidence.candidate.status, reliability_rules.EvidenceStatus.MALFORMED
        )
        self.assertIsNone(evidence.candidate.value)

    def test_extraction_never_returns_a_verdict_or_finding(self):
        for baseline_path, candidate_path in ALL_FIXTURE_PAIRS:
            with self.subTest(candidate=os.path.basename(candidate_path)):
                evidence = reliability_rules.extract_rds_deletion_protection_evidence(
                    baseline_path, candidate_path
                )
                for result in (evidence.baseline, evidence.candidate):
                    self.assertFalse(hasattr(result, "rule_id"))
                    self.assertNotIn(
                        result.status.value, {"PASS", "FAIL", "INCOMPLETE"}
                    )


class CombinedEvidenceExtractionTests(unittest.TestCase):
    """`extract_reliability_evidence` (used by
    `scripts/print_reliability_evidence.py`) combines both fields and still
    never returns a verdict."""

    def test_well_formed_safe_pair_both_available(self):
        evidence = reliability_rules.extract_reliability_evidence(
            BASELINE, CANDIDATE_SAFE
        )
        self.assertEqual(
            evidence.ecs_service.candidate.status,
            reliability_rules.EvidenceStatus.AVAILABLE,
        )
        self.assertEqual(
            evidence.rds_instance.candidate.status,
            reliability_rules.EvidenceStatus.AVAILABLE,
        )

    def test_malformed_pair_both_malformed(self):
        evidence = reliability_rules.extract_reliability_evidence(
            BASELINE, CANDIDATE_MALFORMED
        )
        self.assertEqual(
            evidence.ecs_service.candidate.status,
            reliability_rules.EvidenceStatus.MALFORMED,
        )
        self.assertEqual(
            evidence.rds_instance.candidate.status,
            reliability_rules.EvidenceStatus.MALFORMED,
        )


# ---------------------------------------------------------------------------
# Layer 2: live-agent judgment (verdict-level) tests
# ---------------------------------------------------------------------------

# `CHANGEGUARD_SKIP_LIVE_TESTS=1` (set by `make test`, the fast/deterministic
# default) forces the live-agent judgment tests below to skip even when
# `kiro-cli` is on PATH, so the fast suite never makes a real,
# credit-consuming LLM call. `make test-live` runs without that env var set.
_KIRO_CLI_PATH = None if os.environ.get("CHANGEGUARD_SKIP_LIVE_TESTS") else shutil.which("kiro-cli")
_KIRO_CLI_AVAILABLE = _KIRO_CLI_PATH is not None

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

_AGENT_TIMEOUT_SECONDS = 90


@functools.lru_cache(maxsize=None)
def _invoke_reliability_reviewer(baseline_path, candidate_path):
    """Invoke the live `reliability-reviewer` Kiro Crew agent as a
    subprocess and parse its final JSON verdict.

    Returns the parsed verdict dict. Raises `AssertionError` (via
    `unittest`'s test failure path, since this is only ever called from
    inside a test method) if the agent produced no parseable trailing JSON
    object, since the agent's own prompt contract (see "Output contract" in
    `reliability-reviewer-prompt.md`) requires its final chat message to be
    exactly one JSON object and nothing else.

    Cached (per unique `(baseline_path, candidate_path)` pair, for the
    lifetime of the test process) via `functools.lru_cache` purely to avoid
    redundant live subprocess invocations when multiple test methods in
    this module exercise the same fixture pair - the underlying agent
    invocation itself is always genuine and is never stubbed or faked.
    """
    prompt = (
        f"Baseline plan path: {baseline_path}. "
        f"Candidate plan path: {candidate_path}. "
        "Perform your review."
    )
    result = subprocess.run(
        [
            "kiro-cli",
            "chat",
            "--agent",
            "reliability-reviewer",
            "--no-interactive",
            prompt,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_AGENT_TIMEOUT_SECONDS,
    )
    clean_stdout = _ANSI_ESCAPE_RE.sub("", result.stdout)
    json_start = clean_stdout.rfind('{"agent"')
    if json_start == -1:
        raise AssertionError(
            "reliability-reviewer agent produced no trailing JSON verdict "
            f"object.\nreturncode={result.returncode}\nstdout={clean_stdout!r}"
            f"\nstderr={_ANSI_ESCAPE_RE.sub('', result.stderr)!r}"
        )
    return json.loads(clean_stdout[json_start:])


@unittest.skipUnless(
    _KIRO_CLI_AVAILABLE,
    "kiro-cli is not on PATH; skipping live Reliability Reviewer agent "
    "judgment tests (Requirement 12.10). Evidence-extraction-layer tests "
    "above still run and do not require kiro-cli.",
)
class ReliabilityReviewerLiveJudgmentTests(unittest.TestCase):
    """Verdict-level (`PASS`/`FAIL`/`INCOMPLETE`) behavior of the live
    Reliability Reviewer agent (Task 8.2), invoked via
    `kiro-cli chat --agent reliability-reviewer --no-interactive` against
    the fixture pairs, per Requirements 6.1-6.11 and 12.5/12.6/12.10."""

    def test_rel001_transition_produces_fail_with_rel001_finding(self):
        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_REL001)
        self.assertEqual(verdict.get("status"), "FAIL")
        findings = verdict.get("findings", [])
        rule_ids = {finding.get("rule_id") for finding in findings}
        self.assertIn("REL-001", rule_ids)

    def test_br001_transition_produces_fail_with_br001_finding(self):
        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_BR001)
        self.assertEqual(verdict.get("status"), "FAIL")
        findings = verdict.get("findings", [])
        rule_ids = {finding.get("rule_id") for finding in findings}
        self.assertIn("BR-001", rule_ids)

    def test_safe_pair_produces_pass(self):
        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_SAFE)
        self.assertEqual(verdict.get("status"), "PASS")
        self.assertEqual(verdict.get("findings", []), [])

    def test_malformed_fixture_produces_incomplete_never_pass_never_fabricated(self):
        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_MALFORMED)
        self.assertEqual(verdict.get("status"), "INCOMPLETE")
        self.assertNotEqual(verdict.get("status"), "PASS")
        self.assertEqual(verdict.get("findings", []), [])

    def test_missing_field_fixture_produces_incomplete_never_pass(self):
        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_MISSING_FIELD)
        self.assertEqual(verdict.get("status"), "INCOMPLETE")
        self.assertEqual(verdict.get("findings", []), [])

    def test_missing_resource_fixture_produces_incomplete_never_pass(self):
        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_MISSING_RESOURCE)
        self.assertEqual(verdict.get("status"), "INCOMPLETE")
        self.assertEqual(verdict.get("findings", []), [])

    def test_every_finding_across_fixtures_has_allowed_rule_id(self):
        """Property check (design.md Property 2): every finding returned
        across all fixture pairs has `rule_id in {REL-001, BR-001}`."""
        for baseline_path, candidate_path in (
            (BASELINE, CANDIDATE_SAFE),
            (BASELINE, CANDIDATE_REL001),
            (BASELINE, CANDIDATE_BR001),
        ):
            with self.subTest(candidate=os.path.basename(candidate_path)):
                verdict = _invoke_reliability_reviewer(baseline_path, candidate_path)
                self.assertIn(verdict.get("status"), VERDICT_STATUSES)
                for finding in verdict.get("findings", []):
                    self.assertIn(finding.get("rule_id"), ALLOWED_RULE_IDS)

    def test_findings_derived_only_from_fixture_after_values(self):
        """Property check (design.md Property 1): every finding is derived
        only from comparing two fixture plan JSON files' `.change.after`
        values, never from a single plan's `.change.before`.

        Verified structurally: every fixture plan JSON used in this suite
        has no `.change.before` data available to the reviewer at all (the
        evidence tool only ever reads `.change.after`; see
        `scripts/reliability_rules.py`'s `_find_resource_after`), and the
        `baseline_value`/`candidate_value` recorded on each finding must
        exactly equal the `.change.after` values read directly from the two
        named fixture files for this test's baseline/candidate pair -
        never any other value.
        """
        with open(BASELINE) as f:
            baseline_plan = json.load(f)
        with open(CANDIDATE_REL001) as f:
            candidate_plan = json.load(f)

        def _after(plan, address):
            for entry in plan["resource_changes"]:
                if entry.get("address") == address:
                    return entry["change"]["after"]
            raise AssertionError(f"{address} not found in fixture")

        expected_baseline_desired_count = _after(
            baseline_plan, "aws_ecs_service.payments_api"
        )["desired_count"]
        expected_candidate_desired_count = _after(
            candidate_plan, "aws_ecs_service.payments_api"
        )["desired_count"]

        verdict = _invoke_reliability_reviewer(BASELINE, CANDIDATE_REL001)
        self.assertEqual(verdict.get("status"), "FAIL")
        rel001_findings = [
            finding
            for finding in verdict.get("findings", [])
            if finding.get("rule_id") == "REL-001"
        ]
        self.assertTrue(rel001_findings, "expected a REL-001 finding")
        finding = rel001_findings[0]
        self.assertEqual(finding.get("baseline_value"), expected_baseline_desired_count)
        self.assertEqual(finding.get("candidate_value"), expected_candidate_desired_count)


if __name__ == "__main__":
    unittest.main()

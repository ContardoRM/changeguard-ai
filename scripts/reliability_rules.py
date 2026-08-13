#!/usr/bin/env python3
"""Reliability evidence extraction.

Boundary decision (paths vs. parsed dicts): identical to
`scripts/security_rules.py` and for the same reason — design.md's
"Security/Reliability Evidence Extraction" section states these libraries'
allowed inputs are "paths to two plan JSON artifacts ... and the rule
ID(s) to extract evidence for." Every public function in this module
therefore accepts *file paths* (strings) to Terraform plan JSON artifacts
and performs its own `json.load`, rather than accepting already-parsed
dicts. Invalid or unreadable JSON at a given path is a first-class,
in-scope condition handled by this module (see `EvidenceStatus.MALFORMED`
below).

Scope boundary (read this before extending this module):
    This module performs deterministic extraction of structural facts
    from Terraform plan JSON only. Given a resource address and a field
    name, it answers exactly one question: "what value is present in the
    baseline plan's evidence, and what value is present in the
    candidate/remediated plan's evidence, for this resource and field?"

    This module does NOT:
      - compare an extracted value against any threshold to decide
        whether a change is acceptable (e.g. it never compares one
        count value against another, or decides whether a boolean
        transition is a problem);
      - classify a fact pattern by severity or risk category;
      - produce a policy verdict of any kind, a finding, or a
        remediation recommendation.

    Judging whether an extracted fact pattern is acceptable is the sole
    responsibility of a downstream policy-owning component that is not
    implemented in this module and is out of scope for this change. This
    module only ever returns plain extracted values or one of the
    structural, non-judgmental `EvidenceStatus` outcomes defined below.

Resource and field paths (grounded in the real `terraform show -json`
output committed at `artifacts/baseline-plan.json`):

    ECS service:  resource `aws_ecs_service.payments_api`,
                  field `resource_changes[].change.after.desired_count`
                  (int; `bool` is explicitly rejected even though Python's
                  `bool` is a subclass of `int`).
    RDS instance: resource `aws_db_instance.payments_db`,
                  field `resource_changes[].change.after.deletion_protection`
                  (bool).
"""

import enum
import json
from dataclasses import dataclass
from typing import Optional, Union


ECS_SERVICE_RESOURCE = "aws_ecs_service.payments_api"
RDS_INSTANCE_RESOURCE = "aws_db_instance.payments_db"


class EvidenceStatus(enum.Enum):
    """The structural availability of a piece of extracted evidence.

    This is a factual/structural observation about the plan JSON itself
    (was the resource present? was the field present and correctly
    typed?) and carries no judgment about whether the underlying value
    is acceptable. Semantically identical to, and used consistently
    with, `scripts/security_rules.py`'s `EvidenceStatus`.
    """

    AVAILABLE = "AVAILABLE"
    MISSING_RESOURCE = "MISSING_RESOURCE"
    MISSING_FIELD = "MISSING_FIELD"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True)
class EvidenceResult:
    """The outcome of attempting to extract one piece of evidence.

    When `status` is `EvidenceStatus.AVAILABLE`, `value` holds the
    extracted, correctly-typed value (`int` for `desired_count`, `bool`
    for `deletion_protection`). For every other status, `value` is
    `None` and `detail` describes, in plain structural terms, what could
    not be found or validated. Missing or malformed evidence is never
    replaced with a default value.
    """

    status: EvidenceStatus
    detail: str
    value: Optional[Union[int, bool]] = None


@dataclass(frozen=True)
class FieldEvidence:
    """Baseline and candidate evidence results for a single field."""

    resource: str
    baseline: EvidenceResult
    candidate: EvidenceResult


@dataclass(frozen=True)
class ReliabilityEvidence:
    """Combined ECS and RDS evidence for one baseline/candidate pair."""

    ecs_service: FieldEvidence
    rds_instance: FieldEvidence


def _load_plan(plan_path):
    """Read and parse a Terraform plan JSON file from disk.

    Returns a tuple `(plan_dict, error)`, matching
    `security_rules._load_plan`'s contract exactly, for the same reasons.
    """
    try:
        with open(plan_path, "r") as plan_file:
            data = json.load(plan_file)
    except (OSError, json.JSONDecodeError) as exc:
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail=f"could not read/parse plan JSON at {plan_path!r}: {exc}",
        )

    if not isinstance(data, dict):
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail=f"plan JSON at {plan_path!r} did not parse to a JSON object",
        )

    return data, None


def _find_resource_after(plan, address):
    """Locate `resource_changes[].change.after` for the given address.

    Returns a tuple `(after_dict, error)`, matching
    `security_rules._find_resource_after`'s contract exactly.
    """
    resource_changes = plan.get("resource_changes")
    if not isinstance(resource_changes, list):
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail="plan JSON has no usable 'resource_changes' list",
        )

    for entry in resource_changes:
        if not isinstance(entry, dict) or entry.get("address") != address:
            continue

        change = entry.get("change")
        if not isinstance(change, dict):
            return None, EvidenceResult(
                status=EvidenceStatus.MISSING_FIELD,
                detail=f"resource {address!r} has no usable 'change' block",
            )

        after = change.get("after")
        if not isinstance(after, dict):
            return None, EvidenceResult(
                status=EvidenceStatus.MISSING_FIELD,
                detail=f"resource {address!r} has no usable 'change.after' block",
            )

        return after, None

    return None, EvidenceResult(
        status=EvidenceStatus.MISSING_RESOURCE,
        detail=f"resource {address!r} not found in resource_changes",
    )


def _extract_typed_field(plan_path, address, field_name, expected_type):
    """Extract a single, strictly-typed field from one plan JSON file.

    `expected_type` is either `int` (with `bool` explicitly rejected,
    since Python's `bool` is a subclass of `int`) or `bool` (with only
    genuine `bool` values accepted).
    """
    plan, error = _load_plan(plan_path)
    if error is not None:
        return error

    after, error = _find_resource_after(plan, address)
    if error is not None:
        return error

    if field_name not in after:
        return EvidenceResult(
            status=EvidenceStatus.MISSING_FIELD,
            detail=f"'{field_name}' is missing from {address!r}'s change.after",
        )

    value = after.get(field_name)

    if expected_type is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected_type is bool:
        valid = isinstance(value, bool)
    else:  # pragma: no cover - defensive, not reachable via public API
        valid = False

    if not valid:
        return EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail=(
                f"'{field_name}' on {address!r} is not a {expected_type.__name__} "
                f"(got {type(value).__name__})"
            ),
        )

    return EvidenceResult(
        status=EvidenceStatus.AVAILABLE,
        detail=f"'{field_name}' extracted",
        value=value,
    )


def extract_ecs_desired_count_evidence(baseline_plan_path, candidate_plan_path):
    """Extract baseline and candidate `desired_count` evidence.

    Args:
        baseline_plan_path: path to the baseline plan JSON artifact.
        candidate_plan_path: path to the candidate (or remediated) plan
            JSON artifact.

    Returns:
        A `FieldEvidence` for `aws_ecs_service.payments_api`'s
        `desired_count` field. `desired_count` is validated strictly as
        `int`; a `bool` value (even though `bool` is an `int` subclass in
        Python) is rejected as malformed.
    """
    return FieldEvidence(
        resource=ECS_SERVICE_RESOURCE,
        baseline=_extract_typed_field(
            baseline_plan_path, ECS_SERVICE_RESOURCE, "desired_count", int
        ),
        candidate=_extract_typed_field(
            candidate_plan_path, ECS_SERVICE_RESOURCE, "desired_count", int
        ),
    )


def extract_rds_deletion_protection_evidence(baseline_plan_path, candidate_plan_path):
    """Extract baseline and candidate `deletion_protection` evidence.

    Args:
        baseline_plan_path: path to the baseline plan JSON artifact.
        candidate_plan_path: path to the candidate (or remediated) plan
            JSON artifact.

    Returns:
        A `FieldEvidence` for `aws_db_instance.payments_db`'s
        `deletion_protection` field, validated strictly as `bool`.
    """
    return FieldEvidence(
        resource=RDS_INSTANCE_RESOURCE,
        baseline=_extract_typed_field(
            baseline_plan_path, RDS_INSTANCE_RESOURCE, "deletion_protection", bool
        ),
        candidate=_extract_typed_field(
            candidate_plan_path, RDS_INSTANCE_RESOURCE, "deletion_protection", bool
        ),
    )


def extract_reliability_evidence(baseline_plan_path, candidate_plan_path):
    """Extract combined ECS `desired_count` and RDS `deletion_protection` evidence.

    Args:
        baseline_plan_path: path to the baseline plan JSON artifact.
        candidate_plan_path: path to the candidate (or remediated) plan
            JSON artifact.

    Returns:
        A `ReliabilityEvidence` with both `ecs_service` and `rds_instance`
        `FieldEvidence` records. Callers must check each
        `EvidenceResult.status` before reading `.value`.
    """
    return ReliabilityEvidence(
        ecs_service=extract_ecs_desired_count_evidence(
            baseline_plan_path, candidate_plan_path
        ),
        rds_instance=extract_rds_deletion_protection_evidence(
            baseline_plan_path, candidate_plan_path
        ),
    )

#!/usr/bin/env python3
"""Security evidence extraction.

Boundary decision (paths vs. parsed dicts): design.md's "Security/Reliability
Evidence Extraction" section states these libraries' allowed inputs are
"paths to two plan JSON artifacts ... and the rule ID(s) to extract evidence
for." That wording is unambiguous, so every public function in this module
accepts *file paths* (strings) to Terraform plan JSON artifacts and performs
its own `json.load`, rather than accepting already-parsed dicts. Invalid or
unreadable JSON at a given path is therefore a first-class, in-scope
condition handled by this module (see `EvidenceStatus.MALFORMED` below).

Scope boundary (read this before extending this module):
    This module performs deterministic extraction of structural facts from
    Terraform plan JSON only. Given a resource address and a field path, it
    answers exactly one question: "what value is present in the baseline
    plan's evidence, and what value is present in the candidate/remediated
    plan's evidence, for this resource and field?"

    This module does NOT:
      - compare an extracted value against any threshold, allow-list, or
        deny-list to decide whether it is acceptable;
      - classify a fact pattern by severity or risk category;
      - produce a policy verdict of any kind, a finding, or a
        remediation recommendation.

    Judging whether an extracted fact pattern is acceptable is the sole
    responsibility of a downstream policy-owning component that is not
    implemented in this module and is out of scope for this change. This
    module only ever returns plain extracted values or one of the
    structural, non-judgmental `EvidenceStatus` outcomes defined below.

    The only comparisons performed here are structural ones needed to
    locate the correct piece of evidence (e.g. "which ingress array entry
    covers port 22 versus port 5432?"). No comparison in this module
    evaluates whether an extracted value is safe, acceptable, or within
    policy.

Resource and field paths (grounded in the real `terraform show -json`
output committed at `artifacts/baseline-plan.json`):

    Resource: `aws_security_group.payments_sg`
    Field:    `resource_changes[].change.after.ingress[]`, an array of
              ingress block objects, each with `.protocol` (str),
              `.from_port` (int), `.to_port` (int), and `.cidr_blocks`
              (list of str).

Two ingress entries are tracked: the one covering TCP port 22 and the one
covering TCP port 5432, matched structurally by
`from_port <= <port> <= to_port` with `protocol` equal to `"tcp"` or
`"-1"` (the Terraform AWS provider's "all protocols" sentinel).
`cidr_blocks` is always returned as a list, exactly as read from the plan
JSON. It is never flattened, joined into a string, or inspected for
whether any entry within it means "public".
"""

import enum
import json
from dataclasses import dataclass
from typing import Dict, List, Optional


SECURITY_GROUP_RESOURCE = "aws_security_group.payments_sg"

_TRACKED_PORTS = ("22", "5432")


class EvidenceStatus(enum.Enum):
    """The structural availability of a piece of extracted evidence.

    This is a factual/structural observation about the plan JSON itself
    (was the resource present? was the field present and correctly typed?)
    and carries no judgment about whether the underlying value is
    acceptable.
    """

    AVAILABLE = "AVAILABLE"
    MISSING_RESOURCE = "MISSING_RESOURCE"
    MISSING_FIELD = "MISSING_FIELD"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True)
class IngressRuleEvidence:
    """Verbatim, typed facts read from one ingress block."""

    protocol: str
    from_port: int
    to_port: int
    cidr_blocks: List[str]


@dataclass(frozen=True)
class EvidenceResult:
    """The outcome of attempting to extract one piece of evidence.

    When `status` is `EvidenceStatus.AVAILABLE`, `value` holds the
    extracted `IngressRuleEvidence`. For every other status, `value` is
    `None` and `detail` describes, in plain structural terms, what could
    not be found or validated. Missing or malformed evidence is never
    replaced with a default value.
    """

    status: EvidenceStatus
    detail: str
    value: Optional[IngressRuleEvidence] = None


@dataclass(frozen=True)
class PortEvidence:
    """Baseline and candidate evidence results for a single tracked port."""

    baseline: EvidenceResult
    candidate: EvidenceResult


@dataclass(frozen=True)
class SecurityGroupEvidence:
    """Combined evidence for `aws_security_group.payments_sg`.

    `baseline` and `candidate` are each a mapping from port string
    (`"22"`, `"5432"`) to an `EvidenceResult` for that port in that plan.
    """

    resource: str
    baseline: Dict[str, EvidenceResult]
    candidate: Dict[str, EvidenceResult]


def _load_plan(plan_path):
    """Read and parse a Terraform plan JSON file from disk.

    Returns a tuple `(plan_dict, error)`. On success, `plan_dict` is the
    parsed JSON object and `error` is `None`. On failure (file cannot be
    read, content is not valid JSON, or the parsed JSON is not an object),
    `plan_dict` is `None` and `error` is an `EvidenceResult` with status
    `MALFORMED`.
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

    Returns a tuple `(after_dict, error)`. `error` is `None` on success.
    Otherwise it is an `EvidenceResult` with status `MISSING_RESOURCE`
    (no `resource_changes` entry has this address) or `MISSING_FIELD`
    (the entry exists but has no usable `change.after` object).
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


def _find_ingress_entry(after, port):
    """Locate the ingress block entry structurally covering `port`.

    This is a structural lookup only ("which array entry corresponds to
    this port?"), not a policy comparison of the entry's contents.
    """
    ingress = after.get("ingress")
    if not isinstance(ingress, list):
        return None, EvidenceResult(
            status=EvidenceStatus.MISSING_FIELD,
            detail="'ingress' field is missing or not a list",
        )

    for entry in ingress:
        if not isinstance(entry, dict):
            continue

        from_port = entry.get("from_port")
        to_port = entry.get("to_port")
        protocol = entry.get("protocol")

        if not _is_plain_int(from_port) or not _is_plain_int(to_port):
            continue

        if from_port <= port <= to_port and protocol in ("tcp", "-1"):
            return entry, None

    return None, EvidenceResult(
        status=EvidenceStatus.MISSING_FIELD,
        detail=f"no ingress entry structurally covers port {port}",
    )


def _is_plain_int(value):
    """True for a genuine int, explicitly excluding bool (a bool subclass)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _build_ingress_evidence(entry):
    """Validate and extract typed fields from one ingress block entry.

    Returns `(IngressRuleEvidence, None)` on success, or
    `(None, EvidenceResult(status=MALFORMED, ...))` if any field is
    missing or not the expected type.
    """
    protocol = entry.get("protocol")
    from_port = entry.get("from_port")
    to_port = entry.get("to_port")
    cidr_blocks = entry.get("cidr_blocks")

    if not isinstance(protocol, str):
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail="'protocol' is missing or not a string",
        )
    if not _is_plain_int(from_port):
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail="'from_port' is missing or not an int",
        )
    if not _is_plain_int(to_port):
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail="'to_port' is missing or not an int",
        )
    if not isinstance(cidr_blocks, list) or not all(
        isinstance(cidr, str) for cidr in cidr_blocks
    ):
        return None, EvidenceResult(
            status=EvidenceStatus.MALFORMED,
            detail="'cidr_blocks' is missing or not a list of strings",
        )

    return (
        IngressRuleEvidence(
            protocol=protocol,
            from_port=from_port,
            to_port=to_port,
            cidr_blocks=list(cidr_blocks),
        ),
        None,
    )


def _extract_single_ingress(plan_path, port):
    """Extract ingress evidence for one port from one plan JSON file."""
    plan, error = _load_plan(plan_path)
    if error is not None:
        return error

    after, error = _find_resource_after(plan, SECURITY_GROUP_RESOURCE)
    if error is not None:
        return error

    entry, error = _find_ingress_entry(after, port)
    if error is not None:
        return error

    evidence, error = _build_ingress_evidence(entry)
    if error is not None:
        return error

    return EvidenceResult(
        status=EvidenceStatus.AVAILABLE,
        detail="ingress evidence extracted",
        value=evidence,
    )


def extract_ingress_evidence(baseline_plan_path, candidate_plan_path, port):
    """Extract baseline and candidate ingress evidence for a single port.

    Args:
        baseline_plan_path: path to the baseline plan JSON artifact.
        candidate_plan_path: path to the candidate (or remediated) plan
            JSON artifact.
        port: the TCP port to structurally match an ingress entry against
            (e.g. `22` or `5432`).

    Returns:
        A `PortEvidence` with independently computed `baseline` and
        `candidate` `EvidenceResult`s. Each side is evaluated
        independently; a failure on one side does not affect the other.
    """
    return PortEvidence(
        baseline=_extract_single_ingress(baseline_plan_path, port),
        candidate=_extract_single_ingress(candidate_plan_path, port),
    )


def extract_security_group_evidence(baseline_plan_path, candidate_plan_path):
    """Extract combined port-22 and port-5432 ingress evidence.

    Args:
        baseline_plan_path: path to the baseline plan JSON artifact.
        candidate_plan_path: path to the candidate (or remediated) plan
            JSON artifact.

    Returns:
        A `SecurityGroupEvidence` whose `baseline` and `candidate` fields
        each map `"22"` and `"5432"` to an `EvidenceResult`. Callers must
        check each `EvidenceResult.status` before reading `.value`.
    """
    port_22 = extract_ingress_evidence(baseline_plan_path, candidate_plan_path, 22)
    port_5432 = extract_ingress_evidence(baseline_plan_path, candidate_plan_path, 5432)

    return SecurityGroupEvidence(
        resource=SECURITY_GROUP_RESOURCE,
        baseline={"22": port_22.baseline, "5432": port_5432.baseline},
        candidate={"22": port_22.candidate, "5432": port_5432.candidate},
    )

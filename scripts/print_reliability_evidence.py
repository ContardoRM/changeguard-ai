#!/usr/bin/env python3
"""Print Reliability evidence as JSON.

This is a thin serialization CLI, not a policy engine. It calls
`scripts/reliability_rules.extract_reliability_evidence` and prints the
resulting evidence record as JSON to stdout, so a Kiro Reviewer Agent
(the Reliability Reviewer) can be handed structured evidence as text
input without the agent needing to import Python itself.

This script performs no rule evaluation:
    - It does not compare baseline `desired_count` against candidate
      `desired_count` for policy purposes.
    - It does not compare baseline `deletion_protection` against
      candidate `deletion_protection` for policy purposes.
    - It does not return, print, or compute any policy verdict, rule
      identifier, severity, or remediation recommendation.

It only calls the extraction function and serializes whatever
`EvidenceStatus`/value it receives, verbatim, to JSON.

CLI contract:

    python3 scripts/print_reliability_evidence.py \
        --baseline <path> --candidate <path>
"""

import argparse
import dataclasses
import enum
import json
import sys

from reliability_rules import extract_reliability_evidence


def _serialize(value):
    """Recursively convert dataclasses/enums into plain JSON-serializable data."""
    if isinstance(value, enum.Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return {
            field.name: _serialize(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="print_reliability_evidence.py",
        description=(
            "Print deterministically extracted ECS/RDS evidence as JSON. "
            "Performs no policy evaluation."
        ),
    )
    parser.add_argument("--baseline", required=True, help="Path to the baseline plan JSON.")
    parser.add_argument("--candidate", required=True, help="Path to the candidate/remediated plan JSON.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    evidence = extract_reliability_evidence(args.baseline, args.candidate)
    print(json.dumps(_serialize(evidence), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

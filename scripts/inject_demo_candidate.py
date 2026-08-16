#!/usr/bin/env python3
"""Demo-only Terraform candidate-injection helper.

NOT part of ChangeGuard's reviewed product surface (Security Reviewer,
Reliability Reviewer, Remediator, `apply_remediation.py`, or the Kiro Crew
DAG). This script exists solely so `make demo-rel`/`make demo-sec` can
deterministically move `terraform/main.tf` FROM the safe baseline TO one of
the two documented, judge-facing candidate scenarios, without hand-editing
HCL or resorting to a fragile ad-hoc `sed` replacement across a multi-line
HCL block.

It performs no rule evaluation of its own and adds no new rule: it reuses
`apply_remediation.py`'s own mechanical, whitelisted edit primitives
(`_find_resource_block`, `_replace_cidr_blocks`, `_replace_scalar_attribute`,
`_atomic_write`) — the exact same narrow, single-attribute,
verified-current-value edit engine already reviewed and tested for
remediation — just supplying the candidate/unsafe target value instead of
the baseline/restore value. Every check `apply_remediation.py` enforces on a
write (exactly one matching resource block, exactly one matching
attribute/ingress entry, current value differs from the new value) applies
here identically, so this script fails closed (non-zero exit, no write) if
`terraform/main.tf` is not in the exact documented safe-baseline shape, or
if the requested scenario is already injected.

Supported `--rule-id` values are fixed to the two scenarios `make demo-rel`/
`make demo-sec` need — SEC-002 and BR-001 have no dedicated `make` target
and are intentionally not included here (design.md's Five-Minute Demo
Walkthrough only demonstrates one scenario at a time; a judge who wants a
SEC-002/BR-001 walkthrough edits `terraform/main.tf` by hand, exactly as
described there):

    SEC-001 -> aws_security_group.payments_sg TCP/22 cidr_blocks: "10.0.0.0/8" -> "0.0.0.0/0"
    REL-001 -> aws_ecs_service.payments_api desired_count: 3 -> 1

CLI contract:

    python3 scripts/inject_demo_candidate.py --terraform-dir <path> --rule-id <SEC-001|REL-001>
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apply_remediation import (  # noqa: E402
    RemediationError,
    _atomic_write,
    _find_resource_block,
    _replace_cidr_blocks,
    _replace_scalar_attribute,
)

# Fixed whitelist of the two demo scenarios this script knows how to
# inject. Mirrors apply_remediation.py's own SUPPORTED_RULES posture: no
# fallback, no free-form target, no rule ID outside this set.
DEMO_SCENARIOS = {
    "SEC-001": {
        "resource": "aws_security_group.payments_sg",
        "kind": "ingress_cidr",
        "port": 22,
        "candidate_value": "0.0.0.0/0",
    },
    "REL-001": {
        "resource": "aws_ecs_service.payments_api",
        "kind": "int_attr",
        "attribute": "desired_count",
        "candidate_value": 1,
    },
}


def parse_args(argv=None):
    """Parse CLI arguments. Accepts exactly `--terraform-dir` and
    `--rule-id`, the latter restricted to the two supported demo
    scenarios."""
    parser = argparse.ArgumentParser(
        prog="inject_demo_candidate.py",
        description=(
            "Demo-only helper: deterministically edit terraform/main.tf "
            "from the safe baseline to one documented candidate scenario "
            "(SEC-001 or REL-001). Not part of ChangeGuard's reviewed "
            "product surface -- performs no SEC/REL/BR policy judgment."
        ),
    )
    parser.add_argument(
        "--terraform-dir",
        default="terraform",
        help="Path to the Terraform configuration directory containing main.tf.",
    )
    parser.add_argument(
        "--rule-id",
        required=True,
        choices=sorted(DEMO_SCENARIOS),
        help="Which documented demo scenario to inject.",
    )
    return parser.parse_args(argv)


def inject_demo_candidate(main_tf_path, rule_id):
    """Return the fully reconstructed file content with exactly one
    scenario's candidate value injected. Raises `RemediationError` (no
    write performed by this function itself -- the caller writes) if the
    target resource/attribute is missing, ambiguous, or already at the
    candidate value."""
    scenario = DEMO_SCENARIOS[rule_id]

    with open(main_tf_path, "r") as main_tf_file:
        content = main_tf_file.read()

    resource_type, resource_name = scenario["resource"].split(".", 1)
    block_start, block_end = _find_resource_block(content, resource_type, resource_name)

    if scenario["kind"] == "ingress_cidr":
        return _replace_cidr_blocks(
            content, block_start, block_end, scenario["port"], scenario["candidate_value"]
        )
    if scenario["kind"] == "int_attr":
        return _replace_scalar_attribute(
            content,
            block_start,
            block_end,
            scenario["attribute"],
            r"\d+",
            int,
            str(scenario["candidate_value"]),
            scenario["candidate_value"],
        )
    raise RemediationError(  # pragma: no cover - unreachable, DEMO_SCENARIOS is fixed above
        f"Unhandled demo scenario kind {scenario['kind']!r} for rule {rule_id!r}."
    )


def main(argv=None):
    args = parse_args(argv)
    main_tf_path = os.path.join(args.terraform_dir, "main.tf")

    try:
        new_content = inject_demo_candidate(main_tf_path, args.rule_id)
    except RemediationError as exc:
        print(f"inject_demo_candidate.py: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"inject_demo_candidate.py: could not read {main_tf_path!r}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        _atomic_write(main_tf_path, new_content)
    except OSError as exc:
        print(
            f"inject_demo_candidate.py: could not write {main_tf_path!r}: {exc}",
            file=sys.stderr,
        )
        return 1

    scenario = DEMO_SCENARIOS[args.rule_id]
    print(
        f"inject_demo_candidate.py: injected {args.rule_id} candidate scenario "
        f"into {main_tf_path} (resource={scenario['resource']}, "
        f"candidate_value={scenario['candidate_value']!r})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

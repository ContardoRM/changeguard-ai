#!/usr/bin/env python3
"""Remediation Script.

Deterministic local CLI tool that applies exactly one narrowly scoped,
approved correction to `terraform/main.tf` for a supported ChangeGuard rule
ID. It contains no risk-detection or rule-evaluation logic: it never decides
whether a CIDR is "public," whether a `desired_count` transition is
dangerous, or whether `deletion_protection` should be disabled. Those
judgments belong entirely to the Security Reviewer / Reliability Reviewer
agents (and, upstream of this script, the human approval gate). This script
only knows HOW to mechanically apply an already-approved restore value to
the exact expected attribute or ingress block for a supported rule ID —
never WHAT should be approved.

CLI contract (design.md "Remediation Script"):

    python3 scripts/apply_remediation.py \
        --terraform-dir <path> \
        --rule-id <SEC-001|SEC-002|REL-001|BR-001> \
        --resource <address> \
        --restore-value <value> \
        [--result-file <path>]

Only `--terraform-dir`, `--rule-id`, `--resource`, `--restore-value`, and the
optional `--result-file` are accepted. There is no argument that accepts
arbitrary file paths outside `--terraform-dir`/`--result-file` (the
Terraform target file is always `<terraform-dir>/main.tf`, a fixed filename
this script chooses, never a path supplied by the caller), arbitrary HCL
content, arbitrary replacement expressions, shell commands, or free-form
remediation instructions.

`--result-file` (Phase 8B transport correction; design.md "Kiro Crew 0.2.0
Orchestration Mapping"): a caller-supplied, per-invocation path this script
writes its own structured execution result to, atomically, ONLY on a fully
successful, validated mutation. This exists because a live investigation
confirmed `kiro-cli` chat stdout is not a reliable machine-readable
transport for the Remediator agent's result — that same stdout stream
simultaneously carries human-readable narration, the underlying shell
tool's own stdout (this script's own `print(json.dumps(...))` line,
echoed back by the CLI's tool-output rendering), Kiro's progress/credits
UI text, and the final assistant response, so more than one JSON-shaped
fragment can legitimately appear in one chat transcript. `--result-file`
gives the caller (`scripts/run_remediation_stage.py`) a deterministic,
unambiguous execution artifact to validate directly, independently of
whatever the agent's chat stdout happens to contain. When `--result-file`
is omitted, behavior is unchanged from before this correction (stdout-only,
for backward compatibility / direct CLI use). This script still performs
no SEC-001/SEC-002/REL-001/BR-001 policy judgment — `--result-file`
records mechanical execution evidence (what was mechanically changed),
never a policy decision about whether that change was warranted.

Supported rule IDs and their fixed resource/attribute bindings:

    SEC-001 -> aws_security_group.payments_sg -> TCP/22 ingress cidr_blocks
    SEC-002 -> aws_security_group.payments_sg -> TCP/5432 ingress cidr_blocks
    REL-001 -> aws_ecs_service.payments_api   -> desired_count
    BR-001  -> aws_db_instance.payments_db    -> deletion_protection

No other rule ID is supported. There is no fallback/generic remediation
path: an unrecognized `--rule-id` always exits non-zero without touching
`terraform/main.tf`.

Design note on the "current value must differ from the restore value"
check: this script requires that the value currently present at the target
attribute/ingress block is *different* from `--restore-value` before it
will write anything (see `_require_change_needed`). This is a deliberate,
rule-agnostic safety check — it verifies a genuine "unsafe/current" target
exists to correct — and it is implemented without any knowledge of what
values are considered "safe" or "unsafe" for a given rule (that would be a
policy judgment, which this script deliberately never makes). If the
current value already equals the requested restore value, there is nothing
to remediate, and the script refuses (non-zero exit, no write) rather than
performing a silent no-op.
"""

import argparse
import ipaddress
import json
import os
import re
import sys
import tempfile


# Fixed whitelist of supported rule IDs and their exact resource/attribute
# bindings. This is the only "remediation path" this script knows about —
# there is no fallback for any other rule ID.
SUPPORTED_RULES = {
    "SEC-001": {
        "resource": "aws_security_group.payments_sg",
        "kind": "ingress_cidr",
        "port": 22,
    },
    "SEC-002": {
        "resource": "aws_security_group.payments_sg",
        "kind": "ingress_cidr",
        "port": 5432,
    },
    "REL-001": {
        "resource": "aws_ecs_service.payments_api",
        "kind": "int_attr",
        "attribute": "desired_count",
    },
    "BR-001": {
        "resource": "aws_db_instance.payments_db",
        "kind": "bool_attr",
        "attribute": "deletion_protection",
    },
}


class RemediationError(Exception):
    """Raised for any validation, targeting, or ambiguity failure.

    Every `RemediationError` results in a non-zero exit and, by
    construction, no write to `terraform/main.tf` — every raise site in
    this module occurs strictly before the single atomic write at the end
    of `apply_remediation`.
    """


def parse_args(argv=None):
    """Parse CLI arguments for the Remediation Script.

    Accepts exactly `--terraform-dir`, `--rule-id`, `--resource`, and
    `--restore-value`, all required, all plain strings. No other argument
    is accepted.
    """
    parser = argparse.ArgumentParser(
        prog="apply_remediation.py",
        description=(
            "Apply one narrowly scoped, deterministic Terraform source "
            "correction for a supported ChangeGuard rule ID. Performs no "
            "policy evaluation of its own."
        ),
    )
    parser.add_argument(
        "--terraform-dir",
        required=True,
        help="Path to the Terraform configuration directory containing main.tf.",
    )
    parser.add_argument(
        "--rule-id",
        required=True,
        help="Supported rule ID: SEC-001, SEC-002, REL-001, or BR-001.",
    )
    parser.add_argument(
        "--resource",
        required=True,
        help="Terraform resource address the rule ID is expected to bind to.",
    )
    parser.add_argument(
        "--restore-value",
        required=True,
        help="The approved baseline value to restore (from Finding.baseline_value).",
    )
    parser.add_argument(
        "--result-file",
        default=None,
        help=(
            "Optional path to atomically write this invocation's structured "
            "execution result to, ONLY on full success. Never written on "
            "any failure path. See module docstring for why this exists."
        ),
    )
    return parser.parse_args(argv)


def _validate_rule_id(rule_id):
    """Return the rule spec for a supported rule ID, or raise.

    This is the whitelist enforcement point (Requirement 9.7's script-level
    backstop): if `rule_id` is not exactly one of the four supported IDs,
    there is no fallback path — this function always raises.
    """
    if rule_id not in SUPPORTED_RULES:
        raise RemediationError(
            f"Unsupported rule ID {rule_id!r}. Supported rule IDs: "
            f"{sorted(SUPPORTED_RULES)}."
        )
    return SUPPORTED_RULES[rule_id]


def _validate_resource(rule_id, rule_spec, resource):
    """Reject if `resource` does not match the rule's fixed binding."""
    expected = rule_spec["resource"]
    if resource != expected:
        raise RemediationError(
            f"Resource {resource!r} does not match the expected resource "
            f"{expected!r} for rule {rule_id!r}."
        )


def _validate_restore_value(rule_id, rule_spec, raw_value):
    """Validate and parse `--restore-value` according to the rule's type.

    Returns the parsed value (a normalized CIDR string for `ingress_cidr`,
    an `int` for `int_attr`, a `bool` for `bool_attr`). Raises
    `RemediationError` for any value outside the rule's fixed type
    contract — this is a type/shape check only, never a policy judgment
    about whether the value is "safe."
    """
    kind = rule_spec["kind"]

    if kind == "ingress_cidr":
        try:
            network = ipaddress.IPv4Network(raw_value, strict=True)
        except ValueError as exc:
            raise RemediationError(
                f"--restore-value {raw_value!r} is not a valid, strict IPv4 "
                f"CIDR for rule {rule_id!r}: {exc}"
            ) from exc
        return str(network)

    if kind == "int_attr":
        if not re.fullmatch(r"\d+", raw_value):
            raise RemediationError(
                f"--restore-value {raw_value!r} is not a valid non-negative "
                f"integer literal for rule {rule_id!r}."
            )
        return int(raw_value)

    if kind == "bool_attr":
        if raw_value not in ("true", "false"):
            raise RemediationError(
                f"--restore-value {raw_value!r} is not a valid boolean "
                f"literal ('true' or 'false') for rule {rule_id!r}."
            )
        return raw_value == "true"

    raise RemediationError(f"Unhandled rule kind {kind!r} for rule {rule_id!r}.")


def _find_matching_brace(content, open_brace_index):
    """Return the index of the `}` balancing the `{` at `open_brace_index`.

    Deterministic stdlib brace counting, appropriate to this repository's
    deliberately fixed demo Terraform structure (no string literals in this
    file contain braces, so no escaping/string-awareness is required).
    """
    depth = 0
    index = open_brace_index
    length = len(content)
    while index < length:
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise RemediationError(
        "Unbalanced braces encountered while parsing terraform/main.tf; "
        "refusing to guess a target location."
    )


def _find_resource_block(content, resource_type, resource_name):
    """Locate the unique `resource "<type>" "<name>" { ... }` block.

    Returns `(open_brace_index, close_brace_index)`. Raises
    `RemediationError` if the resource block is missing (zero matches) or
    ambiguous (more than one match) — the "exactly one expected target"
    invariant applies at the resource level too.
    """
    pattern = re.compile(
        r'resource\s+"'
        + re.escape(resource_type)
        + r'"\s+"'
        + re.escape(resource_name)
        + r'"\s*\{'
    )
    matches = list(pattern.finditer(content))

    if not matches:
        raise RemediationError(
            f"Resource block for '{resource_type}.{resource_name}' was not "
            f"found in terraform/main.tf."
        )
    if len(matches) > 1:
        raise RemediationError(
            f"Resource block for '{resource_type}.{resource_name}' matched "
            f"{len(matches)} times; expected exactly one."
        )

    open_brace_index = matches[0].end() - 1
    close_brace_index = _find_matching_brace(content, open_brace_index)
    return open_brace_index, close_brace_index


def _find_ingress_block_for_port(content, block_start, block_end, port):
    """Locate the unique `ingress { ... }` sub-block covering `port`.

    Only sub-blocks whose `from_port` equals `port` are considered — this
    is a structural lookup (which ingress entry corresponds to this port?),
    never a policy comparison of the entry's `cidr_blocks` contents.
    Returns `(open_brace_index, close_brace_index)`. Raises
    `RemediationError` if zero or more than one sub-block matches.
    """
    candidates = []
    for match in re.finditer(r"ingress\s*\{", content[block_start:block_end]):
        open_idx = block_start + match.end() - 1
        close_idx = _find_matching_brace(content, open_idx)
        if close_idx > block_end:
            continue
        sub_content = content[open_idx : close_idx + 1]
        port_match = re.search(r"from_port\s*=\s*(\d+)", sub_content)
        if port_match and int(port_match.group(1)) == port:
            candidates.append((open_idx, close_idx))

    if not candidates:
        raise RemediationError(
            f"No ingress block structurally covering port {port} was found "
            f"in the targeted resource block."
        )
    if len(candidates) > 1:
        raise RemediationError(
            f"{len(candidates)} ingress blocks structurally cover port "
            f"{port}; expected exactly one."
        )
    return candidates[0]


_CIDR_LINE_PATTERN = re.compile(
    r'(?P<prefix>^[ \t]*cidr_blocks[ \t]*=[ \t]*)\[(?P<inner>[^\]]*)\][ \t]*$',
    re.MULTILINE,
)


def _locate_cidr_blocks_line(content, sub_start, sub_end):
    """Find the unique `cidr_blocks = [...]` line within a sub-block span.

    Returns the regex `Match` object (relative to `content`, not to the
    sub-block slice). Raises `RemediationError` if zero or more than one
    line matches within the sub-block.
    """
    sub_content = content[sub_start : sub_end + 1]
    matches = list(_CIDR_LINE_PATTERN.finditer(sub_content))

    if not matches:
        raise RemediationError(
            "No 'cidr_blocks' attribute was found in the targeted ingress block."
        )
    if len(matches) > 1:
        raise RemediationError(
            f"{len(matches)} 'cidr_blocks' attributes were found in the "
            f"targeted ingress block; expected exactly one."
        )

    match = matches[0]
    # Re-anchor the match's span onto the full `content` string so callers
    # can splice using absolute indices.
    return match, sub_start


def _parse_single_cidr(inner_text):
    """Parse the single CIDR string literal inside a `cidr_blocks = [...]`.

    Returns the normalized CIDR string. Raises `RemediationError` if the
    list does not contain exactly one double-quoted string literal — this
    repository's fixed demo structure always uses exactly one CIDR per
    ingress rule, and this script does not support editing a multi-entry
    list (that would require an arbitrary-list mutation this script
    deliberately does not implement).
    """
    literals = re.findall(r'"([^"]*)"', inner_text)
    if len(literals) != 1:
        raise RemediationError(
            f"Expected exactly one CIDR string literal inside cidr_blocks, "
            f"found {len(literals)}; refusing to guess which one to replace."
        )
    try:
        return str(ipaddress.IPv4Network(literals[0], strict=True))
    except ValueError as exc:
        raise RemediationError(
            f"Existing cidr_blocks value {literals[0]!r} is not a valid, "
            f"strict IPv4 CIDR: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# --result-file path confinement (Phase 8C hardening)
#
# The `--result-file` mechanism (Phase 8B transport correction, see module
# docstring) originally accepted any caller-supplied path and this script
# would `os.remove()` and then atomically write to it with no confinement
# check at all. Combined with the Remediator agent's existing broad shell
# allow-list entry `python3 scripts/apply_remediation.py *`, an
# unrestricted path (e.g. a value under system `/tmp`, or worse, an
# arbitrary path anywhere on disk via `../` traversal or a symlink) meant a
# maliciously crafted `--result-file` argument could in principle be used
# to delete or overwrite a file this script has no business touching. This
# script performs no SEC/REL/BR policy judgment, but it MUST still be
# incapable of touching anything outside its own narrow, fixed contract --
# exactly the same "no fallback, no guessing" posture already applied to
# `--rule-id`/`--resource`/`--restore-value` above, extended to
# `--result-file`.
#
# The fix: `--result-file` must resolve to a path strictly inside the
# `artifacts/` directory associated with `--terraform-dir` (its sibling
# directory -- the same repository-root-relative convention already used
# by `scripts/cleanup_run_artifacts.py`'s own `--artifacts-dir` default and
# `scripts/changeguard_launch.py`'s own default, and the convention
# `scripts/run_remediation_stage.py` now generates paths under), and its
# filename must match the fixed `.remediation-execution-<id>.json` pattern
# that only `run_remediation_stage.py` generates (see its
# `_make_result_file_path`) -- the Remediator agent never invents this
# path itself, it only ever passes through the exact path it was given
# (`.kiro/agents/remediator-prompt.md`). Validation happens BEFORE any
# filesystem side effect (before the stale-artifact `os.remove()`, before
# the success-path `_atomic_write_json()`) and resolves the path with
# `os.path.realpath` first, so `../` traversal and symlink-escape attempts
# are both defeated by the same check: whatever the path resolves to, it
# must land inside the resolved real `artifacts/` directory, full stop.
# Any failure here is fail-closed -- non-zero exit, no delete, no write.
_RESULT_FILE_NAME_PATTERN = re.compile(r"^\.remediation-execution-[A-Za-z0-9_-]+\.json$")


def _resolve_allowed_artifacts_dir(terraform_dir):
    """Return the resolved, real `artifacts/` directory associated with
    `terraform_dir`.

    Sibling-directory convention: `artifacts/` lives next to
    `terraform_dir` (i.e. `dirname(realpath(terraform_dir))/artifacts`).
    This matches how this script and `scripts/run_remediation_stage.py`
    are actually invoked in `.kiro/crew/changeguard-workflow-remediation.yaml`
    (`--terraform-dir terraform` and `artifacts/...` paths, both relative
    to the same working directory). `os.path.realpath` is used (not just
    `abspath`) so a symlinked `terraform_dir` cannot be used to smuggle a
    different, attacker-controlled "artifacts" directory into this
    computation.
    """
    terraform_real = os.path.realpath(terraform_dir)
    return os.path.realpath(os.path.join(os.path.dirname(terraform_real), "artifacts"))


def _validate_result_file_path(result_file, terraform_dir):
    """Validate `--result-file` is confined to the expected `artifacts/`
    directory with the expected filename pattern, or raise
    `RemediationError`.

    Called BEFORE any filesystem side effect involving `result_file`
    (before the stale-artifact clear, before the success-path write).
    Resolves `result_file` with `os.path.realpath` so `../` traversal and
    symlink-escape attempts are resolved to their real, final target
    before the confinement check runs -- there is no way to pass this
    check and still land outside `artifacts/`. Returns the resolved real
    path on success, so callers use one canonical, already-validated path
    for every subsequent operation rather than re-resolving (and
    potentially re-following a since-changed symlink) later.
    """
    allowed_dir = _resolve_allowed_artifacts_dir(terraform_dir)
    resolved_path = os.path.realpath(result_file)

    basename = os.path.basename(resolved_path)
    if not _RESULT_FILE_NAME_PATTERN.fullmatch(basename):
        raise RemediationError(
            f"--result-file {result_file!r} does not match the required "
            f"'.remediation-execution-<id>.json' filename pattern; refusing "
            f"to touch it."
        )

    try:
        common = os.path.commonpath([allowed_dir, resolved_path])
    except ValueError:
        common = None
    if common != allowed_dir or resolved_path == allowed_dir:
        raise RemediationError(
            f"--result-file {result_file!r} resolves to {resolved_path!r}, "
            f"which is not strictly inside the expected artifacts "
            f"directory {allowed_dir!r}; refusing to touch it."
        )

    return resolved_path


def _require_change_needed(current_value, restore_value, description):
    """Refuse a no-op remediation.

    This is a rule-agnostic safety check: it verifies a genuine
    current-vs-restore delta exists (an "unsafe/current target") before
    allowing any write. It requires no knowledge of what a "safe" or
    "unsafe" value looks like for any rule — it only compares the current
    value against the requested restore value.
    """
    if current_value == restore_value:
        raise RemediationError(
            f"{description} already equals the requested restore value "
            f"({restore_value!r}); there is no unsafe/current state to "
            f"remediate."
        )


def _replace_cidr_blocks(content, block_start, block_end, port, new_cidr):
    """Apply the SEC-001/SEC-002 mutation: rewrite one ingress block's CIDR.

    Locates the unique ingress sub-block for `port`, then the unique
    `cidr_blocks = [...]` line within it, verifies the current value
    differs from `new_cidr`, and returns the fully reconstructed file
    content with only that line's value replaced.
    """
    sub_start, sub_end = _find_ingress_block_for_port(content, block_start, block_end, port)
    match, _ = _locate_cidr_blocks_line(content, sub_start, sub_end)

    current_cidr = _parse_single_cidr(match.group("inner"))
    _require_change_needed(
        current_cidr, new_cidr, f"TCP/{port} ingress cidr_blocks"
    )

    match_abs_start = sub_start + match.start()
    match_abs_end = sub_start + match.end()
    new_line = f'{match.group("prefix")}["{new_cidr}"]'
    return content[:match_abs_start] + new_line + content[match_abs_end:]


_SCALAR_LINE_TEMPLATE = r'(?P<prefix>^[ \t]*{attribute}[ \t]*=[ \t]*)(?P<value>-?[A-Za-z0-9_.]+)[ \t]*$'


def _replace_scalar_attribute(content, block_start, block_end, attribute, expected_pattern, current_parser, new_literal, new_value):
    """Apply the REL-001/BR-001 mutation: rewrite one scalar attribute line.

    Locates the unique `<attribute> = <value>` line within
    `content[block_start:block_end]`, parses and validates the current
    value with `current_parser` (raising if it is missing or malformed),
    verifies the current value differs from `new_value`, and returns the
    fully reconstructed file content with only that line's value replaced.
    """
    pattern = re.compile(
        _SCALAR_LINE_TEMPLATE.format(attribute=re.escape(attribute)),
        re.MULTILINE,
    )
    block_content = content[block_start : block_end + 1]
    matches = list(pattern.finditer(block_content))

    if not matches:
        raise RemediationError(
            f"No '{attribute}' attribute was found in the targeted resource block."
        )
    if len(matches) > 1:
        raise RemediationError(
            f"{len(matches)} '{attribute}' attributes were found in the "
            f"targeted resource block; expected exactly one."
        )

    match = matches[0]
    raw_current = match.group("value")
    if not re.fullmatch(expected_pattern, raw_current):
        raise RemediationError(
            f"Existing '{attribute}' value {raw_current!r} is not in the "
            f"expected shape for this rule; refusing to guess its meaning."
        )
    current_value = current_parser(raw_current)
    _require_change_needed(current_value, new_value, f"'{attribute}'")

    match_abs_start = block_start + match.start()
    match_abs_end = block_start + match.end()
    new_line = f'{match.group("prefix")}{new_literal}'
    return content[:match_abs_start] + new_line + content[match_abs_end:]


def apply_remediation(main_tf_path, rule_id, resource, raw_restore_value):
    """Validate everything, then return the fully reconstructed file content.

    Performs, in order: rule ID whitelist check, resource binding check,
    restore-value type validation, resource block location (exactly one),
    attribute/ingress-block location (exactly one), current-value
    parsing/validation, and the current-vs-restore no-op guard. Raises
    `RemediationError` on any failure. Returns `(new_content, restore_value)`
    only when every check has passed — the caller is responsible for
    performing the actual write, so that no file mutation can happen before
    every validation step has succeeded.
    """
    rule_spec = _validate_rule_id(rule_id)
    _validate_resource(rule_id, rule_spec, resource)
    restore_value = _validate_restore_value(rule_id, rule_spec, raw_restore_value)

    with open(main_tf_path, "r") as main_tf_file:
        content = main_tf_file.read()

    resource_type, resource_name = resource.split(".", 1)
    block_start, block_end = _find_resource_block(content, resource_type, resource_name)

    kind = rule_spec["kind"]
    if kind == "ingress_cidr":
        new_content = _replace_cidr_blocks(
            content, block_start, block_end, rule_spec["port"], restore_value
        )
    elif kind == "int_attr":
        new_content = _replace_scalar_attribute(
            content,
            block_start,
            block_end,
            rule_spec["attribute"],
            r"\d+",
            int,
            str(restore_value),
            restore_value,
        )
    elif kind == "bool_attr":
        new_content = _replace_scalar_attribute(
            content,
            block_start,
            block_end,
            rule_spec["attribute"],
            r"true|false",
            lambda raw: raw == "true",
            "true" if restore_value else "false",
            restore_value,
        )
    else:  # pragma: no cover - unreachable, SUPPORTED_RULES is fixed above
        raise RemediationError(f"Unhandled rule kind {kind!r}.")

    return new_content, restore_value


def _atomic_write(path, content):
    """Write `content` to `path` atomically (temp file + rename).

    Guarantees no partially written `main.tf` can ever be observed: the
    new content is fully written to a sibling temporary file first, then
    `os.replace` atomically swaps it into place.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=".apply_remediation_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_json(path, payload):
    """Write `payload` as JSON to `path` atomically (temp file + rename).

    Mirrors `_atomic_write`'s guarantee for the Terraform file: no
    partially written result artifact can ever be observed by a reader
    that races this write.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=".apply_remediation_result_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as tmp_file:
            json.dump(payload, tmp_file)
            tmp_file.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def main(argv=None):
    args = parse_args(argv)
    main_tf_path = os.path.join(args.terraform_dir, "main.tf")

    # Path-confinement validation (Phase 8C hardening) runs FIRST, before
    # any filesystem side effect involving --result-file (including the
    # stale-artifact clear below): a --result-file argument that is not
    # strictly confined to the expected artifacts/ directory with the
    # expected filename pattern is rejected outright, fail-closed, with
    # no delete and no write ever attempted against it.
    result_file = None
    if args.result_file is not None:
        try:
            result_file = _validate_result_file_path(args.result_file, args.terraform_dir)
        except RemediationError as exc:
            print(f"apply_remediation.py: {exc}", file=sys.stderr)
            return 1

    # Stale-artifact protection (Phase 8B transport correction): if a
    # --result-file path is given, this invocation owns that path
    # completely. Any pre-existing file at that path is removed BEFORE
    # attempting the remediation, so a failure below can never leave a
    # prior, unrelated invocation's success artifact in place to be
    # mistaken for this invocation's outcome. Combined with
    # run_remediation_stage.py generating a fresh, unique per-invocation
    # path (never a fixed/reused filename), this makes the artifact
    # unambiguous: absence or staleness is structurally impossible once
    # the caller follows that contract. This only ever runs against
    # `result_file`, the already-validated, confined, resolved path --
    # never against the raw, unvalidated `args.result_file`.
    if result_file is not None:
        try:
            os.remove(result_file)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(
                f"apply_remediation.py: could not clear stale --result-file "
                f"{args.result_file!r}: {exc}",
                file=sys.stderr,
            )
            return 1

    try:
        new_content, restore_value = apply_remediation(
            main_tf_path, args.rule_id, args.resource, args.restore_value
        )
    except RemediationError as exc:
        print(f"apply_remediation.py: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"apply_remediation.py: could not read {main_tf_path!r}: {exc}",
            file=sys.stderr,
        )
        return 1

    try:
        _atomic_write(main_tf_path, new_content)
    except OSError as exc:
        print(
            f"apply_remediation.py: could not write {main_tf_path!r}: {exc}",
            file=sys.stderr,
        )
        return 1

    result_payload = {
        "status": "remediated",
        "rule_id": args.rule_id,
        "resource": args.resource,
        "restored_value": restore_value,
    }

    # The --result-file write happens ONLY after the Terraform mutation
    # has already succeeded (this line is unreachable on any failure
    # path above), targets only the already-validated, confined
    # `result_file` path (never the raw, unvalidated `args.result_file`),
    # and is itself atomic (temp file + os.replace) so a caller can never
    # observe a partially written result artifact.
    if result_file is not None:
        try:
            _atomic_write_json(result_file, result_payload)
        except OSError as exc:
            print(
                f"apply_remediation.py: main.tf was mutated successfully, "
                f"but the result file {args.result_file!r} could not be "
                f"written: {exc}",
                file=sys.stderr,
            )
            return 1

    print(json.dumps(result_payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())

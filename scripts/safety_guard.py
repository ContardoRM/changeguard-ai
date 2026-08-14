#!/usr/bin/env python3
"""ChangeGuard Safety Guard (Kiro `preToolUse` hook command).

Deterministic, defense-in-depth safety control. It is deliberately
independent of, and knows nothing about, ChangeGuard's product policy: it
contains no reference to SEC-001, SEC-002, REL-001, BR-001, severities,
`PASS`/`FAIL`/`INCOMPLETE`, `desired_count`, `deletion_protection`, ports
22/5432, `0.0.0.0/0`, or Terraform plan JSON. Its only responsibility is
command safety — deciding whether a proposed shell command is one of a
small set of prohibited destructive/out-of-scope command classes.

Architecture (see design.md "Kiro Hook / Safety Strategy" and
requirements.md Requirement 11):

    Agent decision
      -> Kiro tool permission (per-agent `permissions.rules`)
      -> pre-tool-use safety guard (this script)
      -> allowed local deterministic command

This guard supplements the per-agent `permissions.rules` restrictions
already present on every ChangeGuard agent; it does not replace them. Even
if a future prompt, tool invocation, or orchestration mistake attempted to
grant broader shell access, this guard still refuses the four prohibited
command classes.

Installed-CLI discovery (kiro-cli 2.18.0): this CLI version's `preToolUse`
hook is configured as an embedded field inside an agent's JSON config
(`hooks.preToolUse`), not as a standalone `.kiro/hooks/*.json` v1-schema
file (that mechanism belongs to a later CLI/IDE major version — see the
ChangeGuard design.md discrepancy note for detail on how this was
determined against the actually-installed CLI). Each hook entry has the
shape `{"matcher": "execute_bash", "command": "<shell command>"}`. The
command receives a single JSON object on stdin:

    {
      "hook_event_name": "preToolUse",
      "cwd": "<current working directory>",
      "tool_name": "execute_bash",
      "tool_input": {"command": "<the proposed shell command string>"}
    }

Exit code semantics for a `preToolUse` command hook (verified empirically
against the installed CLI, and consistent with Kiro's documented CLI 2.x
hook exit-code contract):

    0 -> ALLOW  (the tool call proceeds; stdout, if any, is added to the
                 agent's context)
    2 -> DENY   (the tool call is blocked before it ever executes; stderr
                 is surfaced to the agent as the denial reason)

Any other non-zero exit code is *not* guaranteed to block the tool call in
this CLI version (empirically, exit code 1 does not block) — this script
therefore always exits with exactly 0 or 2, never any other code, so its
allow/deny behavior is unambiguous.

Fail-closed behavior: if stdin cannot be read, is not valid JSON, or does
not contain a `tool_input.command` string, this script denies (exit 2)
rather than allowing an un-inspectable invocation through.
"""

import json
import shlex
import sys


# Prohibited command classes (Requirement 11.1-11.4). Each entry is a
# plain-language label plus a predicate function that receives the
# lower-cased, shell-tokenized command (a list of argv-like tokens, with
# shell control operators such as ';', '&&', '||', '|' as their own
# tokens) and returns True if that token sequence matches the prohibited
# pattern. Matching is deliberately structural (token-based), not a raw
# substring search, so that trivial whitespace/quoting differences do not
# change the result, while still not attempting to be a complete shell
# grammar parser.


def _contains_subsequence(tokens, subsequence):
    """True if `subsequence` appears contiguously anywhere in `tokens`."""
    n = len(subsequence)
    if n == 0 or n > len(tokens):
        return False
    for start in range(len(tokens) - n + 1):
        if tokens[start : start + n] == subsequence:
            return True
    return False


def _is_terraform_apply(tokens):
    return _contains_subsequence(tokens, ["terraform", "apply"])


def _is_terraform_destroy(tokens):
    return _contains_subsequence(tokens, ["terraform", "destroy"])


def _is_aws_cli_invocation(tokens):
    """Matches a bare `aws ...` invocation, or one invoked via an
    absolute/relative path ending in `/aws` (e.g. `/usr/local/bin/aws`),
    as its own command token (not merely a substring of some other word,
    e.g. this must not match `awssomething` or `saws`)."""
    for token in tokens:
        if token == "aws":
            return True
        if token.endswith("/aws"):
            return True
    return False


# Every accepted spelling of "recursive, forced, unattended delete" that
# `rm` accepts, expressed as the exact flag-token combinations Requirement
# 11.4 enumerates. Both short-flag orderings (`-rf`, `-fr`) and both
# long-flag orderings (`--recursive --force`, `--force --recursive`) are
# covered, plus the combined single-token long-flag spellings.
_RM_COMBINED_SHORT_FLAGS = {"-rf", "-fr"}
_RM_LONG_FLAGS = {"--recursive", "-r", "-R"}
_RM_FORCE_FLAGS = {"--force", "-f"}


def _is_destructive_rm(tokens):
    """True if `tokens` contains an `rm` invocation whose flags request
    both recursive and forced deletion, in any flag ordering or spelling
    covered by Requirement 11.4's enumerated forms."""
    for index, token in enumerate(tokens):
        if token != "rm":
            continue
        # Look at the flag tokens that follow this `rm` token, up to the
        # next shell control operator (so `rm -rf x; echo done` doesn't
        # let a later, unrelated token influence this rm's own flags).
        flags = []
        for later in tokens[index + 1 :]:
            if later in (";", "&&", "||", "|"):
                break
            if later.startswith("-"):
                flags.append(later)

        if any(flag in _RM_COMBINED_SHORT_FLAGS for flag in flags):
            return True

        has_recursive = any(flag in _RM_LONG_FLAGS for flag in flags)
        has_force = any(flag in _RM_FORCE_FLAGS for flag in flags)
        if has_recursive and has_force:
            return True

    return False


# Ordered list of (label, predicate) pairs. Order only affects which
# `reason` string is reported first when a command matches more than one
# class; it does not affect whether the command is denied.
_PROHIBITED_CLASSES = [
    ("terraform apply", _is_terraform_apply),
    ("terraform destroy", _is_terraform_destroy),
    ("AWS CLI invocation", _is_aws_cli_invocation),
    ("destructive recursive-force filesystem deletion (rm)", _is_destructive_rm),
]


def tokenize_command(command_text):
    """Tokenize a shell command string into lower-cased tokens.

    Shell control operators (`;`, `&&`, `||`, `|`) are preserved as their
    own tokens via `shlex`'s `punctuation_chars` support, so a prohibited
    command hidden after a benign one in a compound command is still
    visible to the matchers above (e.g. `echo hi; terraform destroy`
    tokenizes with `terraform` and `destroy` as ordinary tokens, which the
    subsequence matchers above find regardless of what precedes them).

    Raises `ValueError` if `command_text` cannot be tokenized at all
    (e.g. unbalanced quoting) — callers must treat this as fail-closed
    (deny), never as an all-clear.
    """
    lexer = shlex.shlex(command_text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    return [token.lower() for token in tokens]


def evaluate_command(command_text):
    """Return `(allowed, reason)` for a proposed shell command string.

    `allowed` is `True` only if none of the prohibited command classes
    match. `reason` is `None` when `allowed` is `True`, and a concise,
    human-readable denial message identifying the matched class when
    `allowed` is `False`.

    This function performs no ChangeGuard product-policy evaluation of
    any kind — it never inspects Terraform plan JSON, and it has no
    concept of a rule ID, severity, or verdict. It answers exactly one
    question: does this command text structurally match a prohibited
    command class?
    """
    try:
        tokens = tokenize_command(command_text)
    except ValueError as exc:
        return False, f"command could not be safely parsed for inspection: {exc}"

    for label, predicate in _PROHIBITED_CLASSES:
        if predicate(tokens):
            return False, f"command matches prohibited class: {label}"

    return True, None


def evaluate_hook_input(raw_stdin_text):
    """Return `(allowed, reason)` for the raw JSON text of a `preToolUse`
    hook payload.

    Fails closed (denies) if `raw_stdin_text` is not valid JSON, is not a
    JSON object, or does not contain a `tool_input.command` string — an
    un-inspectable invocation is never treated as safe.
    """
    try:
        payload = json.loads(raw_stdin_text)
    except json.JSONDecodeError as exc:
        return False, f"hook input was not valid JSON: {exc}"

    if not isinstance(payload, dict):
        return False, "hook input was not a JSON object"

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return False, "hook input has no usable 'tool_input' object"

    command_text = tool_input.get("command")
    if not isinstance(command_text, str):
        return False, "hook input has no usable 'tool_input.command' string"

    return evaluate_command(command_text)


def main(argv=None):
    del argv  # This hook command takes no CLI arguments; input is stdin-only.

    try:
        raw_stdin_text = sys.stdin.read()
    except Exception as exc:  # noqa: BLE001 - fail closed on any read error
        print(f"safety_guard.py: could not read hook input: {exc}", file=sys.stderr)
        return 2

    allowed, reason = evaluate_hook_input(raw_stdin_text)
    if allowed:
        return 0

    print(f"safety_guard.py: DENIED - {reason}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

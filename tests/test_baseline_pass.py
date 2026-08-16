#!/usr/bin/env python3
"""Safe-baseline PASS test (Requirements 12.2, 12.10).

This module verifies that, when the Security Reviewer and the Reliability
Reviewer are each given the safe baseline plan compared against an
identically safe candidate plan (`tests/fixtures/baseline_plan.json` vs.
`tests/fixtures/candidate_safe.json` — no supported rule transition present),
both reviewers return `status: PASS` with an empty `findings` list.

Why this test invokes a live `kiro-cli` agent, not a plain Python function:
per design.md's "Security Reviewer" / "Reliability Reviewer" component
sections and Requirements 5.10/5.11/6.10/6.11, the rule-satisfaction
judgment step (the decision that produces `PASS`/`FAIL`/`INCOMPLETE`) is
implemented entirely in each agent's own LLM system prompt
(`.kiro/agents/security-reviewer-prompt.md` and
`.kiro/agents/reliability-reviewer-prompt.md`), driven by the agent
definitions at `.kiro/agents/security-reviewer.json` and
`.kiro/agents/reliability-reviewer.json`. Unlike the deterministic
evidence-extraction modules (`scripts/security_rules.py`,
`scripts/reliability_rules.py`, exercised directly by
`tests/test_security_rules.py` and `tests/test_reliability_rules.py`), there
is no plain, directly-importable Python function anywhere in this repository
that returns a `ReviewResult`/`PASS`/`FAIL`/`INCOMPLETE` verdict — that
judgment is deliberately kept inside the agent's free-form LLM behavior
(see `scripts/security_rules.py`'s and `scripts/reliability_rules.py`'s
module docstrings: "Judging whether an extracted fact pattern is acceptable
is the sole responsibility of a downstream policy-owning component that is
not implemented in this module"). Asserting a live `PASS` verdict for this
task therefore requires invoking each reviewer via
`kiro-cli chat --agent <name> --no-interactive`, exactly as
`.kiro/agents/security-reviewer.json` and
`.kiro/agents/reliability-reviewer.json` were themselves verified during
Tasks 8.1/8.2.

This is consistent with how the task instructions describe Tasks 4.3/5.3
handling reviewer-judgment assertions (invoking the judgment logic from
Tasks 8.1/8.2 rather than only the evidence-extraction modules); at the time
this module was written, `tests/test_security_reviewer.py` and
`tests/test_reliability_reviewer.py` had not yet landed from their parallel
tasks, so this module makes its own, self-contained live-agent invocation
rather than importing a shared helper from either of those modules.

Because a real `kiro-cli` binary and a real (metered) LLM call are required,
this module is guarded with `unittest.skipUnless(shutil.which("kiro-cli"), ...)`
so the suite degrades gracefully — consistent with
`tests/test_remediation_script.py`'s `shutil.which("terraform")` guard for
its own external-binary dependency — in any environment where `kiro-cli` is
not installed or not practical to invoke (e.g. a CI runner with no LLM
credentials configured).

Uses only the Python 3 standard library `unittest` module for the test
framework itself; `kiro-cli` is an external CLI dependency of the product
under test (the Kiro Crew agent runtime), not a Python test dependency.
"""

import functools
import json
import os
import re
import shutil
import subprocess
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

# Paths as seen by the agent's shell tool, whose commands run with
# `cwd=REPO_ROOT` (matching the `python3 scripts/print_*_evidence.py *`
# permission rules in `.kiro/agents/security-reviewer.json` /
# `reliability-reviewer.json`, which are relative to the repo root).
BASELINE_REL = "tests/fixtures/baseline_plan.json"
CANDIDATE_SAFE_REL = "tests/fixtures/candidate_safe.json"

# `CHANGEGUARD_SKIP_LIVE_TESTS=1` (set by `make test`, the fast/deterministic
# default) forces these live-agent tests to skip even when `kiro-cli` is on
# PATH, so the fast suite never makes a real, credit-consuming LLM call.
# `make test-live` runs without that env var set, so kiro-cli's real
# presence/absence is what gates these tests there.
KIRO_CLI = None if os.environ.get("CHANGEGUARD_SKIP_LIVE_TESTS") else shutil.which("kiro-cli")

_INVOCATION_TIMEOUT_SECONDS = 120


@functools.lru_cache(maxsize=None)
def _invoke_reviewer(agent_name, baseline_rel_path, candidate_rel_path):
    """Invoke a live ChangeGuard reviewer agent for one comparison cycle.

    Returns the completed `subprocess.CompletedProcess`. Raises
    `unittest.SkipTest` (via the caller's assertions) is not done here;
    callers are expected to assert on `returncode`/`stdout` themselves so
    a genuine agent failure is reported as a test failure, not a skip.

    Cached (per unique `(agent_name, baseline_rel_path, candidate_rel_path)`
    tuple, for the lifetime of the test process) via `functools.lru_cache`
    purely to avoid redundant live subprocess invocations if this helper is
    ever called more than once with the same arguments - the underlying
    agent invocation itself is always genuine and is never stubbed or
    faked.
    """
    prompt = (
        "Evaluate this comparison cycle. "
        f"Baseline plan path: {baseline_rel_path}. "
        f"Candidate plan path: {candidate_rel_path}."
    )
    return subprocess.run(
        [KIRO_CLI, "chat", "--agent", agent_name, "--no-interactive", prompt],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=_INVOCATION_TIMEOUT_SECONDS,
    )


def _extract_review_result(stdout_text):
    """Extract the final `ReviewResult` JSON object from agent stdout.

    Each reviewer's prompt contract requires the *final* message to be a
    single JSON object of the shape `{"agent": ..., "status": ..., ...}`,
    but a reviewer may also emit private reasoning/tool-output text before
    that final message (as observed for the Reliability Reviewer). This
    locates the last occurrence of a JSON object starting with `"agent"`
    and decodes exactly that object, ignoring any surrounding narration.
    """
    matches = list(re.finditer(r'\{\s*"agent"', stdout_text))
    if not matches:
        raise AssertionError(
            "no JSON ReviewResult object (starting with '{\"agent\"') found "
            f"in reviewer output:\n{stdout_text}"
        )
    start = matches[-1].start()
    decoder = json.JSONDecoder()
    review_result, _ = decoder.raw_decode(stdout_text, start)
    return review_result


@unittest.skipUnless(
    KIRO_CLI,
    "kiro-cli not found on PATH; skipping live-agent judgment tests. The "
    "Security Reviewer's and Reliability Reviewer's PASS/FAIL/INCOMPLETE "
    "judgment logic lives entirely in each agent's LLM prompt "
    "(.kiro/agents/security-reviewer-prompt.md and "
    ".kiro/agents/reliability-reviewer-prompt.md), not in a plain "
    "importable Python function, so asserting a live verdict requires "
    "invoking `kiro-cli chat --agent <name> --no-interactive`.",
)
class SafeBaselinePassTests(unittest.TestCase):
    """A safe baseline vs. safe candidate comparison PASSes both reviewers."""

    def setUp(self):
        for rel_path in (BASELINE_REL, CANDIDATE_SAFE_REL):
            abs_path = os.path.join(REPO_ROOT, rel_path)
            self.assertTrue(
                os.path.isfile(abs_path),
                msg=f"expected fixture file to exist: {abs_path!r}",
            )

    def test_security_reviewer_passes_on_safe_baseline(self):
        result = _invoke_reviewer(
            "security-reviewer", BASELINE_REL, CANDIDATE_SAFE_REL
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"security-reviewer invocation failed:\n{result.stderr}",
        )
        review_result = _extract_review_result(result.stdout)
        self.assertEqual(review_result.get("status"), "PASS")
        self.assertEqual(review_result.get("findings"), [])

    def test_reliability_reviewer_passes_on_safe_baseline(self):
        result = _invoke_reviewer(
            "reliability-reviewer", BASELINE_REL, CANDIDATE_SAFE_REL
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"reliability-reviewer invocation failed:\n{result.stderr}",
        )
        review_result = _extract_review_result(result.stdout)
        self.assertEqual(review_result.get("status"), "PASS")
        self.assertEqual(review_result.get("findings"), [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for scripts/run_agent_and_save.py's agent allow-list.

Uses only the Python 3 standard library `unittest` module. Never invokes
a real `kiro-cli` subprocess for the rejection case (the allow-list check
happens before any subprocess.run call).
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import run_agent_and_save  # noqa: E402


class AgentAllowListTestCase(unittest.TestCase):
    def test_unsupported_agent_rejected_before_subprocess(self):
        with mock.patch("subprocess.run") as run_mock:
            exit_code = run_agent_and_save.main(
                ["--agent", "not-a-real-agent", "--prompt", "hi", "--output", "/tmp/should_not_be_written.json"]
            )
        self.assertEqual(exit_code, 1)
        run_mock.assert_not_called()

    def test_allowed_agents_are_exactly_the_three_reviewers_and_remediator(self):
        self.assertEqual(
            run_agent_and_save.ALLOWED_AGENTS,
            frozenset({"security-reviewer", "reliability-reviewer", "remediator"}),
        )


if __name__ == "__main__":
    unittest.main()

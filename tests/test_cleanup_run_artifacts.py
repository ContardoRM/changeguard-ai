#!/usr/bin/env python3
"""Unit tests for scripts/cleanup_run_artifacts.py.

Covers the Phase 8B artifact hygiene requirement: cleanup must preserve
artifacts/baseline-plan.json and remove only the explicit run-specific
allow-list, using os.remove only (never a shell rm, never recursive
deletion). Uses only the Python 3 standard library `unittest` module.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scripts import cleanup_run_artifacts  # noqa: E402


class CleanupRunArtifactsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="cleanup_run_artifacts_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _touch(self, name):
        path = os.path.join(self.tmp_dir, name)
        with open(path, "w") as f:
            f.write("{}")
        return path

    def test_preserves_baseline_plan(self):
        baseline_path = self._touch("baseline-plan.json")
        self._touch("candidate-plan.json")

        cleanup_run_artifacts.cleanup_run_artifacts(self.tmp_dir)

        self.assertTrue(os.path.isfile(baseline_path), "baseline-plan.json must never be removed")

    def test_removes_only_the_explicit_allow_list(self):
        for name in cleanup_run_artifacts.RUN_SPECIFIC_ARTIFACT_NAMES:
            self._touch(name)
        baseline_path = self._touch("baseline-plan.json")
        unrelated_path = self._touch("some-unrelated-file.json")

        removed = cleanup_run_artifacts.cleanup_run_artifacts(self.tmp_dir)

        self.assertEqual(len(removed), len(cleanup_run_artifacts.RUN_SPECIFIC_ARTIFACT_NAMES))
        for name in cleanup_run_artifacts.RUN_SPECIFIC_ARTIFACT_NAMES:
            self.assertFalse(os.path.isfile(os.path.join(self.tmp_dir, name)))
        self.assertTrue(os.path.isfile(baseline_path))
        self.assertTrue(os.path.isfile(unrelated_path), "cleanup must not touch files outside the explicit allow-list")

    def test_absence_of_files_is_not_an_error(self):
        # Nothing exists in self.tmp_dir at all.
        removed = cleanup_run_artifacts.cleanup_run_artifacts(self.tmp_dir)
        self.assertEqual(removed, [])

    def test_allow_list_never_contains_baseline_plan(self):
        self.assertNotIn(
            cleanup_run_artifacts.PRESERVED_ARTIFACT_NAME,
            cleanup_run_artifacts.RUN_SPECIFIC_ARTIFACT_NAMES,
        )


if __name__ == "__main__":
    unittest.main()

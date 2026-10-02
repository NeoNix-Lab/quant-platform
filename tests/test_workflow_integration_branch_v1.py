"""Integration-branch detection and the governance check in tools/workflow.py."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import workflow  # noqa: E402

BRANCH_LISTING = """\
  agent/issue-239-safety-tooling-hygiene
* agent/issue-255-workflow-integration-branch-detection
  chore/implement/not-an-integration-branch
  governance/wave-7-scope
+ implement/omega
  implement/wave-7
  main
  remotes/origin/HEAD -> origin/main
  remotes/origin/agent/issue-1-implement/x
  remotes/origin/implement/omega
  remotes/origin/implement/wave-5
  remotes/origin/main
"""


class IntegrationBranchSelectionTest(unittest.TestCase):
    def test_parse_recognizes_any_implement_branch_local_or_remote(self):
        self.assertEqual(
            workflow.parse_integration_branches(BRANCH_LISTING),
            ["implement/omega", "implement/wave-5", "implement/wave-7"],
        )

    def test_parse_ignores_issue_governance_and_symbolic_refs(self):
        listing = "  agent/issue-1-x\n  governance/y\n  remotes/origin/HEAD -> origin/main\n  main\n"
        self.assertEqual(workflow.parse_integration_branches(listing), [])

    def test_closest_by_ancestry_beats_the_highest_wave_number(self):
        chosen = workflow.choose_integration_branch(
            ["implement/omega", "implement/wave-7"],
            {"implement/omega": 1, "implement/wave-7": 40},
            {"implement/omega": 100, "implement/wave-7": 50},
        )
        self.assertEqual(chosen, "implement/omega")

    def test_equal_distance_prefers_the_most_recently_updated_tip(self):
        chosen = workflow.choose_integration_branch(
            ["implement/wave-7", "implement/omega"],
            {"implement/omega": 2, "implement/wave-7": 2},
            {"implement/omega": 200, "implement/wave-7": 100},
        )
        self.assertEqual(chosen, "implement/omega")

    def test_equal_distance_and_recency_fall_back_to_the_name(self):
        chosen = workflow.choose_integration_branch(
            ["implement/b", "implement/a"],
            {"implement/a": 0, "implement/b": 0},
            {"implement/a": 5, "implement/b": 5},
        )
        self.assertEqual(chosen, "implement/a")

    def test_branch_without_merge_base_ranks_last(self):
        chosen = workflow.choose_integration_branch(
            ["implement/new", "implement/old"],
            {"implement/new": None, "implement/old": 30},
            {"implement/new": 999, "implement/old": 1},
        )
        self.assertEqual(chosen, "implement/old")

    def test_no_candidate_means_main(self):
        self.assertEqual(workflow.choose_integration_branch([], {}, {}), "main")


class GovernanceCheckInRepositoryTest(unittest.TestCase):
    """A stale, higher-numbered wave branch must not mask the real base."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self._original_root = workflow.REPO_ROOT
        workflow.REPO_ROOT = self.repo
        self.addCleanup(setattr, workflow, "REPO_ROOT", self._original_root)

        self._git("init", "-q", "-b", "main")
        self._commit("SCOPE.md", "v1\n", "base", 1_000_000_000)
        self._git("branch", "implement/wave-7")
        # Governance change that lands on main after wave-7 was cut.
        self._commit("SCOPE.md", "v2\n", "governance lands on main", 1_000_001_000)
        self._git("branch", "implement/omega")
        self._git("checkout", "-q", "-b", "agent/issue-9-slice", "implement/omega")
        self._commit("src_module.py", "x = 1\n", "slice work", 1_000_002_000)

    def _git(self, *args):
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True, env=env)

    def _commit(self, name, text, message, timestamp):
        (self.repo / name).write_text(text, encoding="utf-8")
        self._git("add", name)
        stamp = f"{timestamp} +0000"
        env = {
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
            "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp,
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "PATH": os.environ["PATH"],
        }
        subprocess.run(
            ["git", "commit", "-q", "-m", message], cwd=self.repo, check=True,
            capture_output=True, env=env,
        )

    def test_detects_the_branch_the_issue_branch_was_cut_from(self):
        self.assertEqual(workflow.detect_active_integration_branch(), "implement/omega")

    def test_slice_touching_no_governance_file_passes(self):
        self.assertEqual(workflow.governance_violations("agent/issue-9-slice"), set())

    def test_slice_touching_a_governance_file_is_still_reported(self):
        self._commit("SCOPE.md", "v3\n", "illegal governance edit", 1_000_003_000)
        self.assertEqual(workflow.governance_violations("agent/issue-9-slice"), {"SCOPE.md"})

    def test_explicit_base_is_honored(self):
        # Against the stale base the governance commit from main shows up,
        # which is exactly what the old auto-detection misreported.
        self.assertEqual(
            workflow.governance_violations("agent/issue-9-slice", "implement/wave-7"),
            {"SCOPE.md"},
        )
        self.assertEqual(
            workflow.governance_violations("agent/issue-9-slice", "implement/omega"),
            set(),
        )

    def test_stale_local_base_does_not_mask_a_fresher_remote_ref(self):
        # A local integration branch that was never fast-forwarded must not
        # make every governance change that landed since look like this
        # branch's own change; origin/<name> is fresher and must be used.
        fresh = subprocess.run(
            ["git", "rev-parse", "implement/omega"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        stale = subprocess.run(
            ["git", "rev-parse", "implement/wave-7"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        self._git("update-ref", "refs/remotes/origin/implement/omega", fresh)
        self._git("branch", "-f", "implement/omega", stale)
        self.assertEqual(workflow.resolve_ref("implement/omega"), "origin/implement/omega")
        self.assertEqual(workflow.governance_violations("agent/issue-9-slice"), set())

    def test_remote_only_base_is_resolved(self):
        fresh = subprocess.run(
            ["git", "rev-parse", "implement/omega"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        self._git("update-ref", "refs/remotes/origin/implement/omega", fresh)
        self._git("branch", "-D", "implement/omega")
        self.assertEqual(workflow.resolve_ref("implement/omega"), "origin/implement/omega")
        self.assertEqual(workflow.governance_violations("agent/issue-9-slice", "implement/omega"), set())

    def test_unknown_branch_does_not_resolve(self):
        self.assertIsNone(workflow.resolve_ref("implement/does-not-exist"))

    def test_without_any_integration_branch_the_base_is_main(self):
        self._git("checkout", "-q", "main")
        self._git("branch", "-D", "agent/issue-9-slice", "implement/omega", "implement/wave-7")
        self.assertEqual(workflow.detect_active_integration_branch(), "main")


if __name__ == "__main__":
    unittest.main()

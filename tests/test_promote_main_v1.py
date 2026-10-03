#!/usr/bin/env python3
"""infra/promote-main.sh validates the candidate before advancing (#241).

Builds a disposable, fully isolated bare-origin + server-checkout pair (never
touches this repository's own history) and drives the real script end to end:
a failing candidate must be rejected without moving the operational checkout;
a passing candidate must be fast-forwarded onto.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMOTE_SCRIPT = ROOT / "infra" / "promote-main.sh"
RUN_TESTS_SCRIPT = ROOT / "tools" / "run_tests.py"
REPO_IDENTITY_SCRIPT = ROOT / "tools" / "repo_identity.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "promote-main-test",
    "GIT_AUTHOR_EMAIL": "promote-main-test@example.invalid",
    "GIT_COMMITTER_NAME": "promote-main-test",
    "GIT_COMMITTER_EMAIL": "promote-main-test@example.invalid",
}


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise unittest.SkipTest(f"{tool} is not available on PATH")


def _to_bash_path(path: Path) -> str:
    """Convert a native path to the POSIX form Git Bash/MSYS expects as an
    argv element. Found in review: MSYS's own path handling for bash's *own*
    script argument (as opposed to a path embedded inside a quoted command
    string, which does translate) strips backslashes outright on at least one
    real Windows checkout -- "D:\\Documents\\...\\x.sh" became the literal,
    nonexistent "D:Documents...x.sh". Passing an explicit POSIX-style path
    sidesteps that translation entirely rather than depending on it."""
    text = str(path)
    if len(text) >= 2 and text[1] == ":":
        drive = text[0].lower()
        rest = text[2:].replace("\\", "/")
        return f"/{drive}{rest}"
    return text.replace("\\", "/")


def _bash(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", *args], cwd=str(cwd), env=env, capture_output=True, text=True)


def _git(args: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def _python3_shim_dir() -> Path:
    """A `python3` on PATH that forwards to this interpreter.

    The real target environment (Linux CI, Linux server) already has a
    working `python3`; this shim only matters on a dev machine where `python3`
    is not aliased to a real interpreter (e.g. the Windows Store stub) -- it
    is harmless and redundant everywhere else since it resolves to the same
    interpreter `python3` would anyway.
    """
    shim_dir = Path(tempfile.mkdtemp())
    shim = shim_dir / "python3"
    shim.write_text(f'#!/usr/bin/env bash\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | 0o111)
    return shim_dir


def _write_candidate_commit(repo: Path, *, tests_pass: bool, message: str, env: dict[str, str]) -> str:
    tools_dir = repo / "tools"
    tests_dir = repo / "tests"
    tools_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RUN_TESTS_SCRIPT, tools_dir / "run_tests.py")
    shutil.copyfile(REPO_IDENTITY_SCRIPT, tools_dir / "repo_identity.py")
    exit_code = 0 if tests_pass else 1
    (tests_dir / "test_marker.py").write_text(
        f"# {message}\nimport sys\nsys.exit({exit_code})\n", encoding="utf-8"
    )
    _git(["add", "-A"], cwd=repo, env=env)
    _git(["commit", "-m", message], cwd=repo, env=env)
    return _git(["rev-parse", "HEAD"], cwd=repo, env=env).stdout.strip()


def _write_candidate_commit_that_pushes_a_sneaky_commit_during_validation(
    repo: Path, *, origin_bare: Path, server_checkout: Path, message: str, env: dict[str, str]
) -> str:
    """A candidate whose own validation step pushes a second, unvalidated
    commit to origin/main *and* fetches it into the server checkout's own
    local refs/remotes/origin/main while validation runs -- simulating
    another process/hook updating that local ref between this script's own
    fetch and its final merge. Pushing to the bare origin alone is not
    enough: the server checkout's local origin/main ref only moves when
    something fetches into it, so the test must do that explicitly to
    actually exercise the race. Review-caught: the merge must stay pinned
    to the OID that was actually validated, never re-resolve the mutable
    local ref at merge time."""
    tools_dir = repo / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "run_tests.py").write_text(
        "import subprocess, sys, tempfile\n"
        "sneaky = tempfile.mkdtemp()\n"
        f"subprocess.run(['git', 'clone', '-q', {str(origin_bare)!r}, sneaky], check=True)\n"
        "subprocess.run(['git', '-C', sneaky, 'config', 'user.email', 'sneaky@example.invalid'], check=True)\n"
        "subprocess.run(['git', '-C', sneaky, 'config', 'user.name', 'sneaky'], check=True)\n"
        "open(sneaky + '/sneaky.txt', 'w').write('unvalidated concurrent push')\n"
        "subprocess.run(['git', '-C', sneaky, 'add', '-A'], check=True)\n"
        "subprocess.run(['git', '-C', sneaky, 'commit', '-q', '-m', 'sneaky concurrent push'], check=True)\n"
        "subprocess.run(['git', '-C', sneaky, 'push', '-q', 'origin', 'main'], check=True)\n"
        # This is the part that makes the race real: advance the *server
        # checkout's own* local origin/main ref, as a concurrent hook/fetch
        # would, so a merge that re-resolved origin/main at this point would
        # pick up the unvalidated sneaky commit instead of the validated one.
        f"subprocess.run(['git', '-C', {str(server_checkout)!r}, 'fetch', 'origin'], check=True)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    shutil.copyfile(REPO_IDENTITY_SCRIPT, tools_dir / "repo_identity.py")
    _git(["add", "-A"], cwd=repo, env=env)
    _git(["commit", "-m", message], cwd=repo, env=env)
    return _git(["rev-parse", "HEAD"], cwd=repo, env=env).stdout.strip()


class PromoteMainValidatesBeforeAdvancingTests(unittest.TestCase):
    def setUp(self):
        _require("bash")
        _require("git")
        self.holder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.holder, ignore_errors=True)
        self.shim_dir = _python3_shim_dir()
        self.addCleanup(shutil.rmtree, self.shim_dir, ignore_errors=True)

        self.env = dict(os.environ)
        self.env.update(GIT_ENV)
        self.env["PATH"] = str(self.shim_dir) + os.pathsep + self.env.get("PATH", "")

        self.author_repo = self.holder / "author"
        self.author_repo.mkdir()
        _git(["init", "-q"], cwd=self.author_repo, env=self.env)
        self.initial_sha = _write_candidate_commit(
            self.author_repo, tests_pass=True, message="initial", env=self.env
        )
        _git(["branch", "-M", "main"], cwd=self.author_repo, env=self.env)

        self.origin_bare = self.holder / "origin.git"
        _git(["clone", "-q", "--bare", str(self.author_repo), str(self.origin_bare)], cwd=self.holder, env=self.env)

        self.server_checkout = self.holder / "server"
        _git(["clone", "-q", str(self.origin_bare), str(self.server_checkout)], cwd=self.holder, env=self.env)
        _git(["checkout", "-q", "main"], cwd=self.server_checkout, env=self.env)

        root_result = _bash(["-c", "git rev-parse --show-toplevel"], cwd=self.server_checkout, env=self.env)
        self.quant_platform_repo = root_result.stdout.strip()
        origin_result = _bash(["-c", "git remote get-url origin"], cwd=self.server_checkout, env=self.env)
        self.quant_platform_origin = origin_result.stdout.strip()

        self.env["QUANT_PLATFORM_REPO"] = self.quant_platform_repo
        self.env["QUANT_PLATFORM_ORIGIN"] = self.quant_platform_origin

    def _current_sha(self) -> str:
        return _git(["rev-parse", "HEAD"], cwd=self.server_checkout, env=self.env).stdout.strip()

    def _run_promote(self) -> subprocess.CompletedProcess:
        return _bash([_to_bash_path(PROMOTE_SCRIPT)], cwd=self.server_checkout, env=self.env)

    def test_failing_candidate_is_rejected_without_moving_the_checkout(self):
        failing_sha = _write_candidate_commit(
            self.author_repo, tests_pass=False, message="breaks tests", env=self.env
        )
        _git(["push", "-q", str(self.origin_bare), "main"], cwd=self.author_repo, env=self.env)

        result = self._run_promote()

        self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            self.initial_sha,
            self._current_sha(),
            "operational checkout must not move when candidate validation fails",
        )
        self.assertIn("operational checkout was NOT moved", result.stdout + result.stderr)
        self.assertIn(self.initial_sha, result.stdout + result.stderr)
        self.assertIn(failing_sha, result.stdout + result.stderr)

    def test_passing_candidate_is_validated_then_fast_forwarded(self):
        passing_sha = _write_candidate_commit(
            self.author_repo, tests_pass=True, message="adds a feature", env=self.env
        )
        _git(["push", "-q", str(self.origin_bare), "main"], cwd=self.author_repo, env=self.env)

        result = self._run_promote()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(passing_sha, self._current_sha())
        self.assertIn("validating_candidate", result.stdout)
        self.assertIn("post_promotion_identity", result.stdout)

    def test_merge_stays_pinned_to_the_validated_oid_despite_a_concurrent_push(self):
        """Review-caught race: origin/main is a mutable local ref. If the
        final merge re-resolved it instead of merging the already-validated
        OID, a commit pushed by another process during validation (never
        itself validated) could get promoted. The candidate's own validation
        step here pushes exactly such a commit to prove the fix holds."""
        validated_sha = _write_candidate_commit_that_pushes_a_sneaky_commit_during_validation(
            self.author_repo,
            origin_bare=self.origin_bare,
            server_checkout=self.server_checkout,
            message="validated candidate",
            env=self.env,
        )
        _git(["push", "-q", str(self.origin_bare), "main"], cwd=self.author_repo, env=self.env)

        result = self._run_promote()

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            validated_sha,
            self._current_sha(),
            "the checkout must advance to the OID that was actually validated, "
            "not to whatever origin/main points to by the time the merge runs",
        )
        sneaky_log = _git(["log", "--all", "--oneline"], cwd=self.origin_bare, env=self.env).stdout
        self.assertIn(
            "sneaky concurrent push",
            sneaky_log,
            "the concurrent push must have actually happened, or this test proves nothing",
        )
        local_origin_main = _git(
            ["rev-parse", "origin/main"], cwd=self.server_checkout, env=self.env
        ).stdout.strip()
        self.assertNotEqual(
            validated_sha,
            local_origin_main,
            "the server checkout's own local origin/main ref must have actually "
            "moved past the validated OID during validation, or this test is not "
            "exercising the race at all",
        )

    def test_failed_validation_leaves_no_leftover_worktree(self):
        _write_candidate_commit(self.author_repo, tests_pass=False, message="breaks tests", env=self.env)
        _git(["push", "-q", str(self.origin_bare), "main"], cwd=self.author_repo, env=self.env)

        self._run_promote()

        worktree_list = _git(["worktree", "list"], cwd=self.server_checkout, env=self.env).stdout
        self.assertEqual(1, len(worktree_list.strip().splitlines()), worktree_list)


if __name__ == "__main__":
    unittest.main()

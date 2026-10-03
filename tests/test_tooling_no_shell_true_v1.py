#!/usr/bin/env python3
"""Local workflow tooling never uses shell=True (#240, materialized from #232 H1)."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import check_pre_push  # noqa: E402
import workflow  # noqa: E402


class NoShellTrueStaticTests(unittest.TestCase):
    def test_check_pre_push_source_has_no_shell_true(self):
        source = Path(check_pre_push.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)

    def test_workflow_source_has_no_shell_true(self):
        source = Path(workflow.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)


class RunCommandFunctionalTests(unittest.TestCase):
    def test_run_command_executes_an_argv_list_and_captures_output(self):
        code, out, err = check_pre_push.run_command(["git", "--version"])
        self.assertEqual(0, code)
        self.assertIn("git version", out)
        self.assertEqual("", err)

    def test_run_command_does_not_shell_interpret_embedded_metacharacters(self):
        """The exact injection shape the string+shell=True version was exposed
        to: a value embedding a shell command separator. With no shell
        involved, the whole value is one literal argv element git rejects as
        an invalid ref -- the embedded command is never executed."""
        with tempfile.TemporaryDirectory() as holder:
            marker = Path(holder) / "pwned_marker"
            malicious_ref = f"HEAD; echo injected > {marker}"

            code, out, err = check_pre_push.run_command(["git", "merge-base", malicious_ref, "HEAD"])

            self.assertNotEqual(0, code)
            self.assertFalse(marker.exists(), "embedded shell command must never execute")


class RunCmdFunctionalTests(unittest.TestCase):
    def test_run_cmd_executes_an_argv_list_and_captures_output(self):
        result = workflow.run_cmd(["git", "--version"])
        self.assertEqual(0, result.returncode)
        self.assertIn("git version", result.stdout)

    def test_run_cmd_does_not_shell_interpret_embedded_metacharacters(self):
        with tempfile.TemporaryDirectory() as holder:
            marker = Path(holder) / "pwned_marker"
            malicious_ref = f"HEAD; echo injected > {marker}"

            result = workflow.run_cmd(["git", "merge-base", malicious_ref, "HEAD"])

            self.assertNotEqual(0, result.returncode)
            self.assertFalse(marker.exists(), "embedded shell command must never execute")

    def test_run_cmd_only_accepts_argv_lists_not_strings(self):
        """The signature itself forces callers to keep passing lists --
        there is no string branch left to silently reintroduce shell=True."""
        import inspect

        signature = inspect.signature(workflow.run_cmd)
        self.assertEqual("list[str]", signature.parameters["cmd"].annotation)


if __name__ == "__main__":
    unittest.main()

"""Keep tests/requirements.txt in sync with the dependencies declared in pyproject.toml."""

from __future__ import annotations

from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parent.parent


def _load_pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _requirements_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


class DevDependencyMetadataTest(unittest.TestCase):
    def test_test_requirements_mirror_runtime_dependencies_plus_test_extra(self):
        project = _load_pyproject()["project"]
        declared = set(project["dependencies"]) | set(
            project["optional-dependencies"]["test"]
        )
        mirrored = _requirements_lines(ROOT / "tests" / "requirements.txt")
        self.assertEqual(len(mirrored), len(set(mirrored)), "duplicate requirement line")
        self.assertEqual(set(mirrored), declared)

    def test_test_extra_declares_the_dependencies_that_used_to_live_only_in_tests(self):
        extras = _load_pyproject()["project"]["optional-dependencies"]
        names = {item.split(">")[0].split("=")[0].split("[")[0] for item in extras["test"]}
        self.assertEqual(names, {"jsonschema", "rfc3339-validator"})

    def test_dev_extra_includes_the_test_extra_and_a_bounded_linter(self):
        dev = _load_pyproject()["project"]["optional-dependencies"]["dev"]
        self.assertIn("quant-platform[test]", dev)
        ruff = [item for item in dev if item.startswith("ruff")]
        self.assertEqual(len(ruff), 1)
        self.assertIn("<", ruff[0], "ruff must be version-bounded so the lint gate is reproducible")

    def test_ci_installs_the_same_ruff_bound_as_pyproject(self):
        dev = _load_pyproject()["project"]["optional-dependencies"]["dev"]
        specifier = next(item for item in dev if item.startswith("ruff"))
        workflow = (ROOT / ".github" / "workflows" / "integrity.yml").read_text(encoding="utf-8")
        self.assertIn(f'"{specifier}"', workflow)
        self.assertIn("ruff check src tools", workflow)

    def test_ruff_baseline_rule_set_is_explicit(self):
        lint = _load_pyproject()["tool"]["ruff"]["lint"]
        self.assertEqual(lint["select"], ["E4", "E7", "E9", "F"])


if __name__ == "__main__":
    unittest.main()

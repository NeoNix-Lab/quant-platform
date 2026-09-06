#!/usr/bin/env python3
"""Executable ownership and import boundaries for the current modular monolith.

The physical reader is a shared Data Plane seam, not Producer orchestration.
Keep this inventory explicit: a new runtime module needs an ownership decision.
"""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
OWNERS = {
    "quant_platform": "shared",
    "quant_platform.ordering": "shared",
    "quant_platform.data": "shared",
    "quant_platform.data.models": "shared",
    "quant_platform.data.parquet": "physical",
    "quant_platform.data.coverage": "producer",
    "quant_platform.data.manifests": "producer",
    "quant_platform.data.materializer": "producer",
    "quant_platform.data.publication": "producer",
    "quant_platform.data.publication_catalog": "producer",
    "quant_platform.data.publication_eligibility": "producer",
    "quant_platform.data.publication_eligibility_catalog": "producer",
    "quant_platform.access": "access",
    "quant_platform.access.models": "access",
    "quant_platform.access.catalog": "access",
    "quant_platform.access.gateway": "access",
    "quant_platform.source_adapters": "source",
    "quant_platform.source_adapters.bybit": "source",
    "quant_platform.source_adapters.bybit_historical": "source",
}
ALLOWED = {
    "shared": {"shared"},
    "physical": {"physical", "shared"},
    "producer": {"producer", "physical", "shared"},
    "access": {"access", "physical", "shared"},
    "source": {"source", "producer", "shared"},
}
SHARED_STDLIB = {
    "__future__", "collections", "dataclasses", "datetime", "hashlib",
    "json", "re", "typing",
}
ACCESS_MODELS = {
    "CatalogDataset", "CatalogPartition", "DataRequest", "DataSlice",
    "DataSliceMetadata", "LifecyclePolicy", "_fingerprint", "gaps_for",
    "intersect", "merge_intervals", "result_fingerprint",
}


def source_inventory():
    sources = {}
    packages = set()
    for path in sorted((SOURCE / "quant_platform").rglob("*.py")):
        parts = list(path.relative_to(SOURCE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
            packages.add(".".join(parts))
        sources[".".join(parts)] = path.read_text(encoding="utf-8")
    return sources, packages


def dependency_graph(sources, packages):
    """Resolve absolute/relative imports, aliases and package initialization.

Every re-export is itself inspected, including imports inside functions and
TYPE_CHECKING. Star/dynamic imports are refused rather than silently untracked.
"""
    graph = {module: set() for module in sources}
    errors = []
    for module, source in sources.items():
        if module not in OWNERS:
            errors.append(f"unowned module: {module}")
            continue
        owner = OWNERS[module]
        package = module if module in packages else module.rpartition(".")[0]
        tree = ast.parse(source, filename=module)
        aliases = {}

        def add(target, line):
            if target == "quant_platform" or target.startswith("quant_platform."):
                if target not in sources:
                    errors.append(f"{module}:{line}: unresolved module {target}")
                    return
                # Importing a submodule executes its parent __init__ modules.
                graph[module].update(
                    name for name in packages if target.startswith(name + ".")
                )
                graph[module].add(target)
            elif owner == "shared" and target.split(".")[0] not in SHARED_STDLIB:
                errors.append(f"{module}:{line}: shared imports non-pure dependency {target}")
            if target.split(".")[0] == "importlib":
                errors.append(f"{module}:{line}: dynamic import machinery is not a declared seam")
            if target.split(".")[0] in {"tools", "tests"}:
                errors.append(f"{module}:{line}: runtime depends on {target}")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name, node.lineno)
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                target = resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module
                add(target, node.lineno)
                for alias in node.names:
                    if alias.name == "*":
                        errors.append(f"{module}:{node.lineno}: star import hides the public seam")
                    child = f"{target}.{alias.name}"
                    aliases[alias.asname or alias.name] = child
                    if child in sources:
                        add(child, node.lineno)
                    if alias.name in {"__import__", "import_module", "exec", "eval"}:
                        errors.append(f"{module}:{node.lineno}: imported dynamic code/import {alias.name}")
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
                if name in {"__import__", "import_module", "exec", "eval"}:
                    errors.append(f"{module}:{node.lineno}: untracked dynamic code/import {name}")
                if owner == "shared" and name in {"open", "read_text", "read_bytes", "write_text", "write_bytes"}:
                    errors.append(f"{module}:{node.lineno}: shared performs I/O via {name}")
        # A module may already be loaded by the host. Qualified references through
        # an imported namespace must not bypass the rules for direct imports.
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            parts = []
            value = node
            while isinstance(value, ast.Attribute):
                parts.insert(0, value.attr)
                value = value.value
            if isinstance(value, ast.Name) and value.id in aliases:
                qualified = ".".join([aliases[value.id], *parts])
                while qualified:
                    if qualified in sources:
                        add(qualified, node.lineno)
                        break
                    qualified = qualified.rpartition(".")[0]
        for target in sorted(graph[module]):
            if target not in OWNERS or OWNERS[target] not in ALLOWED[owner]:
                errors.append(f"{module} ({owner}) -> forbidden dependency -> {target}")
    return graph, errors


def owner_cycles(graph):
    edges = {owner: set() for owner in ALLOWED}
    for module, targets in graph.items():
        for target in targets:
            if module in OWNERS and target in OWNERS and OWNERS[module] != OWNERS[target]:
                edges[OWNERS[module]].add(OWNERS[target])
    cycles = []

    def visit(owner, path):
        if owner in path:
            cycles.append(path + [owner])
            return
        for target in sorted(edges[owner]):
            visit(target, path + [owner])

    for owner in edges:
        visit(owner, [])
    return cycles


class PackageBoundaryTests(unittest.TestCase):
    def test_every_runtime_module_has_an_owner_and_only_allowed_dependencies(self):
        sources, packages = source_inventory()
        self.assertEqual(set(OWNERS), set(sources))
        _, errors = dependency_graph(sources, packages)
        self.assertEqual([], errors)

    def test_owner_graph_is_acyclic_including_package_initializers(self):
        sources, packages = source_inventory()
        graph, errors = dependency_graph(sources, packages)
        self.assertEqual([], errors)
        self.assertEqual([], owner_cycles(graph))

    def test_access_models_are_defined_only_by_access(self):
        sources, _ = source_inventory()
        for module, source in sources.items():
            definitions = {node.name for node in ast.parse(source).body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}
            if module == "quant_platform.access.models":
                self.assertTrue(ACCESS_MODELS.issubset(definitions))
            else:
                self.assertFalse(ACCESS_MODELS.intersection(definitions), module)

    def test_forbidden_import_forms_are_detected_without_editing_runtime(self):
        sources, packages = source_inventory()
        cases = [
            ("quant_platform.data.models", "import quant_platform.source_adapters.bybit as policy"),
            ("quant_platform.ordering", "from . import source_adapters as adapters"),
            ("quant_platform.data.materializer", "from ..access import DataGateway as Reader"),
            ("quant_platform.source_adapters.bybit", "from ..access.models import DataRequest"),
            ("quant_platform.access.gateway", "def run():\n    from ..data import materializer as writer"),
            ("quant_platform.access.models", "from quant_platform.data.publication import QualityReport"),
            ("quant_platform.access.catalog", "from ..data import manifests"),
            ("quant_platform.access.gateway", "from ..data.publication_eligibility import PublicationEligibilityBridge"),
            ("quant_platform.data.models", "from pathlib import Path"),
            ("quant_platform.ordering", "open('canonical.parquet')"),
            ("quant_platform.access.gateway", "__import__('quant_platform.data.materializer')"),
            ("quant_platform.access.gateway", "from builtins import __import__ as load\nload('quant_platform.data.materializer')"),
            ("quant_platform.access.gateway", "from quant_platform.data import *"),
            ("quant_platform.access.gateway", "import quant_platform as qp\nqp.data.materializer.materialize_trade_v1()"),
            ("quant_platform.source_adapters.bybit", "from .. import access as reads\nreads.DataGateway()"),
        ]
        for module, injected in cases:
            with self.subTest(module=module, injected=injected):
                _, errors = dependency_graph({**sources, module: injected}, packages)
                self.assertTrue(errors)

    def test_eager_reexport_cannot_hide_forbidden_dependency(self):
        sources, packages = source_inventory()
        sources["quant_platform.data"] += "\nfrom .materializer import materialize_trade_v1 as write\n"
        graph, errors = dependency_graph(sources, packages)
        self.assertTrue(any("quant_platform.data (shared) -> forbidden" in error for error in errors))
        self.assertIn("quant_platform.data", graph["quant_platform.access.gateway"])

    def test_cycle_detector_rejects_a_reverse_owner_edge(self):
        graph = {
            "quant_platform.access.gateway": {"quant_platform.data.models"},
            "quant_platform.data.models": {"quant_platform.access.gateway"},
        }
        self.assertTrue(owner_cycles(graph))

    def test_shared_imports_do_not_load_access_or_producer_runtimes(self):
        # Fresh isolated processes prevent previous test imports from hiding eager loads.
        for statement in (
            "import quant_platform.data.models",
            "from quant_platform.data import Instant, CanonicalContentHashV1",
            "from quant_platform.ordering import OrderingProvider",
        ):
            with self.subTest(statement=statement):
                script = (
                    "import sys\n"
                    f"sys.path.insert(0, {str(SOURCE)!r})\n"
                    f"{statement}\n"
                    f"owners = {OWNERS!r}\n"
                    "loaded = {name for name in sys.modules if name == 'quant_platform' or name.startswith('quant_platform.')}\n"
                    "unexpected = {name for name in loaded if owners.get(name) != 'shared'}\n"
                    "assert not unexpected, sorted(unexpected)\n"
                )
                result = subprocess.run([sys.executable, "-I", "-B", "-c", script], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

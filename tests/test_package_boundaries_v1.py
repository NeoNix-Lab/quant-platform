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
TOOLS = ROOT / "tools"
TESTS = ROOT / "tests"
CLIENTS = ROOT / "clients"
OWNERS = {
    "quant_platform": "shared",
    "quant_platform.canonical": "shared",
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
    "quant_platform.data.quality_lifecycle": "producer",
    "quant_platform.data.repair": "producer",
    "quant_platform.access": "access",
    "quant_platform.access.models": "access",
    "quant_platform.access.catalog": "access",
    "quant_platform.access.gateway": "access",
    "quant_platform.representation": "representation",
    "quant_platform.representation.candles": "representation",
    "quant_platform.representation.footprints": "representation",
    "quant_platform.features": "feature",
    "quant_platform.features.artifacts": "feature",
    "quant_platform.features.definitions": "feature",
    "quant_platform.features.h01_imbalance": "feature",
    "quant_platform.features.imbalance": "feature",
    "quant_platform.operations": "operations",
    "quant_platform.operations.capacity": "operations",
    "quant_platform.operations.checkpoint": "operations",
    "quant_platform.operations.observability": "operations",
    "quant_platform.operations.pressure": "operations",
    "quant_platform.operations.protection": "operations",
    "quant_platform.operations.recovery": "operations",
    "quant_platform.operations.relocation": "operations",
    "quant_platform.operations.retention": "operations",
    "quant_platform.validation": "validation",
    "quant_platform.validation.availability": "validation",
    "quant_platform.validation.labels": "validation",
    "quant_platform.validation.lockbox": "validation",
    "quant_platform.validation.robustness": "validation",
    "quant_platform.validation.walk_forward": "validation",
    "quant_platform.learning": "learning",
    "quant_platform.learning.supervised": "learning",
    "quant_platform.learning.training": "learning",
    "quant_platform.experiments": "experiment",
    "quant_platform.experiments.accounting": "experiment",
    "quant_platform.experiments.identities": "experiment",
    "quant_platform.experiments.persistence": "experiment",
    "quant_platform.research": "research",
    "quant_platform.research.events": "research",
    "quant_platform.research.hypothesis": "research",
    "quant_platform.research.outcomes": "research",
    "quant_platform.research.studies": "research",
    "quant_platform.strategy": "strategy",
    "quant_platform.execution": "execution",
    "quant_platform.portfolio": "portfolio",
    "quant_platform.replay": "replay",
    "quant_platform.source_adapters": "source",
    "quant_platform.source_adapters.bybit": "source",
    "quant_platform.source_adapters.bybit_historical": "source",
    "quant_platform.source_adapters.bybit_live": "source",
    "quant_platform.source_adapters.l2": "source",
    "quant_platform.application": "application",
    "quant_platform.application.admitted_input": "application",
    "quant_platform.application.governed_result_import": "application",
    "quant_platform.application.api_transport_server": "application",
    "quant_platform.application.backup_restore": "application",
    "quant_platform.application.bybit_import": "application",
    "quant_platform.application.bybit_live": "application",
    "quant_platform.application.composition": "application",
    "quant_platform.application.conformity": "application",
    "quant_platform.application.durable_jobs": "application",
    "quant_platform.application.golden_conformity": "application",
    "quant_platform.application.golden_replay": "application",
    "quant_platform.application.golden_supervised": "application",
    "quant_platform.application.h01_composition": "application",
    "quant_platform.application.live_candle_stream": "application",
    "quant_platform.application.live_gap_orchestration": "application",
    "quant_platform.application.live_ingest_server": "application",
    "quant_platform.application.market_data": "application",
    "quant_platform.application.retention_deletion": "application",
    "quant_platform.application.framed_result_transport": "application",
    "quant_platform.application.storage_relocation": "application",
    "quant_platform.application.strategy_consumer": "application",
    "quant_platform.application.training_consumer": "application",
    "quant_platform.application.validation_consumer": "application",
    "quant_platform.application.wave6_golden": "application",
}
ALLOWED = {
    "shared": {"shared"},
    "physical": {"physical", "shared"},
    "producer": {"producer", "physical", "shared"},
    "access": {"access", "physical", "shared"},
    "representation": {"representation", "shared"},
    "feature": {"feature", "shared"},
    "operations": {"operations", "shared"},
    "validation": {"validation", "shared"},
    "learning": {"learning", "experiment", "validation", "feature", "shared"},
    "experiment": {"experiment", "shared"},
    "research": {"research", "feature", "shared"},
    "strategy": {"strategy", "shared"},
    "execution": {"execution", "strategy", "shared"},
    "portfolio": {"portfolio", "execution", "shared"},
    "replay": {"replay", "access", "strategy", "execution", "portfolio", "shared"},
    "source": {"source", "producer", "shared"},
    # The application seam composes capabilities and owns no domain semantics.
    # Nothing may depend on it: it is the top of the owner graph.
    "application": {
        "application", "access", "representation", "feature", "producer", "operations", "validation",
        "source", "physical", "shared", "strategy", "execution", "portfolio", "replay",
        "learning", "experiment"
    },
}
SHARED_STDLIB = {
    "__future__", "collections", "dataclasses", "datetime", "hashlib", "importlib",
    "json", "re", "typing", "datetime", "secrets", "ssl",
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
            elif (
                owner == "shared" and target.split(".")[0] not in SHARED_STDLIB
                and not (module == "quant_platform.canonical" and target == "rfc8785")
            ):
                errors.append(f"{module}:{line}: shared imports non-pure dependency {target}")
            if target.split(".")[0] == "rfc8785" and module != "quant_platform.canonical":
                errors.append(f"{module}:{line}: RFC 8785 must use the declared canonical profile")
            if target.split(".")[0] == "importlib" and target != "importlib.metadata":
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


APPLICATION = "quant_platform.application"
# Declared runtime dependencies from pyproject.  Executables may use them; the
# rule below constrains which *repository* code a tool may reach, not which
# third-party libraries it links.
THIRD_PARTY = {"psycopg", "pyarrow", "websockets"}

# ASS-03 is complete when no tools bypass the application seam.
TOOLS_PENDING_ASS03 = set()
TOOLS_TESTS_PENDING_ASS03 = set()
# The rule is deliberately one-directional.  Forbidding tools -> tests is what
# makes a tools/tests cycle impossible, so tests -> tools needs no restriction:
# a test importing the executable it tests is verification, not a bypass.
DYNAMIC_CODE_TARGET = "dynamic-code:"
DYNAMIC_CODE_NAMES = {"__import__", "import_module", "exec", "eval"}


def script_layers():
    """Map every tools/ and tests/ module name to its layer.

    Both directories are placed on ``sys.path`` by existing scripts, so a bare
    import name must resolve to exactly one layer for the rules to be sound.
    """
    layers = {}
    for directory, layer in ((TOOLS, "tools"), (TESTS, "tests")):
        for path in sorted(directory.glob("*.py")):
            if path.stem in layers:
                raise AssertionError(f"ambiguous script module name: {path.stem}")
            layers[path.stem] = layer
    return layers


def script_inventory():
    scripts = {}
    for directory, layer in ((TOOLS, "tools"), (TESTS, "tests")):
        for path in sorted(directory.glob("*.py")):
            scripts[path.stem] = (layer, path.read_text(encoding="utf-8"))
    return scripts


def imported_targets(source, filename):
    """Every imported module path, with the line that imported it."""
    targets = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # tools/ and tests/ are flat script directories, not packages.
                targets.append((f"relative-import-level-{node.level}", node.lineno))
            else:
                targets.append((node.module or "", node.lineno))
            for alias in node.names:
                if alias.name in DYNAMIC_CODE_NAMES:
                    targets.append((DYNAMIC_CODE_TARGET + alias.name, node.lineno))
        elif isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in DYNAMIC_CODE_NAMES:
                targets.append((DYNAMIC_CODE_TARGET + name, node.lineno))
    return targets


def client_inventory():
    if not CLIENTS.exists():
        return {}
    return {
        path.relative_to(ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(CLIENTS.rglob("*"))
        if path.suffix in {".css", ".html", ".js", ".mjs", ".py"}
    }


def client_import_violations(clients):
    errors = []
    for filename, source in clients.items():
        if filename.endswith(".py"):
            for target, line in imported_targets(source, filename):
                if target.split(".")[0] == "quant_platform":
                    errors.append(f"{filename}:{line}: remote client imports repository runtime -> {target}")
                if target.startswith(DYNAMIC_CODE_TARGET) or target.split(".")[0] == "importlib":
                    errors.append(f"{filename}:{line}: remote client uses dynamic import machinery -> {target}")
        elif "quant_platform" in source:
            errors.append(f"{filename}: remote client references repository runtime")
    return errors


def prohibited_script_edges(scripts, layers):
    """Return the exact tool-to-domain and tool-to-tests edges needing debt."""
    domain_edges = set()
    test_edges = set()
    for module, (layer, source) in sorted(scripts.items()):
        if layer != "tools":
            continue
        for target, _ in imported_targets(source, module):
            root = target.split(".")[0]
            if root == "quant_platform":
                if target != APPLICATION and not target.startswith(APPLICATION + "."):
                    domain_edges.add((module, target))
            elif root in layers and layers[root] == "tests":
                test_edges.add((module, root))
    return domain_edges, test_edges


def stale_ledger_entries(scripts, layers, domain_ledger, tests_ledger):
    domain_edges, test_edges = prohibited_script_edges(scripts, layers)
    return domain_ledger - domain_edges, tests_ledger - test_edges


def script_violations(scripts, layers):
    """Executable orchestration must not bypass the application seam.

    tools/ is executable orchestration: it may reach repository code only
    through ``quant_platform.application`` (plus its own siblings).
    tests/ is verification composition: it may compose any runtime owner
    directly, and is bound only by the tools/tests direction rules.
    """
    errors = []
    for module, (layer, source) in sorted(scripts.items()):
        for target, line in imported_targets(source, module):
            root = target.split(".")[0]
            if target.startswith(DYNAMIC_CODE_TARGET) or root == "importlib":
                if layer == "tools":
                    errors.append(
                        f"{layer}/{module}.py:{line}: dynamic code/import machinery "
                        f"is not a declared seam -> {target}"
                    )
            elif root == "quant_platform":
                if layer != "tools":
                    continue
                if target == APPLICATION or target.startswith(APPLICATION + "."):
                    continue
                if (module, target) in TOOLS_PENDING_ASS03:
                    continue
                errors.append(
                    f"{layer}/{module}.py:{line}: executable orchestration bypasses "
                    f"the application seam -> {target}"
                )
            elif root in layers:
                target_layer = layers[root]
                if layer == "tools" and target_layer == "tests":
                    if (module, root) in TOOLS_TESTS_PENDING_ASS03:
                        continue
                    errors.append(
                        f"{layer}/{module}.py:{line}: executable depends on "
                        f"verification support -> {root}"
                    )
            elif layer == "tools" and root not in sys.stdlib_module_names and root not in THIRD_PARTY:
                errors.append(
                    f"{layer}/{module}.py:{line}: undeclared dependency -> {target}"
                )
    return errors


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

    def test_rfc8785_dependency_is_exactly_the_canonical_module(self):
        sources, packages = source_inventory()
        _, errors = dependency_graph(sources, packages)
        self.assertEqual([], errors)
        for module in ("quant_platform.data.models", "quant_platform.application.market_data"):
            with self.subTest(module=module):
                _, errors = dependency_graph({**sources, module: "import rfc8785"}, packages)
                self.assertTrue(any("RFC 8785 must use" in error for error in errors))
        _, errors = dependency_graph(
            {**sources, "quant_platform.canonical": "import requests"}, packages
        )
        self.assertTrue(any("shared imports non-pure" in error for error in errors))

    def test_cycle_detector_rejects_a_reverse_owner_edge(self):
        graph = {
            "quant_platform.access.gateway": {"quant_platform.data.models"},
            "quant_platform.data.models": {"quant_platform.access.gateway"},
        }
        self.assertTrue(owner_cycles(graph))

    def test_application_is_owned_and_nothing_depends_on_it(self):
        sources, _ = source_inventory()
        self.assertIn(APPLICATION, sources)
        self.assertEqual("application", OWNERS[APPLICATION])
        for owner, permitted in ALLOWED.items():
            if owner == "application":
                continue
            self.assertNotIn("application", permitted, owner)

    def test_script_module_names_resolve_to_one_layer(self):
        layers = script_layers()
        self.assertEqual("tools", layers["conformity_e2e"])
        self.assertEqual("tests", layers["golden_conformity_support"])

    def test_executable_orchestration_respects_the_application_seam(self):
        self.assertEqual([], script_violations(script_inventory(), script_layers()))

    def test_remote_clients_do_not_import_quant_platform_runtime(self):
        clients = client_inventory()
        self.assertIn("clients/tui/market_data_tui.py", clients)
        self.assertIn("clients/app_ui/app.js", clients)
        self.assertEqual([], client_import_violations(clients))

    def test_migration_ledgers_describe_real_unmigrated_modules(self):
        scripts = script_inventory()
        layers = script_layers()
        for module, _ in TOOLS_PENDING_ASS03:
            self.assertEqual("tools", layers.get(module), module)
        for module, target in TOOLS_TESTS_PENDING_ASS03:
            self.assertEqual("tools", layers.get(module), module)
            self.assertEqual("tests", layers.get(target), target)
        stale_domain, stale_tests = stale_ledger_entries(
            scripts, layers, TOOLS_PENDING_ASS03, TOOLS_TESTS_PENDING_ASS03
        )
        self.assertEqual(set(), stale_domain, f"stale tool -> domain debt: {sorted(stale_domain)}")
        self.assertEqual(set(), stale_tests, f"stale tool -> tests debt: {sorted(stale_tests)}")

    def test_mutations_cannot_reuse_or_outlive_debt_exemptions(self):
        layers = script_layers()
        new_edge = {"import_bybit_trades": ("tools", "from quant_platform.access.gateway import DataGateway")}
        self.assertTrue(script_violations(new_edge, layers))

        stale_domain, _ = stale_ledger_entries(
            {"import_bybit_trades": ("tools", "import argparse")},
            layers,
            {("import_bybit_trades", "quant_platform.data.models")},
            set(),
        )
        self.assertEqual({("import_bybit_trades", "quant_platform.data.models")}, stale_domain)

        _, stale_tests = stale_ledger_entries(
            {"conformity_e2e": ("tools", "from quant_platform.access.gateway import DataGateway")},
            layers,
            set(),
            {("conformity_e2e", "golden_conformity_support")},
        )
        self.assertEqual({("conformity_e2e", "golden_conformity_support")}, stale_tests)

    def test_forbidden_orchestration_forms_are_detected_without_editing_repository(self):
        layers = dict(script_layers())
        layers["new_tool"] = "tools"
        layers["new_test"] = "tests"
        cases = [
            ("new_tool", "tools", "from quant_platform.access.gateway import DataGateway"),
            ("new_tool", "tools", "import quant_platform.data.materializer"),
            ("new_tool", "tools", "from quant_platform.source_adapters.bybit import materialize_bybit_trade_v1"),
            ("new_tool", "tools", "import golden_conformity_support"),
            ("new_tool", "tools", "from adversarial_support import build"),
            ("new_tool", "tools", "import requests"),
            ("new_tool", "tools", "import importlib\nimportlib.import_module('quant_platform.data.materializer')"),
            ("new_tool", "tools", "__import__('quant_platform.data.materializer')"),
        ]
        for module, layer, injected in cases:
            with self.subTest(module=module, injected=injected):
                errors = script_violations({module: (layer, injected)}, layers)
                self.assertTrue(errors, injected)

    def test_permitted_orchestration_forms_are_accepted(self):
        layers = dict(script_layers())
        layers["new_tool"] = "tools"
        layers["new_test"] = "tests"
        cases = [
            ("new_tool", "tools", "from quant_platform.application import compose"),
            ("new_tool", "tools", "import quant_platform.application"),
            ("new_tool", "tools", "import argparse, json, sys"),
            ("new_tool", "tools", "import psycopg"),
            ("new_tool", "tools", "import semantic_validator"),
            # Verification composition roots: a test may import runtime owners,
            # the executable it tests, and test support, without restriction.
            ("new_test", "tests", "from quant_platform.access.gateway import DataGateway"),
            ("new_test", "tests", "import quant_platform.data.materializer"),
            ("new_test", "tests", "from golden_conformity_support import observe_scan"),
            ("new_test", "tests", "import conformity_e2e"),
            ("new_test", "tests", "from semantic_validator import validate"),
            ("new_test", "tests", "from import_bybit_trades import main"),
        ]
        for module, layer, injected in cases:
            with self.subTest(module=module, injected=injected):
                errors = script_violations({module: (layer, injected)}, layers)
                self.assertEqual([], errors, injected)

    def test_runtime_owner_cannot_depend_on_the_application_seam(self):
        sources, packages = source_inventory()
        for module in ("quant_platform.access.gateway", "quant_platform.data.materializer", "quant_platform.data.models"):
            with self.subTest(module=module):
                injected = {**sources, module: f"from {APPLICATION} import compose"}
                _, errors = dependency_graph(injected, packages)
                self.assertTrue(
                    any("forbidden dependency" in error and APPLICATION in error for error in errors),
                    errors,
                )

    def test_shared_imports_do_not_load_access_or_producer_runtimes(self):
        # Fresh isolated processes prevent previous test imports from hiding eager loads.
        for statement in (
            "from quant_platform.canonical import canonical_bytes",
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

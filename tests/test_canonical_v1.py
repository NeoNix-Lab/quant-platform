#!/usr/bin/env python3
"""P01 byte parity and complete site inventory from the pre-migration baseline."""

from __future__ import annotations

import ast
from collections import Counter
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import rfc8785


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.canonical import canonical_bytes  # noqa: E402
from quant_platform.application.bybit_import import CANONICAL_FIELD_ORDER, encode_record  # noqa: E402


INVENTORY = json.loads((ROOT / "tests/fixtures/canonical_sites_v1.json").read_text())
CORPUS = (
    {"z": "é/東京/😀", "a": {"β": [True, None, "\\\"\n"]}},
    {"small": 1e-07, "whole": 2.0, "large": 1e16, "negative_zero": -0.0},
    {"decimal": str(Decimal("1.2300")), "nested": [{"b": 2, "a": [1, 3]}]},
    {}, [], None,
)


def old_bytes(value, site):
    """The exact pre-migration serialization expression, not the new helper."""
    if site["profile"] == "rfc8785-v1":
        return rfc8785.dumps(value)
    options = dict(site["options"])
    options["separators"] = tuple(options["separators"])
    result = json.dumps(value, **options).encode("utf-8")
    return result + b"\n" if site["profile"] == "ordered-compact-ascii-line-v1" else result


def migrated_sites(sources):
    sites, raw = [], []
    for path, source in sorted(sources.items()):
        tree = ast.parse(source)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update((alias.asname or alias.name, alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                aliases.update((alias.asname or alias.name, f"{node.module}.{alias.name}") for alias in node.names)
        ordinal = 0
        for node in sorted(ast.walk(tree), key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0))):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = aliases.get(node.func.id, node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                name = f"{aliases.get(node.func.value.id, node.func.value.id)}.{node.func.attr}"
            if name in ("json.dumps", "rfc8785.dumps"):
                raw.append([path, ast.dump(node, include_attributes=False)])
            if name != "quant_platform.canonical.canonical_bytes":
                continue
            scope = []
            ancestor = parents.get(node)
            while ancestor:
                if isinstance(ancestor, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    scope.insert(0, ancestor.name)
                ancestor = parents.get(ancestor)
            ordinal += 1
            options = {k.arg: ast.literal_eval(k.value) for k in node.keywords}
            sites.append({"path": path, "ordinal": ordinal, "scope": ".".join(scope),
                          "argument_ast": ast.dump(node.args[0], include_attributes=False),
                          "parameters": options})
    return sites, raw


class CanonicalV1Tests(unittest.TestCase):
    def test_each_migrated_site_preserves_its_argument_profile_and_nan_policy(self):
        sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
                   for p in (ROOT / "src/quant_platform").rglob("*.py") if p.name != "canonical.py"}
        sites, raw = migrated_sites(sources)
        expected = []
        for site in INVENTORY["sites"]:
            parameters = {"profile": site["profile"]}
            if site["profile"] != "rfc8785-v1":
                parameters["allow_nan"] = site["options"].get("allow_nan", True)
            expected.append({k: site[k] for k in ("path", "ordinal", "scope", "argument_ast")}
                            | {"parameters": parameters})
        self.assertEqual(expected, sites)
        self.assertEqual(INVENTORY["raw_exceptions"], raw)
        self.assertEqual(Counter({"sorted-compact-ascii-v1": 56, "sorted-compact-utf8-v1": 3,
                                  "ordered-compact-ascii-line-v1": 1, "rfc8785-v1": 2}),
                         Counter(s["parameters"]["profile"] for s in sites))

    def test_old_and_new_bytes_match_at_every_site_on_adversarial_corpus(self):
        for site in INVENTORY["sites"]:
            parameters = {"profile": site["profile"]}
            if site["profile"] != "rfc8785-v1":
                parameters["allow_nan"] = site["options"].get("allow_nan", True)
            for value in CORPUS:
                with self.subTest(path=site["path"], scope=site["scope"], value=value):
                    self.assertEqual(old_bytes(value, site), canonical_bytes(value, **parameters))
            # Decimal normalization remains domain-owned: a raw Decimal still
            # fails with precisely the old serializer's exception type.
            for value in (Decimal("1.2300"), {"x": float("nan")}, {"x": float("inf")}):
                with self.subTest(path=site["path"], scope=site["scope"], value=value):
                    try:
                        expected = old_bytes(value, site)
                    except Exception as error:
                        with self.assertRaises(type(error)):
                            canonical_bytes(value, **parameters)
                    else:
                        self.assertEqual(expected, canonical_bytes(value, **parameters))

    def test_ordered_trade_line_retains_declared_field_order_and_one_lf(self):
        record = {"sequence": None, "price": "1.2300", "venue": "é", "trade_id": "東京"}
        ordered = {key: record[key] for key in CANONICAL_FIELD_ORDER if key in record}
        expected = json.dumps(ordered, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
        self.assertEqual(expected, encode_record(record))
        self.assertFalse(expected.endswith(b"\r\n"))

    def test_guard_detects_new_raw_calls_and_aliases(self):
        for source in ("import json\njson.dumps({})", "import json as j\nj.dumps({})",
                       "from json import dumps as encode\nencode({})",
                       "import rfc8785 as j\nj.dumps({})"):
            with self.subTest(source=source):
                _, raw = migrated_sites({"injected.py": source})
                self.assertEqual(1, len(raw))

    def test_only_j14_declares_the_rfc8785_profile(self):
        sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
                   for p in (ROOT / "src/quant_platform").rglob("*.py") if p.name != "canonical.py"}
        sites, _ = migrated_sites(sources)
        uses = [s["path"] for s in sites if s["parameters"]["profile"] == "rfc8785-v1"]
        self.assertEqual(["src/quant_platform/application/framed_result_transport.py"] * 2, uses)

    def test_profile_identities_are_identical_in_two_hashseed_processes(self):
        representatives = {s["profile"]: s for s in INVENTORY["sites"]}
        value = CORPUS[0] | CORPUS[1] | CORPUS[2]
        expected = {profile: hashlib.sha256(old_bytes(value, site)).hexdigest()
                    for profile, site in representatives.items()}
        script = (
            "import hashlib,json,sys\n"
            f"sys.path.insert(0, {str(ROOT / 'src')!r})\n"
            "from quant_platform.canonical import canonical_bytes\n"
            f"value = {value!r}\n"
            f"profiles = {list(representatives)!r}\n"
            "print(json.dumps({p: hashlib.sha256(canonical_bytes(value, profile=p)).hexdigest() "
            "for p in profiles}, sort_keys=True))\n"
        )
        outputs = []
        for seed in ("17", "891"):
            result = subprocess.run([sys.executable, "-B", "-c", script],
                                    env=dict(os.environ, PYTHONHASHSEED=seed),
                                    capture_output=True, text=True, check=True)
            self.assertEqual(expected, json.loads(result.stdout))
            outputs.append(result.stdout)
        self.assertEqual(outputs[0], outputs[1])

    def test_unknown_profile_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unknown canonical byte profile"):
            canonical_bytes({}, profile="sorted-compact-ascii-v2")


if __name__ == "__main__":
    unittest.main()

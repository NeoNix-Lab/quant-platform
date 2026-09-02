"""Focused, provider-neutral S14 bridge tests.

The small fake catalog is deliberately limited to the bridge boundary.  SQL
locking and rollback are proved by the dedicated PostgreSQL integration.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data import (
    DatasetIdentity,
    Instant,
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
    PublicationEligibilityRefusal,
)
from quant_platform.data.manifests import emit_coverage_manifest, emit_dataset_manifest
from quant_platform.data.publication_eligibility_catalog import (
    PublicationEligibilityCatalog,
    _current_report,
    _eligible_state,
    _live_row,
    _select_authoritative_report,
)


class FakeCatalog:
    def __init__(self):
        self.calls = []

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        return "published"


class OrderedCursor:
    def __init__(self, connection):
        self.connection = connection
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, params=None):
        sql = " ".join(statement.split())
        self.connection.statements.append(sql)
        if "FROM catalog.datasets" in sql and "FOR UPDATE" not in sql:
            identity = tuple(params)
            self.result = [self.connection.datasets[identity]]
        elif "FROM catalog.datasets" in sql and "FOR UPDATE" in sql:
            self.result = [(dataset_id,) for dataset_id in sorted(params)]
        elif "FROM catalog.partitions" in sql:
            self.result = list(self.connection.topology)
        elif "FROM catalog.quality_reports" in sql:
            self.result = list(self.connection.reports)
        elif "INSERT INTO catalog.dataset_lineage" in sql:
            self.connection.lineage.add((str(params[1]), params[2]))
            self.result = []
        elif sql.startswith("SELECT parent_id::text"):
            self.result = list(self.connection.lineage)
        elif sql.startswith("UPDATE catalog.partitions"):
            self.connection.topology[0] = (*self.connection.topology[0][:4], params[0], *self.connection.topology[0][5:])
            self.result = [(self.connection.topology[0][0],)]
        else:
            self.result = []

    def fetchall(self):
        return self.result

    def fetchone(self):
        return self.result[0] if self.result else None


class OrderedConnection:
    def __init__(self, datasets, topology, reports):
        self.datasets = datasets
        self.topology = list(topology)
        self.reports = reports
        self.lineage = set()
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return OrderedCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _partition(identity: DatasetIdentity) -> dict:
    return {
        "schema_version": "partition-manifest-v1", **identity.stable_dict(),
        "partition_key": "dt=2024-01-15", "revision": 1, "state": "closed",
        "rel_path": "dt=2024-01-15/part-001.parquet", "file_size_bytes": 0,
        "row_count": 0, "sha256": "0" * 64, "first_exchange_ts": None,
        "last_exchange_ts": None, "created_at": "2026-09-01T10:00:00Z",
        "closed_at": "2026-09-01T10:00:01Z", "producer": "generic-producer",
        "code_ref": "producer-commit",
    }


def _report(identity, *, status="pass", profile="generic-profile", suite="generic-suite",
            partition_hash="partition-hash", dataset_hash="dataset-hash",
            physical=None, categories=None, violations=None, code_ref="certifier-commit",
            coverage_ids=None, assertion_ids=None, coverage_hashes=None):
    physical = physical or "0" * 64
    coverage_ids = coverage_ids or ["coverage-1"]
    assertion_ids = assertion_ids or ["assertion-1"]
    coverage_hashes = coverage_hashes or ["coverage-hash"]
    categories = categories or {name: "pass" for name in ("source", "canonical", "physical", "manifests", "coverage")}
    metrics = {
        "certification_profile": profile,
        "natural_partition_identity": {"dataset_identity": identity.stable_dict(), "partition_key": "dt=2024-01-15", "revision": 1},
        "dataset_manifest_sha256": dataset_hash,
        "partition_manifest_sha256": partition_hash,
        "coverage_manifest_id": coverage_ids[0], "coverage_assertion_id": assertion_ids[0],
        "coverage_manifest_ids": list(coverage_ids), "coverage_assertion_ids": list(assertion_ids),
        "coverage_manifest_sha256": list(coverage_hashes), "physical_artifact_hash": physical,
        "canonical_content_hash_v1": "c" * 64,
        "evidence": {name: {"status": value} for name, value in categories.items()},
    }
    return ("report-id", status, metrics, violations or [], code_ref, "2026-09-01T11:00:00Z"), metrics


class PublicationEligibilityBridgeTests(unittest.TestCase):
    def evidence(self, venue="genericvenue"):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            identity = DatasetIdentity("canonical", "trades", venue, "BTC-USD", "trade-v1")
            dataset_path = root / "dataset.json"
            partition_path = root / "partition.json"
            coverage_path = root / "coverage.json"
            dataset = emit_dataset_manifest(
                dataset_path, dataset_identity=identity, created_at="2026-09-01T10:00:00Z",
                derived_from=[DatasetIdentity("raw", "trades", venue, "BTC-USD", "trade-v1")],
                transform="canonicalize-trades-v1",
            )
            partition = _partition(identity)
            from quant_platform.data.manifests import _validate_partition_document
            _validate_partition_document(partition)
            partition_path.write_bytes(json.dumps(partition, sort_keys=True, separators=(",", ":")).encode())
            emit_coverage_manifest(
                coverage_path, dataset_identity=identity, source_dataset_identity=identity,
                coverage_id="coverage-1", supersedes=None, created_at="2026-09-01T10:00:02Z",
                acquisition={"basis": "source_extract", "intent_start": "2024-01-15T00:00:00Z", "intent_end": "2024-01-16T00:00:00Z", "source_semantics": "generic-source-v1", "mapping": "generic-mapping-v1"},
                assertions=[{"assertion_id": "assertion-1", "start": "2024-01-15T00:00:00Z", "end": "2024-01-16T00:00:00Z", "status": "complete", "partitions": [{"partition_key": "dt=2024-01-15", "revision": 1}], "evidence": [{"kind": "deterministic_source_extract", "detail": "generic"}]}],
                producer="generic-source", code_ref="source-commit", partition_manifests=[partition],
            )
            # Paths are copied because the temporary directory is otherwise removed.
            saved = Path(tempfile.mkdtemp())
            for item in (dataset_path, partition_path, coverage_path):
                (saved / item.name).write_bytes(item.read_bytes())
        return identity, saved

    def test_A_fold_is_real_and_input_has_no_artifact(self):
        identity, root = self.evidence()
        catalog = FakeCatalog()
        result = PublicationEligibilityBridge(catalog).publish(PublicationEligibilityEvidence(
            root / "dataset.json", root / "partition.json", (root / "coverage.json",), "hot", "generic-profile", "generic-suite"))
        self.assertEqual(result, "published")
        self.assertNotIn("artifact_path", PublicationEligibilityEvidence.__dataclass_fields__)
        self.assertEqual(catalog.calls[0]["coverage_start"], Instant.parse("2024-01-15T00:00:00Z"))

    def test_B_second_provider_is_configuration_only(self):
        identity, root = self.evidence("coinbase")
        catalog = FakeCatalog()
        PublicationEligibilityBridge(catalog).publish(PublicationEligibilityEvidence(root / "dataset.json", root / "partition.json", (root / "coverage.json",), "cold", "coinbase-profile", "coinbase-suite"))
        self.assertEqual(catalog.calls[0]["expected_profile"], "coinbase-profile")

    def test_C_invalid_manifest_refuses(self):
        with tempfile.TemporaryDirectory() as holder:
            path = Path(holder) / "bad.json"
            path.write_text("{}")
            with self.assertRaises(PublicationEligibilityRefusal):
                PublicationEligibilityBridge(FakeCatalog()).publish(PublicationEligibilityEvidence(path, path, (path,), "hot", "p", "s"))

    def test_D_coverage_violation_refuses_before_catalog(self):
        identity, root = self.evidence()
        coverage = json.loads((root / "coverage.json").read_text())
        coverage["assertions"][0]["status"] = "known_gap"
        (root / "coverage.json").write_text(json.dumps(coverage))
        with self.assertRaises(PublicationEligibilityRefusal):
            PublicationEligibilityBridge(FakeCatalog()).publish(PublicationEligibilityEvidence(root / "dataset.json", root / "partition.json", (root / "coverage.json",), "hot", "p", "s"))


class AuthorityTests(unittest.TestCase):
    def setup(self):
        identity = DatasetIdentity("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1")
        target = ("partition", "dataset", "dt=2024-01-15", 1, "closed", "hot", "dt=2024-01-15/part-001.parquet", Instant.parse("2024-01-15T00:00:00Z").to_datetime(), Instant.parse("2024-01-16T00:00:00Z").to_datetime(), 0, 0, "0" * 64, "partition-hash", Instant.parse("2026-09-01T10:00:00Z").to_datetime(), Instant.parse("2026-09-01T10:00:01Z").to_datetime(), None, None, "generic-producer", "producer-commit")
        part = _partition(identity)
        return identity, target, part

    def select(self, reports):
        identity, target, part = self.setup()
        return _select_authoritative_report(reports, expected_profile="generic-profile", expected_check_suite="generic-suite", identity=identity, partition=part, dataset_sha256="dataset-hash", partition_sha256="partition-hash", coverage_ids=["coverage-1"], assertion_ids=["assertion-1"], coverage_sha256=["coverage-hash"], target=target)

    def test_E_pass_authorizes_valid(self):
        identity, _, _ = self.setup(); report, _ = _report(identity)
        self.assertEqual(self.select([report]).status, "pass")

    def test_F_warn_authorizes_degraded_only(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, status="warn", categories={"source": "warn", "canonical": "pass", "physical": "pass", "manifests": "pass", "coverage": "pass"}, violations=[{"category": "source", "message": "weak"}])
        self.assertEqual(self.select([report]).status, "warn")

    def test_G_fail_never_authorizes(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, status="fail", categories={name: "fail" for name in ("source", "canonical", "physical", "manifests", "coverage")}, violations=[{"category": "physical", "message": "bad"}])
        with self.assertRaises(PublicationEligibilityRefusal): _eligible_state(self.select([report]))

    def test_H_missing_certifier_code_refuses(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, code_ref="")
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_I_forbidden_warn_refuses(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, status="warn", categories={name: "warn" for name in ("source", "canonical", "physical", "manifests", "coverage")})
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_J_stale_manifest_hash_is_excluded(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, partition_hash="old")
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_K_physical_hash_mismatch_is_excluded(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, physical="f" * 64)
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_L_multiple_distinct_signatures_are_ambiguous(self):
        identity, _, _ = self.setup(); first, _ = _report(identity); second, _ = _report(identity, code_ref="other-certifier")
        with self.assertRaises(PublicationEligibilityRefusal): self.select([first, second])

    def test_M_equivalent_duplicates_are_authoritative(self):
        identity, _, _ = self.setup(); first, metrics = _report(identity); second = ("different-id", first[1], {**metrics, "coverage_manifest_ids": ["coverage-1"], "coverage_assertion_ids": ["assertion-1"]}, [], first[4], "2027-01-01T00:00:00Z")
        self.assertEqual(self.select([first, second]).signature, self.select([first]).signature)

    def test_N_missing_evidence_category_refuses(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, categories={"source": "pass", "canonical": "pass", "physical": "pass", "manifests": "pass"})
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_O_unknown_category_status_refuses(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, categories={"source": "pass", "canonical": "pass", "physical": "pass", "manifests": "pass", "coverage": "mystery"})
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_P_semantic_signature_excludes_report_runtime_metadata(self):
        identity, _, _ = self.setup(); first, _ = _report(identity); second = ("other-id", first[1], first[2], first[3], first[4], "2099-01-01T00:00:00Z")
        self.assertEqual(self.select([first, second]).signature, self.select([first]).signature)

    def test_Q_production_modules_have_no_source_or_physical_calls(self):
        root = Path(__file__).resolve().parents[1] / "src" / "quant_platform" / "data"
        for name in ("publication_eligibility.py", "publication_eligibility_catalog.py"):
            text = (root / name).read_text()
            tree = ast.parse(text)
            forbidden_imports = []
            forbidden_calls = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [alias.name for alias in node.names]
                    module = getattr(node, "module", "") or ""
                    forbidden_imports.extend(item for item in [module, *modules] if any(token in item.lower() for token in ("source_adapters", "bybit", "parquet", "materializer")))
                if isinstance(node, ast.Call):
                    function = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ""
                    if function in {"scan_trade_v1_all", "open", "physical_artifact_sha256", "PublicationCertification", "record_quality_report"}:
                        forbidden_calls.append(function)
                    if isinstance(node.func, ast.Attribute) and node.func.attr == "open":
                        forbidden_calls.append("Path.open")
            self.assertEqual([], forbidden_imports)
            self.assertEqual([], forbidden_calls)
            injected = ast.parse("import quant_platform.source_adapters.bybit\nPublicationCertification(x)\nPath.open(x)")
            injected_imports = []
            for node in ast.walk(injected):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    modules = [alias.name for alias in node.names]
                    module = getattr(node, "module", "") or ""
                    injected_imports.extend(item for item in [module, *modules] if "source_adapters" in item)
            injected_calls = [node for node in ast.walk(injected) if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == "PublicationCertification") or (isinstance(node.func, ast.Attribute) and node.func.attr == "open"))]
            self.assertTrue(injected_imports and len(injected_calls) == 2)

    def test_R_dataset_hash_mismatch_is_stale(self):
        identity, target, part = self.setup(); report, metrics = _report(identity)
        self.assertFalse(_current_report({**metrics, "dataset_manifest_sha256": "wrong"}, report[1], report[3], report[4], "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash", ["coverage-1"], ["assertion-1"], ["coverage-hash"], target))

    def test_S_natural_identity_mismatch_is_stale(self):
        identity, target, part = self.setup(); report, metrics = _report(identity)
        altered = {**metrics, "natural_partition_identity": {**metrics["natural_partition_identity"], "revision": 2}}
        self.assertFalse(_current_report(altered, report[1], report[3], report[4], "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash", ["coverage-1"], ["assertion-1"], ["coverage-hash"], target))

    def test_T_coverage_id_mismatch_is_stale(self):
        identity, target, part = self.setup(); report, metrics = _report(identity)
        self.assertFalse(_current_report({**metrics, "coverage_manifest_id": "old"}, report[1], report[3], report[4], "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash", ["coverage-1"], ["assertion-1"], ["coverage-hash"], target))

    def test_U_assertion_id_mismatch_is_stale(self):
        identity, target, part = self.setup(); report, metrics = _report(identity)
        self.assertFalse(_current_report({**metrics, "coverage_assertion_id": "old"}, report[1], report[3], report[4], "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash", ["coverage-1"], ["assertion-1"], ["coverage-hash"], target))

    def test_V_coverage_hash_mismatch_is_stale(self):
        identity, target, part = self.setup(); report, metrics = _report(identity)
        self.assertFalse(_current_report({**metrics, "coverage_manifest_sha256": ["old"]}, report[1], report[3], report[4], "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash", ["coverage-1"], ["assertion-1"], ["coverage-hash"], target))

    def test_W_missing_canonical_hash_is_stale(self):
        identity, target, part = self.setup(); report, metrics = _report(identity); altered = dict(metrics); altered.pop("canonical_content_hash_v1")
        self.assertFalse(_current_report(altered, report[1], report[3], report[4], "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash", ["coverage-1"], ["assertion-1"], ["coverage-hash"], target))

    def test_X_pass_with_warn_category_is_not_pass_authority(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, categories={"source": "warn", "canonical": "pass", "physical": "pass", "manifests": "pass", "coverage": "pass"})
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_Y_warn_with_non_source_violation_is_refused(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, status="warn", categories={"source": "warn", "canonical": "pass", "physical": "pass", "manifests": "pass", "coverage": "pass"}, violations=[{"category": "physical", "message": "bad"}])
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_Z_profile_mismatch_is_stale(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, profile="other-profile")
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_AA_no_current_report_is_refused(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, coverage_ids=["new-coverage"])
        with self.assertRaises(PublicationEligibilityRefusal): self.select([report])

    def test_AB_fail_state_mapping_is_closed(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, status="fail", categories={name: "fail" for name in ("source", "canonical", "physical", "manifests", "coverage")}, violations=[{"category": "physical"}])
        self.assertEqual(self.select([report]).status, "fail")
        with self.assertRaises(PublicationEligibilityRefusal): _eligible_state(self.select([report]))

    def test_AC_two_live_revisions_are_refused(self):
        identity, target, _ = self.setup(); second = list(target); second[0] = "partition-2"; second[3] = 2
        with self.assertRaises(PublicationEligibilityRefusal): _live_row([target, tuple(second)])

    def test_AD_superseded_target_is_not_live(self):
        identity, target, _ = self.setup(); old = list(target); old[4] = "superseded"; old[3] = 1; target = list(target); target[3] = 2; target = tuple(target)
        self.assertEqual(_live_row([tuple(old), target]), target)

    def test_AE_code_ref_is_certifier_identity(self):
        identity, _, _ = self.setup(); report, _ = _report(identity, code_ref="certifier-v2")
        self.assertEqual(self.select([report]).code_ref, "certifier-v2")

    def test_AF_set_like_coverage_order_does_not_change_signature(self):
        identity, target, part = self.setup()
        first, metrics = _report(identity, coverage_ids=["coverage-1", "coverage-2"], assertion_ids=["assertion-1", "assertion-2"], coverage_hashes=["a", "b"])
        reordered = {**metrics, "coverage_manifest_ids": ["coverage-2", "coverage-1"], "coverage_assertion_ids": ["assertion-2", "assertion-1"], "coverage_manifest_sha256": ["b", "a"]}
        second = ("different-id", first[1], reordered, first[3], first[4], "2027-01-01T00:00:00Z")
        selected = _select_authoritative_report([first, second], expected_profile="generic-profile", expected_check_suite="generic-suite", identity=identity, partition=part, dataset_sha256="dataset-hash", partition_sha256="partition-hash", coverage_ids=["coverage-1", "coverage-2"], assertion_ids=["assertion-1", "assertion-2"], coverage_sha256=["a", "b"], target=target)
        self.assertEqual(selected.signature, _select_authoritative_report([first], expected_profile="generic-profile", expected_check_suite="generic-suite", identity=identity, partition=part, dataset_sha256="dataset-hash", partition_sha256="partition-hash", coverage_ids=["coverage-1", "coverage-2"], assertion_ids=["assertion-1", "assertion-2"], coverage_sha256=["a", "b"], target=target).signature)

    def current(self, metrics, *, status="pass", violations=None, code_ref="certifier-commit",
                coverage_ids=("coverage-1",), assertion_ids=("assertion-1",), coverage_hashes=("coverage-hash",)):
        identity, target, part = self.setup()
        return _current_report(
            metrics, status, [] if violations is None else violations, code_ref,
            "generic-profile", "generic-suite", identity, part, "dataset-hash", "partition-hash",
            list(coverage_ids), list(assertion_ids), list(coverage_hashes), target,
        )

    def test_AH_complete_plural_coverage_metrics_remain_current(self):
        identity, _, _ = self.setup(); _, metrics = _report(identity)
        self.assertTrue(self.current(metrics))

    def test_AI_missing_plural_coverage_metrics_are_not_current(self):
        identity, _, _ = self.setup(); _, metrics = _report(identity)
        for key in ("coverage_manifest_ids", "coverage_assertion_ids", "coverage_manifest_sha256"):
            with self.subTest(missing=key):
                stripped = {name: value for name, value in metrics.items() if name != key}
                self.assertFalse(self.current(stripped))

    def test_AJ_reused_coverage_id_cannot_hide_changed_coverage_content(self):
        identity, _, _ = self.setup(); _, metrics = _report(identity)
        without_hashes = {name: value for name, value in metrics.items() if name != "coverage_manifest_sha256"}
        self.assertFalse(self.current(without_hashes))
        self.assertFalse(self.current({**metrics, "coverage_manifest_sha256": ["old-coverage-hash"]}))

    def test_AK_reused_coverage_id_with_changed_content_refuses_publication(self):
        identity, _, _ = self.setup()
        report, _ = _report(identity, coverage_hashes=["old-coverage-hash"])
        with self.assertRaises(PublicationEligibilityRefusal):
            self.select([report])

    def test_AL_subset_of_current_coverage_set_is_not_current(self):
        identity, _, _ = self.setup(); _, metrics = _report(identity)
        two = {"coverage_ids": ["coverage-1", "coverage-2"], "assertion_ids": ["assertion-1", "assertion-2"], "coverage_hashes": ["hash-1", "hash-2"]}
        singular_only = {name: value for name, value in metrics.items()
                         if name not in {"coverage_manifest_ids", "coverage_assertion_ids", "coverage_manifest_sha256"}}
        self.assertFalse(self.current(singular_only, **two))
        subset = {**metrics, "coverage_manifest_ids": ["coverage-1"], "coverage_assertion_ids": ["assertion-1"], "coverage_manifest_sha256": ["hash-1"]}
        self.assertFalse(self.current(subset, **two))
        complete = {**metrics, "coverage_manifest_ids": ["coverage-1", "coverage-2"], "coverage_assertion_ids": ["assertion-1", "assertion-2"], "coverage_manifest_sha256": ["hash-1", "hash-2"]}
        self.assertTrue(self.current(complete, **two))

    def test_AM_malformed_plural_coverage_metrics_fail_closed(self):
        identity, _, _ = self.setup(); _, metrics = _report(identity)
        for value in ({"coverage-1": True}, "coverage-1", None, 7):
            with self.subTest(value=value):
                self.assertFalse(self.current({**metrics, "coverage_manifest_ids": value}))

    def test_AN_subsecond_manifest_timestamps_match_microsecond_catalog(self):
        identity, target, part = self.setup()
        created, closed = "2026-09-01T10:00:00.123456789Z", "2026-09-01T10:00:01.987654321Z"
        partition = {**part, "created_at": created, "closed_at": closed}
        row = list(target)
        row[13] = Instant.parse("2026-09-01T10:00:00.123456Z").to_datetime()
        row[14] = Instant.parse("2026-09-01T10:00:01.987654Z").to_datetime()
        child = ("dataset",)
        PublicationEligibilityCatalog._verify_partition(
            tuple(row), child, identity, partition, "partition-hash",
            Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"), "hot",
        )

    def test_AO_different_microsecond_timestamp_still_refuses(self):
        identity, target, part = self.setup()
        partition = {**part, "created_at": "2026-09-01T10:00:00.123456789Z", "closed_at": "2026-09-01T10:00:01Z"}
        row = list(target)
        row[13] = Instant.parse("2026-09-01T10:00:00.123455Z").to_datetime()
        row[14] = Instant.parse("2026-09-01T10:00:01Z").to_datetime()
        with self.assertRaises(PublicationEligibilityRefusal):
            PublicationEligibilityCatalog._verify_partition(
                tuple(row), ("dataset",), identity, partition, "partition-hash",
                Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"), "hot",
            )

    def test_AG_real_catalog_publish_observes_lock_and_phase_order(self):
        identity = DatasetIdentity("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1")
        parent_a = DatasetIdentity("raw", "trades", "genericvenue", "BTC-USD", "trade-v1")
        parent_b = DatasetIdentity("raw", "trades", "genericvenue", "ETH-USD", "trade-v1")
        dataset = {"layer": "canonical", "dataset_kind": "trades", "venue": "genericvenue", "instrument": "BTC-USD", "record_schema_id": "trade-v1", "rel_root": "canonical/trades/genericvenue/BTC-USD/trade-v1", "derived_from": [parent_a.stable_dict(), parent_b.stable_dict()], "transform": "canonicalize-trades-v1"}
        child = ("child", "canonical", "trades", "genericvenue", "BTC-USD", "trade-v1", None, dataset["rel_root"], "dataset-hash")
        parent1 = ("parent-b", "raw", "trades", "genericvenue", "BTC-USD", "trade-v1", None, "raw/trades/genericvenue/BTC-USD/trade-v1", "parent-hash")
        parent2 = ("parent-a", "raw", "trades", "genericvenue", "ETH-USD", "trade-v1", None, "raw/trades/genericvenue/ETH-USD/trade-v1", "parent-hash")
        identity, target, partition = AuthorityTests().setup(); report, _ = _report(identity)
        target = list(target); target[1] = "child"; target = tuple(target)
        connection = OrderedConnection({("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1"): child, ("raw", "trades", "genericvenue", "BTC-USD", "trade-v1"): parent1, ("raw", "trades", "genericvenue", "ETH-USD", "trade-v1"): parent2}, [target], [report])
        from quant_platform.data.publication_eligibility_catalog import PublicationEligibilityCatalog
        result = PublicationEligibilityCatalog(connection).publish(dataset=dataset, dataset_sha256="dataset-hash", partition=partition, partition_sha256="partition-hash", coverage_start=Instant.parse("2024-01-15T00:00:00Z"), coverage_end=Instant.parse("2024-01-16T00:00:00Z"), coverage_ids=["coverage-1"], assertion_ids=["assertion-1"], coverage_sha256=["coverage-hash"], storage_root_id="hot", expected_profile="generic-profile", expected_check_suite="generic-suite")
        self.assertEqual(result.state, "valid")
        kinds = ["dataset_lock" if "FROM catalog.datasets" in item and "FOR UPDATE" in item else "partition_lock" if "FROM catalog.partitions" in item else "quality_select" if "FROM catalog.quality_reports" in item else "lineage_insert" if "INSERT INTO catalog.dataset_lineage" in item else "state_update" if item.startswith("UPDATE catalog.partitions") else "lineage_read" if item.startswith("SELECT parent_id::text") else "other" for item in connection.statements]
        first_partition = min(index for index, value in enumerate(kinds) if value == "partition_lock")
        self.assertLess(max(index for index, value in enumerate(kinds) if value == "dataset_lock"), first_partition)
        first_quality = min(index for index, value in enumerate(kinds) if value == "quality_select")
        self.assertLess(first_partition, first_quality)
        self.assertLess(first_quality, min(index for index, value in enumerate(kinds) if value == "lineage_insert"))
        self.assertLess(max(index for index, value in enumerate(kinds) if value == "lineage_insert"), min(index for index, value in enumerate(kinds) if value == "state_update"))
        state_update = max(index for index, value in enumerate(kinds) if value == "state_update")
        self.assertLess(state_update, max(index for index, value in enumerate(kinds) if value == "quality_select"))
        self.assertLess(state_update, max(index for index, value in enumerate(kinds) if value == "partition_lock"))
        self.assertLess(state_update, max(index for index, value in enumerate(kinds) if value == "lineage_read"))
        lock = next(item for item in connection.statements if "FROM catalog.datasets" in item and "FOR UPDATE" in item)
        self.assertIn("ORDER BY dataset_id::text", lock)


if __name__ == "__main__":
    unittest.main()

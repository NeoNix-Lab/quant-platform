"""Hermetic tests for the source-copy disposal service and tool (#425, ADR-0049 Amendment 1)."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import test_bybit_l2_day_ingest_v1 as fixtures  # noqa: E402
from quant_platform.application import l2_golden  # noqa: E402
from quant_platform.application.bybit_l2_day_ingest import ingest_bybit_l2_day  # noqa: E402
from quant_platform.application.retention_deletion import (  # noqa: E402
    InMemoryRetentionDeletionAuditStore,
    RetentionDeletionCrashPoint,
    RetentionDeletionInterrupted,
)
from quant_platform.application.source_copy_disposal import (  # noqa: E402
    CanonicalPartitionRef,
    SourceCopyDisposalApplicationError,
    SourceCopyDisposalOutcome,
    SourceCopyLocation,
    dispose_source_copy,
    l2_raw_dataset_identity,
    recover_source_copy_disposal_result,
)
from quant_platform.data.models import Instant  # noqa: E402

DAY = fixtures.DAY
NAME = fixtures.archive_file_name(DAY)
TIME = "2026-10-17T12:00:00Z"
FINALIZED = "2026-10-01T00:00:00Z"


def sha_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LoggingStore(InMemoryRetentionDeletionAuditStore):
    """In-memory audit store that remembers the order of events and what was on disk."""

    def __init__(self, probe_path: Path | None = None) -> None:
        super().__init__()
        self.events: list[tuple[str, bool]] = []
        self.probe_path = probe_path

    def record_decision(self, decision) -> None:
        self.events.append(("decision", self.probe_path.exists() if self.probe_path else False))
        super().record_decision(decision)

    def record_result(self, result) -> None:
        self.events.append(("result", self.probe_path.exists() if self.probe_path else False))
        self.results[result.deletion_decision_id] = result.stable_dict()


class Base(unittest.TestCase):
    def setUp(self):
        self._holder = tempfile.TemporaryDirectory()
        self.addCleanup(self._holder.cleanup)
        self.tmp = Path(self._holder.name)
        self.hot = self.tmp / "hot"
        self.cold = self.tmp / "cold"
        (self.hot / "archive").mkdir(parents=True)
        (self.cold / "archive").mkdir(parents=True)
        made = fixtures.make_archive(self.tmp, DAY, fixtures.CLEAN)
        self.candidate_file = self.hot / "archive" / NAME
        shutil.move(str(made), self.candidate_file)
        self.surviving_file = self.cold / "archive" / NAME
        shutil.copyfile(self.candidate_file, self.surviving_file)
        self.sha = sha_of(self.candidate_file)
        self.size = self.candidate_file.stat().st_size
        self.dataset_root = self.tmp / "ds"
        self.ingested = ingest_bybit_l2_day(
            self.candidate_file, day=DAY, dataset_root=self.dataset_root,
            created_at="2026-10-17T10:00:00Z", closed_at="2026-10-17T10:00:01Z",
            producer="test", code_ref="test",
        )
        self.partitions = tuple(
            CanonicalPartitionRef(
                partition_key=p.partition_key, revision=1,
                catalog_partition_id="11111111-1111-4111-8111-11111111111%d" % i,
                catalog_state="valid", catalog_content_sha256=sha_of(p.artifact_path),
                catalog_byte_size=p.artifact_path.stat().st_size, artifact_path=p.artifact_path,
            )
            for i, p in enumerate(self.ingested.partitions)
        )
        self.store = LoggingStore(self.candidate_file)

    def candidate(self) -> SourceCopyLocation:
        return SourceCopyLocation("hot", self.hot, f"archive/{NAME}")

    def surviving_location(self, root_id="cold", root=None, rel=None) -> SourceCopyLocation:
        return SourceCopyLocation(root_id, root or self.cold, rel or f"archive/{NAME}")

    def dispose(self, **overrides):
        params = dict(
            dataset_identity=l2_raw_dataset_identity("BTCUSDT"), candidate=self.candidate(),
            surviving=self.surviving_location(), candidate_sha256=self.sha, candidate_byte_size=self.size,
            canonical_partitions=self.partitions, authority="maintainer", decision_time=TIME,
            producer="test", code_ref="abc123", finalized_at=FINALIZED, audit_store=self.store,
            clock=lambda: Instant.parse(TIME),
        )
        params.update(overrides)
        return dispose_source_copy(**params)

    def decision_document(self):
        self.assertEqual(len(self.store.decisions), 1)
        return next(iter(self.store.decisions.values()))


class PermittedTests(Base):
    def test_removes_only_the_candidate_and_audits_before_and_after(self):
        result = self.dispose()
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.REMOVED)
        self.assertTrue(result.deleted)
        self.assertFalse(self.candidate_file.exists())
        self.assertTrue(self.surviving_file.exists())
        self.assertEqual(sha_of(self.surviving_file), self.sha)
        document = self.decision_document()
        self.assertEqual(document["decision"], "PERMITTED")
        self.assertEqual(document["decision_kind"], "source_copy_disposal")
        self.assertEqual(self.store.results[result.decision_identity]["outcome"], "removed")

    def test_the_decision_audit_is_persisted_before_any_byte_is_removed(self):
        self.dispose()
        # the candidate still existed when the decision was persisted and was gone at the result
        self.assertEqual(self.store.events, [("decision", True), ("result", False)])

    def test_the_audit_document_carries_the_evidence_it_decided_on(self):
        self.dispose()
        document = self.decision_document()
        self.assertEqual(document["surviving_copy"]["storage_root_id"], "cold")
        self.assertEqual(document["surviving_copy"]["content_sha256"], self.sha)
        self.assertEqual(document["protection_evidence"]["protection_state"], "PROTECTED")
        self.assertEqual(len(document["canonical_verifications"]), len(self.partitions))
        self.assertTrue(all(v["binds_source_archive"] for v in document["canonical_verifications"]))

    def test_candidate_already_absent_touches_nothing_and_audits_nothing(self):
        self.candidate_file.unlink()
        result = self.dispose()
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.ALREADY_ABSENT)
        self.assertEqual(self.store.events, [])
        self.assertTrue(self.surviving_file.exists())


class RefusalTests(Base):
    def assertRefusedUntouched(self, result, *reasons):
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.REFUSED)
        self.assertFalse(result.deleted)
        self.assertTrue(self.candidate_file.exists(), "a refused disposal must not remove the candidate")
        for reason in reasons:
            self.assertIn(reason, result.refusal_reasons)
        self.assertEqual(self.decision_document()["decision"], "REFUSED")
        self.assertEqual(self.store.results[result.decision_identity]["outcome"], "refused")

    def test_corrupted_installed_canonical_bytes_refuse_while_manifests_stay_intact(self):
        artifact = self.partitions[0].artifact_path
        artifact.write_bytes(b"CORRUPT")
        manifest_before = self.ingested.partitions[0].partition_manifest_path.read_bytes()
        result = self.dispose()
        self.assertRefusedUntouched(result, "canonical_bytes_mismatch")
        self.assertEqual(self.ingested.partitions[0].partition_manifest_path.read_bytes(), manifest_before)

    def test_missing_installed_canonical_artifact_refuses(self):
        self.partitions[0].artifact_path.unlink()
        self.assertRefusedUntouched(self.dispose(), "canonical_bytes_mismatch")

    def test_partition_that_is_not_valid_refuses(self):
        refs = (self.partitions[0].__class__(**{**_fields(self.partitions[0]), "catalog_state": "closed"}),)
        self.assertRefusedUntouched(self.dispose(canonical_partitions=refs), "canonical_partition_not_valid")

    def test_partition_not_bound_to_this_archive_refuses(self):
        manifest = self.partitions[0].coverage_manifest_path
        text = manifest.read_text(encoding="utf-8").replace(self.sha, "0" * 64)
        manifest.write_text(text, encoding="utf-8")
        self.assertRefusedUntouched(self.dispose(), "canonical_not_bound_to_archive")

    def test_no_named_partition_refuses(self):
        self.assertRefusedUntouched(self.dispose(canonical_partitions=()), "missing_canonical_verification")

    def test_missing_surviving_copy_refuses(self):
        self.surviving_file.unlink()
        self.assertRefusedUntouched(self.dispose(), "missing_surviving_copy", "protection_not_protected")

    def test_corrupted_surviving_copy_refuses(self):
        self.surviving_file.write_bytes(b"damaged copy")
        self.assertRefusedUntouched(self.dispose(), "surviving_copy_identity_mismatch", "protection_not_protected")

    def test_surviving_copy_in_the_same_root_refuses(self):
        other = self.hot / "backup" / NAME
        other.parent.mkdir()
        shutil.copyfile(self.candidate_file, other)
        result = self.dispose(surviving=self.surviving_location("hot", self.hot, f"backup/{NAME}"))
        self.assertRefusedUntouched(result, "surviving_copy_same_root")

    def test_naming_the_candidate_as_its_own_copy_refuses(self):
        result = self.dispose(surviving=self.surviving_location("cold", self.hot, f"archive/{NAME}"))
        self.assertRefusedUntouched(result, "surviving_copy_is_candidate")

    def test_a_hard_link_to_the_candidate_is_not_an_independent_copy(self):
        self.surviving_file.unlink()
        try:
            (self.cold / "archive" / NAME).hardlink_to(self.candidate_file)
        except OSError:
            self.skipTest("hard links are not available here")
        self.assertRefusedUntouched(self.dispose(), "surviving_copy_is_candidate")

    def test_a_candidate_whose_bytes_differ_from_the_pinned_identity_refuses(self):
        self.candidate_file.write_bytes(b"not the pinned archive")
        self.assertRefusedUntouched(self.dispose(), "candidate_identity_mismatch")

    def test_decision_time_far_from_the_observations_refuses_as_stale(self):
        self.assertRefusedUntouched(self.dispose(decision_time="2026-10-18T12:00:00Z"), "evidence_stale")

    def test_authority_must_be_explicit(self):
        with self.assertRaises(Exception):
            self.dispose(authority="  ")
        self.assertTrue(self.candidate_file.exists())
        with self.assertRaises(TypeError):
            dispose_source_copy(dataset_identity=l2_raw_dataset_identity("BTCUSDT"))  # type: ignore[call-arg]

    def test_candidate_path_cannot_escape_its_root(self):
        with self.assertRaises(SourceCopyDisposalApplicationError):
            self.dispose(candidate=SourceCopyLocation("hot", self.hot, "../cold/archive/" + NAME))
        self.assertTrue(self.surviving_file.exists())


class FailureAndCrashTests(Base):
    def test_unlink_failure_is_reported_not_raised_and_the_audit_is_intact(self):
        real_unlink = Path.unlink

        def failing(path, *args, **kwargs):
            if Path(path) == self.candidate_file.resolve() or Path(path) == self.candidate_file:
                raise PermissionError("locked")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", failing):
            result = self.dispose()
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.FAILED)
        self.assertTrue(result.failure_reason.startswith("unlink_failed"))
        self.assertFalse(result.deleted)
        self.assertTrue(self.candidate_file.exists())
        self.assertEqual(self.decision_document()["decision"], "PERMITTED")
        self.assertEqual(self.store.results[result.decision_identity]["outcome"], "failed")
        self.assertTrue(all(p.artifact_path.exists() for p in self.ingested.partitions))

    def test_evidence_that_cannot_be_made_durable_deletes_nothing(self):
        def broken(path):
            raise OSError("no durable flush")

        result = self.dispose(flush=broken)
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.FAILED)
        self.assertTrue(result.failure_reason.startswith("flush_failed"))
        self.assertTrue(self.candidate_file.exists())

    def test_the_flushed_set_is_the_surviving_copy_and_every_canonical_artifact(self):
        seen = []
        self.dispose(flush=seen.append)
        self.assertEqual(
            sorted(map(str, seen)),
            sorted(map(str, [self.surviving_file.resolve(), *(p.artifact_path for p in self.partitions)])),
        )

    def test_surviving_copy_damaged_after_the_decision_deletes_nothing(self):
        def damage_after_flush(path):
            if Path(path) == self.surviving_file.resolve():
                self.surviving_file.write_bytes(b"damaged between decision and delete")

        result = self.dispose(flush=damage_after_flush)
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.FAILED)
        self.assertEqual(result.failure_reason, "surviving_copy_changed_after_decision")
        self.assertTrue(self.candidate_file.exists())

    def test_canonical_bytes_damaged_after_the_decision_deletes_nothing(self):
        artifact = self.partitions[0].artifact_path.resolve()

        def damage_after_flush(path):
            if Path(path) == artifact:
                artifact.write_bytes(b"CORRUPT")

        result = self.dispose(flush=damage_after_flush)
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.FAILED)
        self.assertTrue(result.failure_reason.startswith("canonical_bytes_changed_after_decision"))
        self.assertTrue(self.candidate_file.exists())

    def test_candidate_changed_after_the_decision_deletes_nothing(self):
        def change_candidate(path):
            self.candidate_file.write_bytes(b"swapped after the decision")

        result = self.dispose(flush=change_candidate)
        self.assertEqual(result.outcome, SourceCopyDisposalOutcome.FAILED)
        self.assertEqual(result.failure_reason, "candidate_changed_after_decision")
        self.assertTrue(self.candidate_file.exists())

    def test_crash_after_the_decision_leaves_the_copy_and_a_rerun_removes_it(self):
        with self.assertRaises(RetentionDeletionInterrupted):
            self.dispose(crash_after=RetentionDeletionCrashPoint.AFTER_DECISION_PERSISTED)
        self.assertTrue(self.candidate_file.exists())
        self.assertEqual(self.store.results, {})
        again = self.dispose()
        self.assertEqual(again.outcome, SourceCopyDisposalOutcome.REMOVED)
        self.assertEqual(len(self.store.decisions), 1, "the re-evaluation reuses the same decision identity")

    def test_crash_after_removal_is_recovered_against_the_same_decision(self):
        with self.assertRaises(RetentionDeletionInterrupted):
            self.dispose(crash_after=RetentionDeletionCrashPoint.AFTER_BYTES_DELETED)
        self.assertFalse(self.candidate_file.exists())
        self.assertEqual(self.store.results, {})
        decision_id, document = next(iter(self.store.decisions.items()))
        recovered = recover_source_copy_disposal_result(
            decision_document=document, candidate=self.candidate(), audit_store=self.store,
            deleted_at=TIME, surviving=self.surviving_location(),
        )
        self.assertEqual(recovered.outcome, SourceCopyDisposalOutcome.REMOVED)
        self.assertEqual(recovered.decision_identity, decision_id)
        self.assertIn("surviving copy verified", recovered.detail)
        self.assertEqual(self.store.results[decision_id]["outcome"], "removed")
        self.assertEqual(len(self.store.decisions), 1, "no new unrelated decision explains the old removal")

    def test_recovery_notes_when_the_surviving_copy_no_longer_verifies(self):
        with self.assertRaises(RetentionDeletionInterrupted):
            self.dispose(crash_after=RetentionDeletionCrashPoint.AFTER_BYTES_DELETED)
        self.surviving_file.write_bytes(b"damaged after the crash")
        document = next(iter(self.store.decisions.values()))
        recovered = recover_source_copy_disposal_result(
            decision_document=document, candidate=self.candidate(), audit_store=self.store,
            deleted_at=TIME, surviving=self.surviving_location(),
        )
        self.assertIn("NOT verified", recovered.detail)

    def test_recovery_refuses_a_refused_decision_a_present_candidate_and_a_foreign_candidate(self):
        refused = self.dispose(canonical_partitions=())
        self.assertEqual(refused.outcome, SourceCopyDisposalOutcome.REFUSED)
        refused_document = next(iter(self.store.decisions.values()))
        with self.assertRaises(SourceCopyDisposalApplicationError):
            recover_source_copy_disposal_result(
                decision_document=refused_document, candidate=self.candidate(), audit_store=self.store, deleted_at=TIME)

        self.store = LoggingStore(self.candidate_file)
        with self.assertRaises(RetentionDeletionInterrupted):
            self.dispose(crash_after=RetentionDeletionCrashPoint.AFTER_DECISION_PERSISTED)
        document = next(iter(self.store.decisions.values()))
        with self.assertRaises(SourceCopyDisposalApplicationError):  # candidate still on disk
            recover_source_copy_disposal_result(
                decision_document=document, candidate=self.candidate(), audit_store=self.store, deleted_at=TIME)
        self.candidate_file.unlink()
        foreign = SourceCopyLocation("hot", self.hot, "archive/another.zip")
        with self.assertRaises(SourceCopyDisposalApplicationError):  # decision names a different file
            recover_source_copy_disposal_result(
                decision_document=document, candidate=foreign, audit_store=self.store, deleted_at=TIME)
        with self.assertRaises(SourceCopyDisposalApplicationError):  # not a disposal decision
            recover_source_copy_disposal_result(
                decision_document={**document, "decision_kind": "k09"}, candidate=self.candidate(),
                audit_store=self.store, deleted_at=TIME)


def _fields(ref: CanonicalPartitionRef) -> dict:
    return {name: getattr(ref, name) for name in ref.__dataclass_fields__}


class IngestStaysNonDestructiveTests(unittest.TestCase):
    def test_no_delete_or_cleanup_parameter_on_ingest_or_golden(self):
        for function in (ingest_bybit_l2_day, l2_golden.run_l2_golden):
            names = set(inspect.signature(function).parameters)
            self.assertFalse({n for n in names if "cleanup" in n or "delete" in n or "dispose" in n}, (function, names))

    def test_ingest_and_golden_never_call_unlink_on_the_archive(self):
        archive_sources = [
            Path(inspect.getsourcefile(ingest_bybit_l2_day)).read_text(encoding="utf-8"),
            Path(inspect.getsourcefile(l2_golden)).read_text(encoding="utf-8"),
        ]
        for source in archive_sources:
            self.assertNotIn("archive_path).unlink", source)
            self.assertNotIn("cleanup_source", source)


def load_tool():
    spec = importlib.util.spec_from_file_location("dispose_source_copy_tool", ROOT / "tools" / "dispose_source_copy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolTests(Base):
    """The CLI wiring with the catalog and PostgreSQL seams replaced (the PG leg is integration)."""

    def args(self, **overrides):
        base = {
            "--dsn": "host=unused", "--candidate-root-id": "hot", "--candidate-root": str(self.hot),
            "--candidate-rel-path": f"archive/{NAME}", "--surviving-root-id": "cold",
            "--surviving-root": str(self.cold), "--surviving-rel-path": f"archive/{NAME}",
            "--source-sha256": self.sha, "--source-bytes": str(self.size), "--authority": "maintainer",
            "--decision-time": "NOW", "--finalized-at": FINALIZED, "--producer": "tool-test", "--code-ref": "abc123",
        }
        base.update(overrides)
        argv = []
        for key, value in base.items():
            if value is not None:
                argv += [key, value]
        for p in self.partitions:
            argv += ["--partition", p.partition_key]
        return argv

    def run_tool(self, argv, *, partitions=None, store=None):
        tool = load_tool()
        out, err = io.StringIO(), io.StringIO()
        fake_connection = mock.MagicMock()
        store = store or self.store
        with mock.patch("psycopg.connect", return_value=fake_connection), \
                mock.patch.object(tool, "read_catalog_canonical_partitions",
                                  return_value=self.partitions if partitions is None else partitions), \
                mock.patch.object(tool, "PostgresRetentionDeletionAuditStore", return_value=store), \
                mock.patch.object(tool, "dispose_source_copy", wraps=dispose_with_clock(tool)):
            code = tool.run(argv, out=out, err=err)
        return code, out.getvalue(), err.getvalue(), fake_connection

    def test_removed_exits_zero_and_reports_the_real_outcome(self):
        now = Instant.parse(TIME)
        argv = self.args(**{"--decision-time": TIME})
        code, out, _, connection = self.run_tool(argv)
        payload = json.loads(out)
        self.assertEqual(code, 0, out)
        self.assertEqual(payload["outcome"], "removed")
        self.assertTrue(payload["deleted"])
        self.assertFalse(self.candidate_file.exists())
        self.assertTrue(self.surviving_file.exists())
        self.assertEqual(now, Instant.parse(TIME))
        connection.close.assert_called_once()

    def test_refusal_exits_two_and_reports_reasons_without_touching_the_copy(self):
        self.surviving_file.unlink()
        code, out, _, _ = self.run_tool(self.args(**{"--decision-time": TIME}))
        payload = json.loads(out)
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["outcome"], "refused")
        self.assertIn("missing_surviving_copy", payload["refusal_reasons"])
        self.assertTrue(self.candidate_file.exists())

    def test_permitted_but_not_removed_exits_three(self):
        def broken(path):
            raise OSError("no flush")

        tool_flush = mock.patch("quant_platform.application.source_copy_disposal._flush_file", broken)
        with tool_flush:
            code, out, _, _ = self.run_tool(self.args(**{"--decision-time": TIME}))
        payload = json.loads(out)
        self.assertEqual(code, 3)
        self.assertEqual(payload["outcome"], "failed")
        self.assertTrue(payload["failure_reason"].startswith("flush_failed"))
        self.assertTrue(self.candidate_file.exists())

    def test_already_absent_exits_zero(self):
        self.candidate_file.unlink()
        code, out, _, _ = self.run_tool(self.args(**{"--decision-time": TIME}))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["outcome"], "already_absent")

    def test_catalog_or_request_errors_exit_one_and_delete_nothing(self):
        tool = load_tool()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("psycopg.connect", return_value=mock.MagicMock()), \
                mock.patch.object(tool, "read_catalog_canonical_partitions",
                                  side_effect=SourceCopyDisposalApplicationError("partition(s) not in the catalog: x")):
            code = tool.run(self.args(**{"--decision-time": TIME}), out=out, err=err)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])
        self.assertTrue(self.candidate_file.exists())

    def test_provenance_and_the_second_copy_are_required_arguments(self):
        tool = load_tool()
        for flag in ("--authority", "--decision-time", "--producer", "--code-ref", "--finalized-at",
                     "--surviving-root", "--surviving-rel-path", "--surviving-root-id", "--source-sha256",
                     "--source-bytes", "--dsn"):
            argv = self.args(**{flag: None})
            with self.subTest(flag=flag), mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    tool.run(argv, out=io.StringIO(), err=io.StringIO())
                self.assertEqual(caught.exception.code, 2)
        self.assertTrue(self.candidate_file.exists())

    def test_the_tool_goes_through_the_application_seam_only(self):
        source = (ROOT / "tools" / "dispose_source_copy.py").read_text(encoding="utf-8")
        imports = [line for line in source.splitlines() if line.startswith("from quant_platform")]
        self.assertTrue(imports)
        for line in imports:
            self.assertIn("from quant_platform.application.", line)


def dispose_with_clock(tool):
    """Run the real service with a deterministic clock so the explicit decision time is fresh."""

    real = dispose_source_copy

    def call(**kwargs):
        kwargs.setdefault("clock", lambda: Instant.parse(TIME))
        return real(**kwargs)

    return call


if __name__ == "__main__":
    unittest.main()

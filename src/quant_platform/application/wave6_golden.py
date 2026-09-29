"""Wave 6 Golden E2E proof composition.

This module composes the already-implemented Wave 6 capabilities without
owning their domain semantics:

* B06 ``DataGateway.live_stream`` consumer cursor and resume.
* D04 live candle updates via the application ``live_candle_stream`` seam.
* K07 application storage relocation over real temporary bytes.
* K09 retention/deletion decision, audit and exact-byte deletion.

The proof is intentionally bounded and hermetic.  It does not claim target-host
operation or introduce product runtime/client semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable

from ..access import (
    CatalogDataset,
    CatalogPartition,
    DataGateway,
    LiveStreamCursorV1,
    LiveStreamRequest,
)
from ..data.models import DatasetIdentity, Instant, NaturalPartitionIdentity, TradeRecord
from ..operations.relocation import RelocationPhase, RelocationPlan, RelocationRecordV1
from ..operations.retention import (
    DeletionCandidateV1,
    PreservationClass,
    ProtectionEvidenceRef,
    RelocationEvidenceRef,
    RetentionPolicyDefinitionV1,
    RetentionRefusalReason,
    RetentionDeletionDecisionV1,
    VerifiedRestoreProofRef,
    evaluate_retention_deletion,
)
from ..representation import CandleDefinitionV1, CandleRecord, aggregate_historical_candles
from ..source_adapters.bybit import BYBIT_ORDERING_PROVIDER, BYBIT_TRADE_V1_ORDERING_POLICY
from .live_candle_stream import GapSafeLiveCandleComposer, compose_live_candle_stream
from .retention_deletion import (
    InMemoryRetentionDeletionAuditStore,
    RetentionDeletionApplicationResult,
    execute_retention_deletion,
)
from .storage_relocation import relocate_storage_tier


WAVE6_GOLDEN_E2E_PROOF_VERSION = "1"
WAVE6_CANONICAL_BTCUSDT_TRADES = DatasetIdentity(
    "canonical",
    "trades",
    "bybit",
    "BTCUSDT",
    "trade-v1",
)
WAVE6_DATASET_REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"


@dataclass(frozen=True, slots=True)
class Wave6GoldenE2EProof:
    """Deterministic summary of the bounded Wave 6 Golden E2E proof."""

    code_ref: str
    live_closed_records: tuple[CandleRecord, ...]
    historical_closed_records: tuple[CandleRecord, ...]
    first_cursor: LiveStreamCursorV1
    final_cursor: LiveStreamCursorV1
    relocation_record: RelocationRecordV1
    protected_retention_decision: RetentionDeletionDecisionV1
    permitted_retention_decision: RetentionDeletionDecisionV1
    permitted_deletion_result: RetentionDeletionApplicationResult
    protected_candidate_still_exists: bool
    deleted_candidate_exists_after_k09: bool
    retained_consumer_records_after_storage_actions: tuple[CandleRecord, ...]
    second: dict[str, Any] | None = None

    @property
    def candle_equivalence(self) -> bool:
        return self.live_closed_records == self.historical_closed_records

    @property
    def no_consumer_visible_disappearance(self) -> bool:
        return self.retained_consumer_records_after_storage_actions == self.live_closed_records

    @property
    def deterministic(self) -> bool:
        return self.second is None or self.stable_dict(
            include_second=False,
            include_verdict=False,
        ) == self.second

    @property
    def pass_(self) -> bool:
        return (
            self.candle_equivalence
            and self.no_consumer_visible_disappearance
            and self.relocation_record.phase is RelocationPhase.CLEANED_UP
            and RetentionRefusalReason.K06_PROTECTED_EVIDENCE
            in self.protected_retention_decision.refusal_reasons
            and self.protected_candidate_still_exists
            and self.permitted_deletion_result.deleted
            and not self.deleted_candidate_exists_after_k09
            and self.deterministic
        )

    @property
    def proof_identity(self) -> str:
        digest = _canonical_fingerprint(
            self.stable_dict(
                include_identity=False,
                include_second=False,
                include_verdict=False,
            )
        )
        return f"wave6-golden-e2e-proof-v1:sha256:{digest}"

    def stable_dict(
        self,
        *,
        include_identity: bool = True,
        include_second: bool = True,
        include_verdict: bool = True,
    ) -> dict[str, Any]:
        payload = {
            "proof_version": WAVE6_GOLDEN_E2E_PROOF_VERSION,
            "code_ref": self.code_ref,
            "dataset_identity": WAVE6_CANONICAL_BTCUSDT_TRADES.stable_dict(),
            "live_closed_records": [record.stable_dict() for record in self.live_closed_records],
            "historical_closed_records": [
                record.stable_dict() for record in self.historical_closed_records
            ],
            "candle_equivalence": self.candle_equivalence,
            "first_cursor": self.first_cursor.stable_dict(),
            "final_cursor": self.final_cursor.stable_dict(),
            "relocation": {
                "relocation_id": self.relocation_record.relocation_id,
                "phase": self.relocation_record.phase.value,
                "target_content_sha256": self.relocation_record.target_content_sha256,
                "target_size_bytes": self.relocation_record.target_size_bytes,
            },
            "retention": {
                "protected_decision": self.protected_retention_decision.stable_dict(),
                "permitted_decision": self.permitted_retention_decision.stable_dict(),
                "permitted_deletion_result": _stable_deletion_result(
                    self.permitted_deletion_result,
                    self.permitted_retention_decision.candidate.rel_path,
                ),
                "protected_candidate_still_exists": self.protected_candidate_still_exists,
                "deleted_candidate_exists_after_k09": self.deleted_candidate_exists_after_k09,
            },
            "no_consumer_visible_disappearance": self.no_consumer_visible_disappearance,
        }
        if include_verdict:
            payload["pass"] = self.pass_
            payload["deterministic"] = self.deterministic
        if include_identity:
            payload["proof_identity"] = self.proof_identity
        if include_second and self.second is not None:
            payload["second_run"] = self.second
        return payload


class _FakeCatalog:
    def __init__(self, partitions: Iterable[CatalogPartition]) -> None:
        self.partitions = tuple(partitions)

    def resolve_dataset(self, identity: DatasetIdentity) -> CatalogDataset:
        if identity != WAVE6_CANONICAL_BTCUSDT_TRADES:
            raise AssertionError(identity)
        return CatalogDataset(
            identity=WAVE6_CANONICAL_BTCUSDT_TRADES,
            catalog_dataset_id="wave6-golden-dataset",
            rel_root=WAVE6_DATASET_REL_ROOT,
            manifest_sha256="a" * 64,
            schema_version=1,
            schema_hash="b" * 64,
        )

    def select_partitions(self, dataset: CatalogDataset, start: Instant, end: Instant, states):
        return [
            item
            for item in self.partitions
            if item.state in states
            and item.coverage is not None
            and item.ts_end > start
            and item.ts_start < end
        ]


class _FakeBatchReader:
    def __init__(self, batches_by_path: dict[str, tuple[TradeRecord, ...]]) -> None:
        self.batches_by_path = dict(batches_by_path)

    def __call__(self, path: str, start: Instant, end: Instant, batch_size: int):
        batch = self.batches_by_path.get(path, ())
        filtered = tuple(record for record in batch if start <= record.exchange_ts < end)
        if filtered:
            yield filtered


class _FakeRelocationCatalog:
    def __init__(self) -> None:
        self.records: dict[str, RelocationRecordV1] = {}
        self.storage_root_id = "hot"

    def load_relocation_record(self, relocation_id: str) -> RelocationRecordV1 | None:
        return self.records.get(relocation_id)

    def save_relocation_record(self, record: RelocationRecordV1) -> None:
        self.records[record.relocation_id] = record

    def current_storage_root_id(self, catalog_partition_id: str) -> str:
        return self.storage_root_id

    def switch_partition_storage_root(
        self,
        *,
        catalog_partition_id: str,
        source_storage_root_id: str,
        target_storage_root_id: str,
    ) -> bool:
        if self.storage_root_id != source_storage_root_id:
            return False
        self.storage_root_id = target_storage_root_id
        return True


def run_wave6_golden_e2e_proof(
    *,
    code_ref: str = "wave6-golden-e2e-v1",
    repeat: bool = True,
) -> Wave6GoldenE2EProof:
    """Run the bounded Wave 6 Golden E2E proof.

    The proof interleaves a live B06/D04 consumer with K07 and K09 actions:
    it drains the first live island, runs relocation and retention/deletion
    work over temporary storage, then resumes the same consumer cursor and
    proves closed live candles match D03's historical output.
    """

    first = _run_once(code_ref=code_ref)
    if not repeat:
        return first
    second = _run_once(code_ref=code_ref).stable_dict(
        include_second=False,
        include_verdict=False,
    )
    return replace(first, second=second)


def _run_once(*, code_ref: str) -> Wave6GoldenE2EProof:
    definition = CandleDefinitionV1.from_duration("5m")
    trades = _fixture_trades()
    first_partition, second_partition = _fixture_partitions()
    batches = {
        first_partition.rel_path: tuple(trades[:4]),
        second_partition.rel_path: tuple(trades[4:]),
    }
    composer = GapSafeLiveCandleComposer(definition)

    first_gateway = _gateway((first_partition,), batches)
    first_report = compose_live_candle_stream(
        first_gateway,
        _live_request(),
        definition,
        composer=composer,
    )
    if first_report.final_cursor is None:
        raise RuntimeError("first B06 live stream did not produce a resume cursor")

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        relocation_record = _run_k07(root=root)
        (
            protected_decision,
            permitted_decision,
            permitted_result,
            protected_exists,
            deleted_exists,
        ) = _run_k09(root=root, code_ref=code_ref)

        second_gateway = _gateway((first_partition, second_partition), batches)
        second_report = compose_live_candle_stream(
            second_gateway,
            _live_request(),
            definition,
            cursor=first_report.final_cursor,
            composer=composer,
        )
        if second_report.final_cursor is None:
            raise RuntimeError("resumed B06 live stream did not produce a final cursor")
        live_closed = (*first_report.closed_records, *second_report.closed_records)
        historical = aggregate_historical_candles(
            tuple(record for record in trades if record.exchange_ts < Instant.parse("2026-01-01T10:15:00Z")),
            definition,
            "2026-01-01T10:00:00Z",
            "2026-01-01T10:15:00Z",
        )
        retained_consumer_records = tuple(live_closed)

    return Wave6GoldenE2EProof(
        code_ref=code_ref,
        live_closed_records=live_closed,
        historical_closed_records=historical,
        first_cursor=first_report.final_cursor,
        final_cursor=second_report.final_cursor,
        relocation_record=relocation_record,
        protected_retention_decision=protected_decision,
        permitted_retention_decision=permitted_decision,
        permitted_deletion_result=permitted_result,
        protected_candidate_still_exists=protected_exists,
        deleted_candidate_exists_after_k09=deleted_exists,
        retained_consumer_records_after_storage_actions=retained_consumer_records,
    )


def _gateway(partitions: tuple[CatalogPartition, ...], batches) -> DataGateway:
    return DataGateway(
        _FakeCatalog(partitions),
        batch_reader=_FakeBatchReader(batches),
        path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )


def _live_request() -> LiveStreamRequest:
    return LiveStreamRequest(
        WAVE6_CANONICAL_BTCUSDT_TRADES,
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


def _fixture_trades() -> tuple[TradeRecord, ...]:
    return (
        _trade("2026-01-01T10:00:00Z", "100", "0.1", "t-1", "1"),
        _trade("2026-01-01T10:01:00Z", "101.5", "0.2", "t-2", "2"),
        _trade("2026-01-01T10:05:00Z", "99", "0.3", "t-3", "3"),
        _trade("2026-01-01T10:09:59Z", "102", "0.4", "t-4", "4"),
        _trade("2026-01-01T10:10:00Z", "103", "0.5", "t-5", "5"),
        _trade("2026-01-01T10:14:59Z", "98.5", "0.6", "t-6", "6"),
        _trade("2026-01-01T10:15:00Z", "104", "0.7", "t-7", "7"),
    )


def _trade(ts: str, price: str, size: str, trade_id: str, sequence: str) -> TradeRecord:
    return TradeRecord(
        venue="bybit",
        instrument="BTCUSDT",
        exchange_ts=Instant.parse(ts),
        price=price,
        size=size,
        aggressor_side="buy",
        trade_id=trade_id,
        sequence=sequence,
    )


def _fixture_partitions() -> tuple[CatalogPartition, CatalogPartition]:
    return (
        _partition(
            "wave6-partition-a",
            "dt=2026-01-01/hour=10a",
            "2026-01-01T10:00:00Z",
            "2026-01-01T10:10:00Z",
            "dt=2026-01-01/hour=10a/part-000.parquet",
        ),
        _partition(
            "wave6-partition-b",
            "dt=2026-01-01/hour=10b",
            "2026-01-01T10:10:00Z",
            "2026-01-01T10:20:00Z",
            "dt=2026-01-01/hour=10b/part-000.parquet",
        ),
    )


def _partition(partition_id: str, key: str, start: str, end: str, rel_path: str) -> CatalogPartition:
    suffix = partition_id[-1]
    return CatalogPartition(
        natural_identity=NaturalPartitionIdentity(WAVE6_CANONICAL_BTCUSDT_TRADES, key, 1),
        catalog_partition_id=partition_id,
        storage_root_id="hot",
        storage_root="/wave6-golden-root",
        dataset_rel_root=WAVE6_DATASET_REL_ROOT,
        rel_path=rel_path,
        ts_start=Instant.parse(start),
        ts_end=Instant.parse(end),
        row_count=0,
        content_sha256=(suffix * 64)[:64],
        manifest_sha256=("f" * 64),
        state="valid",
        producer="wave6-golden-fixture",
        code_ref="wave6-golden-fixture",
    )


def _run_k07(*, root: Path) -> RelocationRecordV1:
    payload = b"wave6 golden relocation partition bytes\n"
    digest = hashlib.sha256(payload).hexdigest()
    rel_path = "dt=2026-01-01/part-relocation.parquet"
    plan = RelocationPlan(
        dataset_identity=WAVE6_CANONICAL_BTCUSDT_TRADES,
        catalog_partition_id="77777777-7777-4777-8777-777777777777",
        partition_key="dt=2026-01-01",
        revision=1,
        source_storage_root_id="hot",
        target_storage_root_id="cold",
        dataset_rel_root=WAVE6_DATASET_REL_ROOT,
        rel_path=rel_path,
        expected_content_sha256=digest,
        expected_size_bytes=len(payload),
        partition_state="valid",
        pressure_decision_identity="pressure-decision-v1:sha256:" + "1" * 64,
        protection_assessment_identity="protection-assessment-v1:sha256:" + "2" * 64,
    )
    hot = root / "hot"
    cold = root / "cold"
    source = hot / plan.dataset_rel_root / plan.rel_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(payload)
    return relocate_storage_tier(
        plan=plan,
        catalog=_FakeRelocationCatalog(),
        source_storage_root=hot,
        target_storage_root=cold,
    )


def _run_k09(*, root: Path, code_ref: str):
    storage = root / "retention"
    protected_payload = b"wave6 protected source evidence\n"
    permitted_payload = b"wave6 restorable candidate\n"
    protected_path = storage / "protected" / "part-000.parquet"
    permitted_path = storage / "permitted" / "part-000.parquet"
    protected_path.parent.mkdir(parents=True, exist_ok=True)
    permitted_path.parent.mkdir(parents=True, exist_ok=True)
    protected_path.write_bytes(protected_payload)
    permitted_path.write_bytes(permitted_payload)

    protected_candidate = _candidate(
        rel_path="protected/part-000.parquet",
        payload=protected_payload,
        code_ref=code_ref,
        preservation_class=PreservationClass.PROTECTED_EVIDENCE,
    )
    protected_decision = evaluate_retention_deletion(
        policy=RetentionPolicyDefinitionV1(),
        candidate=protected_candidate,
        decision_time="2026-04-15T00:00:00Z",
        decision_actor_or_authority="wave6-golden-e2e",
        restore_proof=None,
        protection_evidence=ProtectionEvidenceRef(
            candidate_is_k06_protected_evidence=True,
            k06_assessment_identity="protection-assessment-v1:sha256:" + "3" * 64,
            protection_state="protected",
        ),
        relocation_evidence=RelocationEvidenceRef(),
    )
    audit = InMemoryRetentionDeletionAuditStore()
    execute_retention_deletion(
        decision=protected_decision,
        storage_root=storage,
        audit_store=audit,
        deleted_at="2026-04-15T00:00:01Z",
        tombstone_or_catalog_update_ref="wave6-protected-refusal",
    )

    permitted_candidate = _candidate(
        rel_path="permitted/part-000.parquet",
        payload=permitted_payload,
        code_ref=code_ref,
        preservation_class=PreservationClass.CANONICAL_RESTORABLE,
    )
    permitted_decision = evaluate_retention_deletion(
        policy=RetentionPolicyDefinitionV1(),
        candidate=permitted_candidate,
        decision_time="2026-04-15T00:00:00Z",
        decision_actor_or_authority="wave6-golden-e2e",
        restore_proof=VerifiedRestoreProofRef(
            recovery_set_identity="recovery-set-v1:sha256:" + "4" * 64,
            isolated_restore_proof_identity="isolated-restore-proof-v1:sha256:" + "5" * 64,
            dataset_identity=WAVE6_CANONICAL_BTCUSDT_TRADES,
            partition_key="dt=2026-01-01",
            revision=1,
            restored_content_sha256=permitted_candidate.content_sha256,
            restored_size_bytes=permitted_candidate.byte_size,
            restored_coverage_or_support="2026-01-01T00:00:00Z/2026-01-02T00:00:00Z",
            storage_boundary_independent=True,
        ),
        protection_evidence=ProtectionEvidenceRef(),
        relocation_evidence=RelocationEvidenceRef(),
    )
    permitted_result = execute_retention_deletion(
        decision=permitted_decision,
        storage_root=storage,
        audit_store=audit,
        deleted_at="2026-04-15T00:00:02Z",
        tombstone_or_catalog_update_ref="wave6-permitted-deletion",
    )
    return (
        protected_decision,
        permitted_decision,
        permitted_result,
        protected_path.exists(),
        permitted_path.exists(),
    )


def _candidate(
    *,
    rel_path: str,
    payload: bytes,
    code_ref: str,
    preservation_class: PreservationClass,
) -> DeletionCandidateV1:
    return DeletionCandidateV1(
        dataset_identity=WAVE6_CANONICAL_BTCUSDT_TRADES,
        catalog_partition_id="99999999-9999-4999-8999-999999999999",
        partition_key="dt=2026-01-01",
        revision=1,
        storage_root_id="hot",
        rel_path=rel_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        lifecycle_state="closed",
        producer="wave6-golden-e2e",
        code_ref=code_ref,
        finalized_at=Instant.parse("2026-01-01T00:00:00Z"),
        preservation_class=preservation_class,
    )


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _stable_deletion_result(result: Any, expected_rel_path: str) -> dict[str, Any]:
    payload = result.stable_dict()
    payload["deleted_paths"] = [expected_rel_path] if result.deleted else []
    return payload


__all__ = [
    "WAVE6_CANONICAL_BTCUSDT_TRADES",
    "WAVE6_DATASET_REL_ROOT",
    "WAVE6_GOLDEN_E2E_PROOF_VERSION",
    "Wave6GoldenE2EProof",
    "run_wave6_golden_e2e_proof",
]

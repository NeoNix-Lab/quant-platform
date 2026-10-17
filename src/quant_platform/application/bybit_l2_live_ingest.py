"""Application-owned Bybit L2 live ingest and resync orchestration (A14f v2).

ADR-0070 §8 (Live supervision, v2) and Amendment 1 §9 (Live day roll);
ADR-0040 (Bybit live conventions), ADR-0041 (live supervisor process boundary),
ADR-0042 (checkpoint recovery), ADR-0043 (durable checkpoint state).

This module coordinates:
1. Continuous ingestion of normalized Bybit L2 live events.
2. Checkpoint-before-advance semantics with atomic local persistence (:class:`L2CheckpointStore`).
3. Restart recovery resuming from durable checkpoints.
4. UTC midnight day-roll without synthetic snapshots (ADR-0070 Amendment 1 §9):
   - Day transition splits the continuity run cleanly at 00:00:00 UTC.
   - The continuation partition (``dt=<next_day>/seg=001``) starts with a delta.
   - Anchor check verifies continuity against predecessor day catalog state
     (predecessor key, revision, final sequence, and final update id).
5. Canonical Parquet materialization and publication certification via
   :class:`~quant_platform.data.l2_publication.L2PublicationCertification`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..canonical import canonical_bytes
from ..data.l2_materializer import materialize_l2_book_event_v1
from ..data.l2_publication import L2PublicationCertification
from ..data.manifests import (
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from ..data.models import (
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    L2BookEvent,
)
from ..data.publication import (
    SealedPartitionEvidence,
)
from ..data.publication_catalog import CatalogPublicationWriter
from ..source_adapters.bybit_l2 import (
    BYBIT_L2_ORDERING_PROVIDER,
    bybit_l2_continuity_evidence_detail,
)
from ..source_adapters.bybit_l2_live import (
    BYBIT_L2_LIVE_COVERAGE_EVIDENCE_KIND,
    BYBIT_L2_LIVE_MAPPING_V1,
    BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
    BybitL2LiveBookEventV1CertificationProfile,
    BybitL2LiveIntegrityError,
    BybitL2LiveResyncStateMachine,
    L2ResyncState,
)

LIVE_L2_CHECKPOINT_IDENTITY_DOMAIN = "quant-platform/live-l2-checkpoint-v1"


class L2CheckpointError(ValueError):
    """Base exception for L2 checkpoint failures."""


class L2CheckpointCorruptError(L2CheckpointError):
    """The persisted L2 checkpoint is corrupt or unreadable."""


class L2CheckpointRegressionError(L2CheckpointError):
    """A candidate L2 checkpoint does not strictly advance progress."""


class L2ContinuationAnchorError(DataIntegrityError):
    """A day-roll continuation partition fails predecessor anchor validation."""


@dataclass(frozen=True, slots=True)
class LiveL2CheckpointV1:
    """Durable recovery unit for Bybit live L2 order book ingest (ADR-0042 / ADR-0070 §8)."""

    dataset_identity: DatasetIdentity
    source_semantics_id: str
    last_canonical_exchange_ts: Instant
    last_canonical_sequence: int
    last_canonical_update_id: int
    catalog_dataset_id: str
    partition_key: str
    revision: int
    partition_manifest_sha256: str
    coverage_segment_id: str
    generation: int
    created_at: Instant

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise L2CheckpointError("dataset_identity must be DatasetIdentity")
        if not isinstance(self.last_canonical_exchange_ts, Instant):
            raise L2CheckpointError("last_canonical_exchange_ts must be an Instant")
        if not isinstance(self.last_canonical_sequence, int) or self.last_canonical_sequence < 0:
            raise L2CheckpointError("last_canonical_sequence must be a non-negative integer")
        if not isinstance(self.last_canonical_update_id, int) or self.last_canonical_update_id < 0:
            raise L2CheckpointError("last_canonical_update_id must be a non-negative integer")
        if not isinstance(self.generation, int) or self.generation < 1:
            raise L2CheckpointError("generation must be a positive integer")
        if not isinstance(self.created_at, Instant):
            raise L2CheckpointError("created_at must be an Instant")

    def is_same_domain(self, other: LiveL2CheckpointV1) -> bool:
        return (
            self.dataset_identity == other.dataset_identity
            and self.source_semantics_id == other.source_semantics_id
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity.stable_dict(),
            "source_semantics_id": self.source_semantics_id,
            "last_canonical_exchange_ts": self.last_canonical_exchange_ts.isoformat(),
            "last_canonical_sequence": self.last_canonical_sequence,
            "last_canonical_update_id": self.last_canonical_update_id,
            "catalog_dataset_id": self.catalog_dataset_id,
            "partition_key": self.partition_key,
            "revision": self.revision,
            "partition_manifest_sha256": self.partition_manifest_sha256,
            "coverage_segment_id": self.coverage_segment_id,
            "generation": self.generation,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def checkpoint_identity(self) -> str:
        payload = self.canonical_payload()
        payload_bytes = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
        return hashlib.sha256(payload_bytes).hexdigest()


def advance_l2_checkpoint(
    previous: LiveL2CheckpointV1 | None,
    candidate: LiveL2CheckpointV1,
) -> LiveL2CheckpointV1:
    """Validate and advance to the next durable L2 checkpoint (ADR-0042 S2/S4)."""
    if previous is None:
        if candidate.generation != 1:
            raise L2CheckpointRegressionError("Initial checkpoint must be generation 1")
        return candidate

    if not previous.is_same_domain(candidate):
        raise L2CheckpointError("Checkpoint domain mismatch")

    if candidate.checkpoint_identity == previous.checkpoint_identity:
        return previous

    if candidate.generation != previous.generation + 1:
        raise L2CheckpointRegressionError(
            f"Checkpoint generation must advance by 1 (previous={previous.generation}, candidate={candidate.generation})"
        )

    if candidate.last_canonical_sequence <= previous.last_canonical_sequence:
        raise L2CheckpointRegressionError(
            "Candidate checkpoint does not strictly advance sequence"
        )

    return candidate


class L2CheckpointStore:
    """Atomic local-file persistence for :class:`LiveL2CheckpointV1` using ``os.replace``."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LiveL2CheckpointV1 | None:
        if not self._path.exists():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return LiveL2CheckpointV1(
                dataset_identity=DatasetIdentity(**data["dataset_identity"]),
                source_semantics_id=data["source_semantics_id"],
                last_canonical_exchange_ts=Instant.parse(data["last_canonical_exchange_ts"]),
                last_canonical_sequence=int(data["last_canonical_sequence"]),
                last_canonical_update_id=int(data["last_canonical_update_id"]),
                catalog_dataset_id=data["catalog_dataset_id"],
                partition_key=data["partition_key"],
                revision=int(data["revision"]),
                partition_manifest_sha256=data["partition_manifest_sha256"],
                coverage_segment_id=data["coverage_segment_id"],
                generation=int(data["generation"]),
                created_at=Instant.parse(data["created_at"]),
            )
        except Exception as exc:
            raise L2CheckpointCorruptError(f"Failed to load checkpoint from {self._path}: {exc}") from exc

    def save(self, checkpoint: LiveL2CheckpointV1) -> None:
        if not isinstance(checkpoint, LiveL2CheckpointV1):
            raise L2CheckpointError("save() requires a LiveL2CheckpointV1")

        doc = dict(checkpoint.canonical_payload())
        doc["checkpoint_identity"] = checkpoint.checkpoint_identity
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.tmp-{os.getpid()}")
        raw = json.dumps(doc, indent=2, sort_keys=True)
        tmp_path.write_text(raw, encoding="utf-8")
        os.replace(tmp_path, self._path)


@dataclass(frozen=True, slots=True)
class PredecessorAnchorInfo:
    """Summary of predecessor partition's terminal state for continuation verification."""

    partition_key: str
    revision: int
    final_sequence: int
    final_update_id: int
    final_exchange_ts: Instant
    partition_manifest_sha256: str


def validate_continuation_anchor(
    predecessor: PredecessorAnchorInfo,
    first_continuation_event: L2BookEvent,
) -> None:
    """Validate continuation partition against predecessor state (ADR-0070 Amendment 1 §9).

    Enforces:
    - Continuation starts with a ``delta`` (no synthetic snapshot).
    - ``native_update_id`` strictly continues predecessor: $u_{first} = u_{prev} + 1$.
    - ``native_sequence`` strictly advances predecessor: $seq_{first} > seq_{prev}$.
    - ``exchange_ts`` non-decreasing: $cts_{first} \\ge cts_{prev}$.
    """
    if first_continuation_event.event_type != "delta":
        raise L2ContinuationAnchorError(
            f"Continuation partition must start with delta, got {first_continuation_event.event_type!r}"
        )

    seq = int(first_continuation_event.native_sequence)  # type: ignore[arg-type]
    u = int(first_continuation_event.native_update_id)  # type: ignore[arg-type]

    if u != predecessor.final_update_id + 1:
        raise L2ContinuationAnchorError(
            f"Continuation update_id {u} != predecessor final update_id {predecessor.final_update_id} + 1"
        )

    if seq <= predecessor.final_sequence:
        raise L2ContinuationAnchorError(
            f"Continuation sequence {seq} <= predecessor final sequence {predecessor.final_sequence}"
        )

    if first_continuation_event.exchange_ts.epoch_ns < predecessor.final_exchange_ts.epoch_ns:
        raise L2ContinuationAnchorError(
            f"Continuation exchange_ts {first_continuation_event.exchange_ts.isoformat()} "
            f"before predecessor {predecessor.final_exchange_ts.isoformat()}"
        )


@dataclass(frozen=True, slots=True)
class LivePartitionResult:
    """Published live partition summary and evidence."""

    partition_key: str
    artifact_path: Path
    partition_manifest_path: Path
    coverage_manifest_path: Path
    evidence: SealedPartitionEvidence


class BybitL2LiveIngestService:
    """Supervised Live L2 Ingest Service managing batching, day roll, and durable publication."""

    def __init__(
        self,
        *,
        storage_root: Path | str,
        checkpoint_store: L2CheckpointStore,
        catalog_writer: CatalogPublicationWriter | None = None,
        dataset_identity: DatasetIdentity | None = None,
        max_batch_size: int = 500,
        cut_sequence: int | None = None,
        storage_root_id: str = "default",
    ) -> None:
        self._storage_root = Path(storage_root)
        self._storage_root_id = storage_root_id
        self._checkpoint_store = checkpoint_store
        self._catalog_writer = catalog_writer
        self._dataset_identity = dataset_identity or DatasetIdentity(
            "canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1"
        )
        self._max_batch_size = max_batch_size
        self._cut_sequence = cut_sequence

        self._state_machine = BybitL2LiveResyncStateMachine()
        self._current_checkpoint: LiveL2CheckpointV1 | None = None
        self._current_day: str | None = None
        self._current_seg_index: int = 1
        self._buffer: list[L2BookEvent] = []
        self._predecessor_anchor: PredecessorAnchorInfo | None = None

        self._init_from_checkpoint()

    def _init_from_checkpoint(self) -> None:
        checkpoint = self._checkpoint_store.load()
        if checkpoint is not None:
            self._current_checkpoint = checkpoint
            # Extract day and segment from partition_key e.g. "dt=2026-10-18/seg=001"
            parts = checkpoint.partition_key.split("/")
            if len(parts) >= 2 and parts[0].startswith("dt=") and parts[1].startswith("seg="):
                self._current_day = parts[0].split("=")[1]
                self._current_seg_index = int(parts[1].split("=")[1])
            self._state_machine = BybitL2LiveResyncStateMachine(
                initial_state=L2ResyncState.STREAMING,
                anchor_sequence=checkpoint.last_canonical_sequence,
                anchor_update_id=checkpoint.last_canonical_update_id,
                anchor_exchange_ts=checkpoint.last_canonical_exchange_ts,
            )

    @property
    def state_machine(self) -> BybitL2LiveResyncStateMachine:
        return self._state_machine

    @property
    def current_checkpoint(self) -> LiveL2CheckpointV1 | None:
        return self._current_checkpoint

    def feed_event(self, raw_or_event: Mapping[str, Any] | L2BookEvent) -> list[SealedPartitionEvidence]:
        """Feed a live message or normalized event into the ingest service.

        Handles:
        - Cutover enforcement (ignoring events <= cut_sequence).
        - Resync state machine processing.
        - Day roll detection and predecessor anchoring.
        - Micro-batch flushing and publication.
        """
        accepted, closed_gap, resub_needed = self._state_machine.process_message(raw_or_event)
        if accepted is None:
            return []

        seq = int(accepted.native_sequence)  # type: ignore[arg-type]
        if self._cut_sequence is not None and seq <= self._cut_sequence:
            # Pre-cutover event dropped
            return []

        published_partitions: list[LivePartitionResult] = []
        event_day = accepted.exchange_ts.isoformat()[:10]

        # UTC Midnight Day Roll
        if self._current_day is not None and event_day != self._current_day:
            # 1. Flush any pending events for current_day
            if self._buffer:
                sealed = self.flush()
                if sealed is not None:
                    published_partitions.append(sealed)

            # 2. Advance day and reset segment index
            self._current_day = event_day
            self._current_seg_index = 1

            # 3. Validate continuation anchor if we have predecessor info
            if self._predecessor_anchor is not None:
                validate_continuation_anchor(self._predecessor_anchor, accepted)

        if self._current_day is None:
            self._current_day = event_day
            self._current_seg_index = 1

        self._buffer.append(accepted)

        if len(self._buffer) >= self._max_batch_size:
            sealed = self.flush()
            if sealed is not None:
                published_partitions.append(sealed)

        return published_partitions

    def flush(self) -> LivePartitionResult | None:
        """Seal, materialize, certify, publish, and checkpoint current buffer."""
        if not self._buffer or self._current_day is None:
            return None

        events = tuple(self._buffer)
        self._buffer = []

        partition_key = f"dt={self._current_day}/seg={self._current_seg_index:03d}"
        rel_path = (
            f"{self._dataset_identity.layer}/{self._dataset_identity.dataset_kind}/"
            f"{self._dataset_identity.venue}/{self._dataset_identity.instrument}/"
            f"{self._dataset_identity.record_schema_id}"
        )
        partition_dir = self._storage_root / rel_path / partition_key
        partition_dir.mkdir(parents=True, exist_ok=True)
        parquet_file = partition_dir / "part-001.parquet"

        # 1. Materialize Parquet micro-batch
        mat_result = materialize_l2_book_event_v1(
            parquet_file,
            events,
            dataset_identity=self._dataset_identity,
            ordering_provider=BYBIT_L2_ORDERING_PROVIDER,
        )

        first_event = events[0]
        last_event = events[-1]

        # 2. Partition manifest
        part_manifest_file = partition_dir / "partition-manifest.json"
        rel_artifact_path = f"{partition_key}/part-001.parquet"
        emit_partition_manifest(
            part_manifest_file,
            mat_result,
            dataset_identity=self._dataset_identity,
            dataset_root=self._storage_root / rel_path,
            partition_key=partition_key,
            revision=1,
            rel_path=rel_artifact_path,
            created_at=first_event.exchange_ts,
            closed_at=last_event.exchange_ts,
            producer=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            code_ref=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
        )
        part_doc = json.loads(part_manifest_file.read_text(encoding="utf-8"))
        part_manifest_sha = hashlib.sha256(part_manifest_file.read_bytes()).hexdigest()

        start_ns = first_event.exchange_ts.epoch_ns
        end_ns = max(last_event.exchange_ts.epoch_ns + 1_000, (last_event.exchange_ts.epoch_ns // 1000 + 1) * 1000)
        cov_start = Instant(start_ns)
        cov_end = Instant(end_ns)

        # 3. Coverage manifest
        profile = BybitL2LiveBookEventV1CertificationProfile(BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1)
        report = profile.certify(self._dataset_identity, events)
        continuity_detail = bybit_l2_continuity_evidence_detail(report)

        coverage_file = partition_dir / "coverage-manifest.json"
        coverage_id = f"l2-live-{self._current_day}-seg-{self._current_seg_index:03d}"
        acquisition = {
            "basis": "live_stream",
            "source_semantics": BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            "mapping": BYBIT_L2_LIVE_MAPPING_V1,
            "intent_start": cov_start.isoformat(),
            "intent_end": cov_end.isoformat(),
        }
        emit_coverage_manifest(
            coverage_file,
            dataset_identity=self._dataset_identity,
            source_dataset_identity=self._dataset_identity,
            coverage_id=coverage_id,
            supersedes=None,
            created_at=last_event.exchange_ts,
            acquisition=acquisition,
            assertions=[{
                "assertion_id": f"seg-{self._current_seg_index:03d}",
                "start": cov_start.isoformat(),
                "end": cov_end.isoformat(),
                "status": "complete",
                "partitions": [{"partition_key": partition_key, "revision": 1}],
                "evidence": [
                    {"kind": BYBIT_L2_LIVE_COVERAGE_EVIDENCE_KIND, "detail": "live_orderbook_websocket_stream"},
                    {"kind": "sequence_continuity", "detail": continuity_detail},
                ],
            }],
            producer=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            code_ref=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            partition_manifests=[part_doc],
        )

        # 4. Sealed Partition Evidence
        dataset_root = self._storage_root / rel_path
        dataset_manifest_path = dataset_root / "dataset-manifest.json"
        if not dataset_manifest_path.exists():
            dataset_root.mkdir(parents=True, exist_ok=True)
            derived_from = None
            transform = None
            if self._dataset_identity.layer != "raw":
                derived_from = [
                    DatasetIdentity(
                        layer="raw",
                        dataset_kind=self._dataset_identity.dataset_kind,
                        venue=self._dataset_identity.venue,
                        instrument=self._dataset_identity.instrument,
                        record_schema_id=self._dataset_identity.record_schema_id,
                    )
                ]
                transform = "canonicalize-l2-book-events-v1"
            emit_dataset_manifest(
                dataset_manifest_path,
                dataset_identity=self._dataset_identity,
                created_at=first_event.exchange_ts,
                derived_from=derived_from,
                transform=transform,
            )

        evidence = SealedPartitionEvidence(
            dataset_manifest_path=dataset_manifest_path,
            partition_manifest_path=part_manifest_file,
            coverage_manifest_paths=(coverage_file,),
            artifact_path=parquet_file,
            storage_root_id=self._storage_root_id,
        )

        # 5. Certification
        if self._catalog_writer is not None:
            certification = L2PublicationCertification(
                self._catalog_writer,
                profile,
            )
            cert_run = certification.run(evidence)
            if cert_run.certification.status != "pass":
                raise BybitL2LiveIntegrityError(
                    f"L2 live partition certification failed: {cert_run.certification.violations}"
                )

        # 6. Checkpoint advance: advance checkpoint AFTER durable publish
        final_seq = int(last_event.native_sequence)  # type: ignore[arg-type]
        final_u = int(last_event.native_update_id)  # type: ignore[arg-type]
        gen = (self._current_checkpoint.generation + 1) if self._current_checkpoint else 1
        now_instant = Instant.parse(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

        new_checkpoint = LiveL2CheckpointV1(
            dataset_identity=self._dataset_identity,
            source_semantics_id=BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            last_canonical_exchange_ts=last_event.exchange_ts,
            last_canonical_sequence=final_seq,
            last_canonical_update_id=final_u,
            catalog_dataset_id=rel_path,
            partition_key=partition_key,
            revision=1,
            partition_manifest_sha256=part_manifest_sha,
            coverage_segment_id=coverage_id,
            generation=gen,
            created_at=now_instant,
        )
        self._checkpoint_store.save(new_checkpoint)
        self._current_checkpoint = new_checkpoint

        # Record predecessor anchor for day roll
        self._predecessor_anchor = PredecessorAnchorInfo(
            partition_key=partition_key,
            revision=1,
            final_sequence=final_seq,
            final_update_id=final_u,
            final_exchange_ts=last_event.exchange_ts,
            partition_manifest_sha256=part_manifest_sha,
        )

        self._current_seg_index += 1
        return LivePartitionResult(
            partition_key=partition_key,
            artifact_path=parquet_file,
            partition_manifest_path=part_manifest_file,
            coverage_manifest_path=coverage_file,
            evidence=evidence,
        )


__all__ = [
    "LIVE_L2_CHECKPOINT_IDENTITY_DOMAIN",
    "BybitL2LiveIngestService",
    "L2CheckpointCorruptError",
    "L2CheckpointError",
    "L2CheckpointRegressionError",
    "L2CheckpointStore",
    "L2ContinuationAnchorError",
    "LiveL2CheckpointV1",
    "LivePartitionResult",
    "PredecessorAnchorInfo",
    "advance_l2_checkpoint",
    "validate_continuation_anchor",
]

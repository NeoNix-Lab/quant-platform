"""Application-owned bounded Bybit live proof orchestration."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from quant_platform.access.catalog import Catalog
from quant_platform.access.gateway import DataGateway
from quant_platform.access.models import DataRequest, LifecyclePolicy
from quant_platform.data import Instant
from quant_platform.data.manifests import (
    DATASET_MANIFEST_V2,
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.models import TradeRecord
from quant_platform.data.publication import (
    PublicationCertification,
    SealedPartitionEvidence,
)
from quant_platform.data.publication_catalog import CatalogPublicationConflict, CatalogPublicationWriter
from quant_platform.data.publication_eligibility import (
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
)
from quant_platform.data.publication_eligibility_catalog import PublicationEligibilityCatalog
from quant_platform.source_adapters.bybit import (
    BYBIT_ORDERING_PROVIDER,
    materialize_bybit_trade_v1,
)
from quant_platform.source_adapters.bybit_live import (
    BYBIT_LIVE_TRADE_V1_CERTIFICATION_PROFILE,
    BYBIT_LIVE_TRADE_V1_CHECK_SUITE,
    BYBIT_LIVE_SOURCE_SEMANTICS_V1,
    BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1,
    BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1,
    SUPPORTED_CATEGORY,
    SUPPORTED_SYMBOL,
    SUPPORTED_TOPIC,
    BybitLiveSessionEvidence,
    BybitLiveSourceError,
    BybitLiveTradeV1CertificationProfile,
    LiveSessionTracker,
    ReconnectResult,
    ReconnectStatus,
    TradeKeyV1,
    build_bybit_live_coverage_document,
    bybit_live_dataset_identity,
    canonicalize_bybit_live_message,
    canonicalize_bybit_recent_public_trade,
    deduplicate_live_records,
    reconcile_after_disconnect,
)
from quant_platform.operations.checkpoint import (
    CheckpointBindingError,
    CheckpointDomainMismatch,
    CheckpointError,
    CheckpointStore,
    LiveCheckpointV1,
    advance_checkpoint,
    validate_publication_binding,
)


BYBIT_PUBLIC_LINEAR_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_RECENT_TRADES_URL = "https://api.bybit.com/v5/market/recent-trade"
K10_RESTART_DURABLE_STATES = LifecyclePolicy.VALID_CLOSED_AND_DEGRADED.states
BYBIT_RESTART_RECONCILIATION_CHECK_SUITE = (
    "producer-consumer-conformity-v1/bybit-trade-v1-restart-reconciliation"
)
BYBIT_RESTART_RECONCILIATION_CERTIFICATION_PROFILE = (
    "producer-consumer-conformity-v1/bybit-trade-v1-restart-reconciliation-v1"
)


class LiveProviderProofPending(RuntimeError):
    """Raised when a real-provider proof cannot be honestly executed."""


@dataclass(frozen=True, slots=True)
class LiveProviderProofReport:
    status: str
    topic: str
    messages: int
    records: int
    duplicates_removed: int
    final_state: str
    errors: tuple[str, ...]
    # The deduplicated canonical records actually accepted, and the session
    # evidence behind them -- absent from the original summary-only report,
    # but needed by any caller (e.g. a real-server publication proof) that
    # must compose #107's acquisition seam with #105/AC10's canonical
    # publication path rather than re-running acquisition a second time.
    accepted_records: tuple[TradeRecord, ...] = ()
    session_evidence: BybitLiveSessionEvidence | None = None


@dataclass(frozen=True, slots=True)
class DurablePublicationState:
    """Freshly queried durable catalog state a checkpoint's binding is
    validated against (ADR-0042 S4/S6). The caller queries this -- e.g. via
    the same catalog connection ``run_real_server_publish_proof`` already
    uses -- this module never opens a connection to compute it itself."""

    catalog_dataset_id: str
    partition_key: str
    revision: int
    partition_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class BybitRestartReconciliationCertificationProfile:
    """K10-only source profile for post-restart bounded reconciliation publication.

    This deliberately does not broaden the frozen A11 live-session profile:
    Process B publishes records accepted by ADR-0040 bounded REST
    reconciliation, and therefore requires explicit `reconciliation`
    coverage evidence under the Bybit recent-public-trades source semantics.
    Canonical record validation is delegated to the existing live profile so
    the trade-v1/sequence requirements stay identical.
    """

    code_ref: str
    profile_id: str = BYBIT_RESTART_RECONCILIATION_CERTIFICATION_PROFILE
    check_suite: str = BYBIT_RESTART_RECONCILIATION_CHECK_SUITE

    def _live_profile(self) -> BybitLiveTradeV1CertificationProfile:
        return BybitLiveTradeV1CertificationProfile(self.code_ref)

    def applies_to(self, identity) -> bool:
        return self._live_profile().applies_to(identity)

    def ordering_key(self, record: TradeRecord) -> tuple[Instant, str]:
        return self._live_profile().ordering_key(record)

    def validate_source(
        self,
        identity,
        coverage_documents: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if not self.applies_to(identity):
            raise BybitLiveSourceError("Bybit restart reconciliation profile does not apply")
        if not coverage_documents:
            raise BybitLiveSourceError("restart certification requires reconciliation coverage evidence")
        for document in coverage_documents:
            acquisition = document.get("acquisition") or {}
            if acquisition.get("basis") != "reconciliation":
                raise BybitLiveSourceError("restart coverage basis must be reconciliation")
            if acquisition.get("source_semantics") != BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1:
                raise BybitLiveSourceError("restart coverage source_semantics is not Bybit recent public trades")
            if acquisition.get("mapping") != BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1:
                raise BybitLiveSourceError("restart coverage mapping is not Bybit recent public trades to trade-v1")
            for assertion in document.get("assertions") or ():
                if assertion.get("status") != "complete":
                    continue
                if not any(item.get("kind") == "reconciliation" for item in assertion.get("evidence") or ()):
                    raise BybitLiveSourceError("complete restart coverage lacks reconciliation evidence")
        return {
            "source_semantics": BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1,
            "mapping": BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1,
            "documents": len(coverage_documents),
        }

    def validate_records(self, identity, records: Sequence[TradeRecord]) -> Mapping[str, Any]:
        return self._live_profile().validate_records(identity, records)


def fetch_recent_public_trades(*, limit: int = 100) -> tuple[Any, ...]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise BybitLiveSourceError("recent trade limit must be in [1, 1000]", field="limit")
    query = urlencode({"category": SUPPORTED_CATEGORY, "symbol": SUPPORTED_SYMBOL, "limit": str(limit)})
    with urlopen(f"{BYBIT_RECENT_TRADES_URL}?{query}", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("retCode") != 0:
        raise LiveProviderProofPending(f"Bybit recent-trade request failed: {payload!r}")
    result = payload.get("result") or {}
    if result.get("category") != SUPPORTED_CATEGORY:
        raise BybitLiveSourceError("recent-trade response category does not match requested linear category")
    return tuple(canonicalize_bybit_recent_public_trade(item) for item in result.get("list") or ())


async def run_bounded_live_provider_proof(
    *,
    max_messages: int = 3,
    max_seconds: float = 20.0,
) -> LiveProviderProofReport:
    try:
        import websockets  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LiveProviderProofPending(
            "LIVE_PROVIDER_PROOF_PENDING: optional dependency 'websockets' is not installed"
        ) from exc
    if max_messages < 1:
        raise BybitLiveSourceError("max_messages must be positive")
    if max_seconds <= 0:
        raise BybitLiveSourceError("max_seconds must be positive")

    tracker = LiveSessionTracker()
    accepted = []
    errors: list[str] = []
    try:
        async with websockets.connect(BYBIT_PUBLIC_LINEAR_WS_URL, ping_interval=None, close_timeout=5) as socket:
            tracker.connected()
            await socket.send(json.dumps({"op": "subscribe", "args": [SUPPORTED_TOPIC]}))
            deadline = asyncio.get_running_loop().time() + max_seconds
            messages = 0
            while messages < max_messages:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
                document = json.loads(raw)
                if document.get("op") == "subscribe":
                    if document.get("success") is not True:
                        raise LiveProviderProofPending(f"subscription refused: {document!r}")
                    tracker.subscribed(topic=SUPPORTED_TOPIC, conn_id=document.get("conn_id"))
                    continue
                if document.get("op") in {"pong", "ping"}:
                    tracker.heartbeat(conn_id=document.get("conn_id"))
                    continue
                if document.get("topic") != SUPPORTED_TOPIC:
                    continue
                batch = canonicalize_bybit_live_message(document)
                tracker.observed_message(batch)
                accepted.extend(batch.records)
                messages += 1
    except TimeoutError:
        errors.append("bounded proof timed out before enough trade messages arrived")
    except websockets.ConnectionClosed as exc:
        # websockets>=12 raises this on remote/protocol connection loss; it
        # subclasses WebSocketException/Exception, not OSError, so it would
        # otherwise propagate unhandled instead of mapping to a pending
        # proof like every other connection-loss path here.
        tracker.disconnected(str(exc))
        raise LiveProviderProofPending(f"LIVE_PROVIDER_PROOF_PENDING: provider connection closed: {exc}") from exc
    except OSError as exc:
        tracker.disconnected(str(exc))
        raise LiveProviderProofPending(f"LIVE_PROVIDER_PROOF_PENDING: provider network unavailable: {exc}") from exc

    evidence = tracker.evidence()
    deduped = deduplicate_live_records(accepted)
    return LiveProviderProofReport(
        status="PASS" if evidence.validated_messages and not errors else "LIVE_PROVIDER_PROOF_PENDING",
        topic=SUPPORTED_TOPIC,
        messages=evidence.validated_messages,
        records=len(deduped),
        duplicates_removed=len(accepted) - len(deduped),
        final_state=evidence.final_state.value,
        errors=tuple(errors),
        accepted_records=deduped,
        session_evidence=evidence,
    )


def run_bounded_live_provider_proof_sync(*, max_messages: int = 3, max_seconds: float = 20.0) -> LiveProviderProofReport:
    return asyncio.run(run_bounded_live_provider_proof(max_messages=max_messages, max_seconds=max_seconds))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded A11 Bybit public live trade proof")
    parser.add_argument("--max-messages", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=20.0)
    return parser


@dataclass(frozen=True, slots=True)
class RealServerPublishProofReport:
    """K02 (#108): result of composing #107's acquisition seam with the
    existing canonical S13/S14 publication path and a DataGateway read-back,
    on a real deployment. Writes nothing unless acquisition yields at least
    one accepted record."""

    status: str
    acquisition: LiveProviderProofReport
    identity: dict[str, Any] | None = None
    partition_key: str | None = None
    artifact_path: str | None = None
    coverage_status: str | None = None
    certification_status: str | None = None
    certification_categories: tuple[tuple[str, str], ...] = ()
    eligibility_published: bool | None = None
    datagateway_read_record_count: int | None = None
    datagateway_read_matches_published: bool | None = None
    # Set only when this run actually (re-)emitted dataset-manifest.json
    # (i.e. none existed yet at the shared path); a caller can use this to
    # align an existing catalog.datasets row after an operator-authorized
    # dataset-manifest reset, without needing separate file access.
    dataset_manifest_freshly_emitted: bool = False
    dataset_manifest_sha256: str | None = None
    dataset_manifest_rel_root: str | None = None
    durable_publication: DurablePublicationState | None = None
    coverage_id: str | None = None
    coverage_assertion_id: str | None = None


def _connect_catalog(dsn: str | None):
    import psycopg

    return psycopg.connect(dsn) if dsn else psycopg.connect()


def _next_revision(dsn: str | None, identity: Any, partition_key: str) -> int:
    """Each bounded proof run acquires a fresh physical parquet artifact, so
    it cannot reuse a prior run's already-sealed revision for the same
    partition_key (seal_partition correctly refuses a closed target whose
    physical evidence doesn't match). Query the real next revision instead
    of assuming 1, which only holds for a partition_key never sealed before."""
    connection = _connect_catalog(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(MAX(p.revision), 0)
                  FROM catalog.partitions p
                  JOIN catalog.datasets d ON d.dataset_id = p.dataset_id
                 WHERE d.layer=%s AND d.kind=%s AND d.venue=%s
                   AND d.instrument=%s AND d.schema_id=%s
                   AND p.partition_key=%s
                """,
                (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id, partition_key),
            )
            return int(cursor.fetchone()[0]) + 1
    finally:
        connection.close()


def _record_intent_interval(records: tuple[TradeRecord, ...]) -> tuple[str, str]:
    instants = tuple(Instant.parse(record.exchange_ts) for record in records)
    start = min(instants)
    end = Instant(max(instant.epoch_ns for instant in instants) + 1)
    return start.isoformat(), end.isoformat()


def _build_restart_reconciliation_coverage_document(
    *,
    dataset_identity: Any,
    coverage_id: str,
    intent_start: str,
    intent_end: str,
    assertion_id: str,
    assertion_start: str,
    assertion_end: str,
    partition_key: str,
    revision: int,
    reconcile_result: ReconnectResult,
    created_at: str,
    producer: str,
    code_ref: str,
) -> dict[str, Any]:
    if reconcile_result.status is not ReconnectStatus.CONTINUITY_RESTORED:
        raise CheckpointError("restart reconciliation coverage requires restored continuity")
    detail_parts = [
        f"status={reconcile_result.status.value}",
        f"accepted_records={len(reconcile_result.accepted_records)}",
    ]
    for key in ("reason", "recent_window_records", "buffered_ws_records"):
        if key in reconcile_result.evidence:
            detail_parts.append(f"{key}={reconcile_result.evidence[key]}")
    return {
        "source_dataset_identity": dataset_identity,
        "coverage_id": coverage_id,
        "supersedes": None,
        "created_at": created_at,
        "acquisition": {
            "basis": "reconciliation",
            "intent_start": intent_start,
            "intent_end": intent_end,
            "source_semantics": BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1,
            "mapping": BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1,
        },
        "assertions": [
            {
                "assertion_id": assertion_id,
                "start": assertion_start,
                "end": assertion_end,
                "status": "complete",
                "partitions": [{"partition_key": partition_key, "revision": revision}],
                "evidence": [{"kind": "reconciliation", "detail": "; ".join(detail_parts)}],
            }
        ],
        "producer": producer,
        "code_ref": code_ref,
    }


def run_real_server_publish_proof(
    *,
    max_messages: int,
    max_seconds: float,
    storage_root: str | Path,
    storage_root_id: str,
    dsn: str | None,
    producer: str,
    code_ref: str,
) -> RealServerPublishProofReport:
    """K02 (#108) minimum real-server proof.

    Reuses #107's bounded acquisition seam (never re-implements it), then
    composes the same S13/S14/DataGateway production seams
    application/conformity.py already uses for the historical vertical --
    swapped to BybitLiveTradeV1CertificationProfile and live coverage.
    """
    acquire_started_at = datetime.now(timezone.utc)
    report = run_bounded_live_provider_proof_sync(max_messages=max_messages, max_seconds=max_seconds)
    acquire_finished_at = datetime.now(timezone.utc)

    if report.status != "PASS" or not report.accepted_records or report.session_evidence is None:
        return RealServerPublishProofReport(status="LIVE_PROVIDER_PROOF_PENDING", acquisition=report)

    records = report.accepted_records
    session_evidence = report.session_evidence
    identity = bybit_live_dataset_identity()
    day = acquire_started_at.strftime("%Y-%m-%d")
    partition_key = f"dt={day}"
    intent_start = acquire_started_at.isoformat().replace("+00:00", "Z")
    intent_end = acquire_finished_at.isoformat().replace("+00:00", "Z")
    revision = _next_revision(dsn, identity, partition_key)

    storage_root_path = Path(storage_root)
    dataset_root = storage_root_path.joinpath(
        identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
    )
    suffix = acquire_started_at.strftime("%H%M%S")
    rel_path = f"{partition_key}/part-{suffix}.parquet"
    artifact_path = dataset_root / rel_path
    dataset_manifest_path = dataset_root / "dataset-manifest.json"
    partition_manifest_path = dataset_root / f"partition-manifest-{suffix}.json"
    coverage_manifest_path = dataset_root / f"coverage-manifest-{suffix}.json"

    materialization = materialize_bybit_trade_v1(artifact_path, records, dataset_identity=identity)
    # The dataset manifest describes the canonical DatasetIdentity as a
    # whole (shared verbatim between the historical and live producers --
    # AC4: same trade-v1/TradeKeyV1/canonical order), not this one bounded
    # run. It is registered once; catalog_publication.py's dataset-conflict
    # check correctly refuses a second, differently-timestamped emission
    # for the same identity. Reuse whatever is already durably registered
    # at this shared path instead of re-emitting it on every run.
    dataset_emission = None
    if not dataset_manifest_path.exists():
        # Matches application/conformity.py's real production dataset-manifest
        # emission for this same shared canonical identity exactly (v2,
        # origin=source_acquired -- no raw-dataset lineage row required),
        # not the derived_from=[raw] shape used only by hermetic test
        # fixtures. A mismatched schema here is exactly what produced the
        # first DATASET_MANIFEST_CONFLICT/PublicationEligibilityRefusal.
        dataset_emission = emit_dataset_manifest(
            dataset_manifest_path, dataset_identity=identity, created_at=intent_end,
            schema_version=DATASET_MANIFEST_V2, origin="source_acquired",
            transform="canonicalize-trades-v1",
        )
    partition_emission = emit_partition_manifest(
        partition_manifest_path, materialization, dataset_identity=identity,
        dataset_root=dataset_root, partition_key=partition_key, revision=revision,
        rel_path=rel_path, created_at=intent_start, closed_at=intent_end,
        producer=producer, code_ref=code_ref,
    )
    # data/manifests.py's frozen _IDENTIFIER is `^[a-z0-9]+(?:[._-][a-z0-9]+)*$`
    # -- lowercase only, no ISO "T"/"Z" separators.
    run_tag = f"{acquire_started_at.strftime('%Y%m%d')}-{suffix}"
    coverage_id = f"k02-real-server-{run_tag}"
    coverage_assertion_id = f"k02-real-server-assertion-{run_tag}"
    coverage_input = build_bybit_live_coverage_document(
        dataset_identity=identity, coverage_id=coverage_id,
        intent_start=intent_start, intent_end=intent_end,
        assertion_id=coverage_assertion_id,
        assertion_start=intent_start, assertion_end=intent_end,
        partition_key=partition_key, revision=revision, session_evidence=session_evidence,
        created_at=intent_end, producer=producer, code_ref=code_ref,
    )
    emit_coverage_manifest(
        coverage_manifest_path, dataset_identity=identity,
        partition_manifests=[partition_emission.document], **coverage_input,
    )
    coverage_status = coverage_input["assertions"][0]["status"]
    dataset_manifest_fields: dict[str, Any] = {}
    if dataset_emission is not None:
        dataset_manifest_fields = {
            "dataset_manifest_freshly_emitted": True,
            "dataset_manifest_sha256": dataset_emission.manifest_sha256,
            "dataset_manifest_rel_root": dataset_emission.document["rel_root"],
        }

    connection = _connect_catalog(dsn)
    try:
        profile = BybitLiveTradeV1CertificationProfile(code_ref)
        try:
            run = PublicationCertification(CatalogPublicationWriter(connection), profile).run(
                SealedPartitionEvidence(
                    dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,),
                    artifact_path, storage_root_id,
                )
            )
        except CatalogPublicationConflict as exc:
            # Surface the freshly emitted dataset manifest's own hash/rel_root
            # so an operator can decide whether to align catalog.datasets to
            # it (a lineage-pointer update, never touching existing sealed
            # partitions) rather than crash with a bare traceback.
            return RealServerPublishProofReport(
                status="DATASET_MANIFEST_CONFLICT", acquisition=report,
                partition_key=partition_key, artifact_path=str(artifact_path),
                coverage_status=coverage_status,
                certification_status=f"seal_refused: {exc}",
                **dataset_manifest_fields,
            )
        categories = tuple((c.category, c.status) for c in run.certification.categories)
        if run.certification.status != "pass":
            return RealServerPublishProofReport(
                status="CERTIFICATION_FAILED", acquisition=report,
                partition_key=partition_key, artifact_path=str(artifact_path),
                coverage_status=coverage_status,
                certification_status=run.certification.status,
                certification_categories=categories,
                **dataset_manifest_fields,
            )

        eligibility = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
            PublicationEligibilityEvidence(
                dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,),
                storage_root_id, BYBIT_LIVE_TRADE_V1_CERTIFICATION_PROFILE, BYBIT_LIVE_TRADE_V1_CHECK_SUITE,
            )
        )
    finally:
        connection.close()

    connection = _connect_catalog(dsn)
    try:
        request = DataRequest(
            identity, Instant.parse(intent_start), Instant.parse(intent_end),
            schema_requirement=identity.record_schema_id,
            lifecycle_policy=LifecyclePolicy.VALID_ONLY,
            coverage_policy="strict",
            ordering_policy=BYBIT_ORDERING_PROVIDER.identity,
        )
        gateway = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
        scan = gateway.scan(request, batch_size=1000)
        read_records: list[Any] = []
        try:
            for batch in scan:
                read_records.extend(batch)
        finally:
            if getattr(scan, "completed_metadata", None) is None:
                scan.close()
    finally:
        connection.close()

    return RealServerPublishProofReport(
        status="PASS", acquisition=report,
        partition_key=partition_key, artifact_path=str(artifact_path),
        coverage_status=coverage_status,
        certification_status=run.certification.status,
        certification_categories=categories,
        eligibility_published=eligibility is not None,
        datagateway_read_record_count=len(read_records),
        datagateway_read_matches_published=len(read_records) == len(records),
        durable_publication=DurablePublicationState(
            catalog_dataset_id=run.sealed_partition.dataset_id,
            partition_key=run.sealed_partition.natural_identity.partition_key,
            revision=run.sealed_partition.natural_identity.revision,
            partition_manifest_sha256=run.sealed_partition.manifest_sha256,
        ),
        coverage_id=coverage_id,
        coverage_assertion_id=coverage_assertion_id,
        **dataset_manifest_fields,
    )


def _publish_restart_reconciliation_records(
    *,
    accepted_records: tuple[TradeRecord, ...],
    reconcile_result: ReconnectResult,
    storage_root: str | Path,
    storage_root_id: str,
    dsn: str | None,
    producer: str,
    code_ref: str,
) -> RealServerPublishProofReport:
    if not accepted_records:
        return RealServerPublishProofReport(
            status="REAL_RESTART_PROOF_PENDING",
            acquisition=LiveProviderProofReport(
                status="REAL_RESTART_PROOF_PENDING",
                topic=SUPPORTED_TOPIC,
                messages=0,
                records=0,
                duplicates_removed=0,
                final_state="RECONCILED",
                errors=("restart reconciliation produced no post-checkpoint records",),
            ),
        )

    identity = bybit_live_dataset_identity()
    intent_start, intent_end = _record_intent_interval(accepted_records)
    partition_key = f"dt={intent_start[:10]}"
    revision = _next_revision(dsn, identity, partition_key)

    storage_root_path = Path(storage_root)
    dataset_root = storage_root_path.joinpath(
        identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
    )
    suffix = datetime.now(timezone.utc).strftime("%H%M%S")
    rel_path = f"{partition_key}/part-{suffix}.parquet"
    artifact_path = dataset_root / rel_path
    dataset_manifest_path = dataset_root / "dataset-manifest.json"
    partition_manifest_path = dataset_root / f"partition-manifest-{suffix}.json"
    coverage_manifest_path = dataset_root / f"coverage-manifest-{suffix}.json"

    materialization = materialize_bybit_trade_v1(artifact_path, accepted_records, dataset_identity=identity)
    dataset_emission = None
    if not dataset_manifest_path.exists():
        dataset_emission = emit_dataset_manifest(
            dataset_manifest_path, dataset_identity=identity, created_at=intent_end,
            schema_version=DATASET_MANIFEST_V2, origin="source_acquired",
            transform="canonicalize-trades-v1",
        )
    partition_emission = emit_partition_manifest(
        partition_manifest_path, materialization, dataset_identity=identity,
        dataset_root=dataset_root, partition_key=partition_key, revision=revision,
        rel_path=rel_path, created_at=intent_start, closed_at=intent_end,
        producer=producer, code_ref=code_ref,
    )
    run_tag = f"{intent_start[:10].replace('-', '')}-{suffix}"
    coverage_id = f"k10-restart-reconciliation-{run_tag}"
    coverage_assertion_id = f"k10-restart-reconciliation-assertion-{run_tag}"
    coverage_input = _build_restart_reconciliation_coverage_document(
        dataset_identity=identity,
        coverage_id=coverage_id,
        intent_start=intent_start,
        intent_end=intent_end,
        assertion_id=coverage_assertion_id,
        assertion_start=intent_start,
        assertion_end=intent_end,
        partition_key=partition_key,
        revision=revision,
        reconcile_result=reconcile_result,
        created_at=intent_end,
        producer=producer,
        code_ref=code_ref,
    )
    emit_coverage_manifest(
        coverage_manifest_path, dataset_identity=identity,
        partition_manifests=[partition_emission.document], **coverage_input,
    )
    coverage_status = coverage_input["assertions"][0]["status"]
    dataset_manifest_fields: dict[str, Any] = {}
    if dataset_emission is not None:
        dataset_manifest_fields = {
            "dataset_manifest_freshly_emitted": True,
            "dataset_manifest_sha256": dataset_emission.manifest_sha256,
            "dataset_manifest_rel_root": dataset_emission.document["rel_root"],
        }

    report = LiveProviderProofReport(
        status="PASS",
        topic=SUPPORTED_TOPIC,
        messages=0,
        records=len(accepted_records),
        duplicates_removed=0,
        final_state="RECONCILED",
        errors=(),
        accepted_records=accepted_records,
    )

    connection = _connect_catalog(dsn)
    try:
        profile = BybitRestartReconciliationCertificationProfile(code_ref)
        try:
            run = PublicationCertification(CatalogPublicationWriter(connection), profile).run(
                SealedPartitionEvidence(
                    dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,),
                    artifact_path, storage_root_id,
                )
            )
        except CatalogPublicationConflict as exc:
            return RealServerPublishProofReport(
                status="DATASET_MANIFEST_CONFLICT", acquisition=report,
                partition_key=partition_key, artifact_path=str(artifact_path),
                coverage_status=coverage_status,
                certification_status=f"seal_refused: {exc}",
                **dataset_manifest_fields,
            )
        categories = tuple((c.category, c.status) for c in run.certification.categories)
        if run.certification.status != "pass":
            return RealServerPublishProofReport(
                status="CERTIFICATION_FAILED", acquisition=report,
                partition_key=partition_key, artifact_path=str(artifact_path),
                coverage_status=coverage_status,
                certification_status=run.certification.status,
                certification_categories=categories,
                **dataset_manifest_fields,
            )

        eligibility = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
            PublicationEligibilityEvidence(
                dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,),
                storage_root_id,
                BYBIT_RESTART_RECONCILIATION_CERTIFICATION_PROFILE,
                BYBIT_RESTART_RECONCILIATION_CHECK_SUITE,
            )
        )
    finally:
        connection.close()

    connection = _connect_catalog(dsn)
    try:
        request = DataRequest(
            identity, Instant.parse(intent_start), Instant.parse(intent_end),
            schema_requirement=identity.record_schema_id,
            lifecycle_policy=LifecyclePolicy.VALID_ONLY,
            coverage_policy="strict",
            ordering_policy=BYBIT_ORDERING_PROVIDER.identity,
        )
        gateway = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
        scan = gateway.scan(request, batch_size=1000)
        read_records: list[Any] = []
        try:
            for batch in scan:
                read_records.extend(batch)
        finally:
            if getattr(scan, "completed_metadata", None) is None:
                scan.close()
    finally:
        connection.close()

    accepted_keys = {
        (Instant.parse(record.exchange_ts).isoformat(), record.trade_id)
        for record in accepted_records
    }
    read_keys = [
        (Instant.parse(record.exchange_ts).isoformat(), record.trade_id)
        for record in read_records
    ]
    read_matches_published = accepted_keys.issubset(set(read_keys)) and len(read_keys) == len(set(read_keys))

    return RealServerPublishProofReport(
        status="PASS", acquisition=report,
        partition_key=partition_key, artifact_path=str(artifact_path),
        coverage_status=coverage_status,
        certification_status=run.certification.status,
        certification_categories=categories,
        eligibility_published=eligibility is not None,
        datagateway_read_record_count=len(read_records),
        datagateway_read_matches_published=read_matches_published,
        durable_publication=DurablePublicationState(
            catalog_dataset_id=run.sealed_partition.dataset_id,
            partition_key=run.sealed_partition.natural_identity.partition_key,
            revision=run.sealed_partition.natural_identity.revision,
            partition_manifest_sha256=run.sealed_partition.manifest_sha256,
        ),
        coverage_id=coverage_id,
        coverage_assertion_id=coverage_assertion_id,
        **dataset_manifest_fields,
    )


@dataclass(frozen=True, slots=True)
class RestartOutcome:
    """K10 restart-procedure result (ADR-0042 S5)."""

    status: str  # "NO_CHECKPOINT" | "GAP_DETECTED" | "RESUMED"
    checkpoint: LiveCheckpointV1 | None
    reconcile_result: ReconnectResult | None
    accepted_records: tuple[TradeRecord, ...]


@dataclass(frozen=True, slots=True)
class RealServerRestartProofReport:
    """K10 bounded real restart proof over the existing K02 publish seam."""

    status: str
    publication: RealServerPublishProofReport | None
    checkpoint_path: str | None = None
    checkpoint_identity: str | None = None
    restart_outcome: RestartOutcome | None = None
    recent_records: int | None = None
    restart_accepted_records: int | None = None


def resume_live_ingest(
    *,
    checkpoint_store: CheckpointStore,
    recent_rest_records: tuple[TradeRecord, ...],
    buffered_ws_records: tuple[TradeRecord, ...],
    durable_publication: DurablePublicationState | None = None,
) -> RestartOutcome:
    """K10 restart procedure (ADR-0042 S5), composed entirely from existing
    seams: load + validate the durable checkpoint, then reconnect/reconcile
    through A11's existing bounded ``reconcile_after_disconnect`` -- never a
    second recovery path.

    Returns ``NO_CHECKPOINT`` when no checkpoint exists yet (a fresh
    domain: the caller should proceed as a first acquisition, not a
    restart). Returns ``GAP_DETECTED`` when the durable anchor is outside
    the provider's bounded reconciliation window -- an explicit
    non-complete gap, never fabricated continuity (ADR-0042 S5/S6); the
    checkpoint is left unadvanced. This status is an in-memory signal
    only -- it does *not* itself durably record the gap anywhere. The
    caller must separately record it through A11's existing coverage
    semantics (``build_bybit_live_coverage_document`` naturally produces a
    ``"known_gap"`` assertion once a session observes one; see
    ``reconcile_result.evidence`` here for the detail to attribute) before
    resuming governed publication, exactly as ADR-0042 S5 requires.
    Returns ``RESUMED`` when continuity is proven; ``accepted_records`` are
    the deduplicated, ordering-safe records recovered across the restart
    boundary, ready to canonicalize/publish exactly as any other bounded
    acquisition would (proof-matrix items 1-4, 10-13).

    ``durable_publication`` is required whenever a checkpoint exists to
    resume from (ADR-0042 S5 step 1: validating the checkpoint against the
    durable publication/catalog state it binds is a mandatory restart
    step, not an optional one -- a caller must not be able to silently
    skip it merely by omitting the argument). It is legitimately absent
    only when ``checkpoint_store`` has no checkpoint at all, since there is
    then nothing to validate.
    """
    checkpoint = checkpoint_store.load()
    if checkpoint is None:
        return RestartOutcome(status="NO_CHECKPOINT", checkpoint=None, reconcile_result=None, accepted_records=())

    if durable_publication is None:
        raise CheckpointError(
            "durable_publication is required to resume an existing checkpoint "
            "(ADR-0042 S5: the checkpoint must be validated against durable "
            "publication/catalog state before reconnect)"
        )
    validate_publication_binding(
        checkpoint,
        catalog_dataset_id=durable_publication.catalog_dataset_id,
        partition_key=durable_publication.partition_key,
        revision=durable_publication.revision,
        partition_manifest_sha256=durable_publication.partition_manifest_sha256,
    )

    last_durable_key = TradeKeyV1(
        checkpoint.dataset_identity.venue,
        checkpoint.dataset_identity.instrument,
        checkpoint.last_canonical_exchange_ts,
        checkpoint.last_canonical_trade_id,
    )
    result = reconcile_after_disconnect(
        last_durable_key=last_durable_key,
        recent_rest_records=recent_rest_records,
        buffered_ws_records=buffered_ws_records,
    )
    if result.status == ReconnectStatus.UNRESOLVED_GAP:
        return RestartOutcome(
            status="GAP_DETECTED", checkpoint=checkpoint, reconcile_result=result, accepted_records=(),
        )
    return RestartOutcome(
        status="RESUMED", checkpoint=checkpoint, reconcile_result=result,
        accepted_records=result.accepted_records,
    )


def next_checkpoint(
    previous: LiveCheckpointV1,
    *,
    last_record: TradeRecord,
    last_observed_sequence: str | None,
    durable_publication: DurablePublicationState,
    coverage_segment_id: str,
    coverage_status: str,
    created_at: Instant,
) -> LiveCheckpointV1:
    """Build and validate the next checkpoint generation from one more
    durably published canonical record (ADR-0042 S2: publication must
    already be durable before this is called -- callers advance a
    checkpoint only after a real S13/S14 publish, e.g. following
    ``run_real_server_publish_proof``). All monotonic-advancement rules are
    delegated to :func:`advance_checkpoint`; this only assembles the
    candidate from caller-supplied, already-durable evidence.

    ``coverage_status`` must be the exact status
    ``build_bybit_live_coverage_document`` assigned this segment
    (``"complete"`` or ``"known_gap"``). ADR-0042 S4 forbids bypassing an
    unresolved coverage interruption: a checkpoint must never advance to
    claim continuity across a segment durably recorded as ``known_gap``,
    even though the underlying publication itself is real and durable.

    ``last_record`` must belong to ``previous``'s own dataset domain.
    ``candidate`` keeps ``dataset_identity=previous.dataset_identity``
    regardless of what ``last_record`` actually is, so without this check
    a record from an unrelated venue/instrument would silently advance a
    checkpoint that still claims to represent the original domain --
    exactly the cross-domain misuse ADR-0042 S1's bound evidence exists to
    reject.
    """
    if (
        last_record.venue != previous.dataset_identity.venue
        or last_record.instrument != previous.dataset_identity.instrument
    ):
        raise CheckpointDomainMismatch(
            f"last_record ({last_record.venue}/{last_record.instrument}) does not "
            f"belong to the checkpoint's dataset domain "
            f"({previous.dataset_identity.venue}/{previous.dataset_identity.instrument})"
        )
    if coverage_status != "complete":
        raise CheckpointError(
            f"cannot advance checkpoint across a non-complete coverage segment "
            f"(coverage_status={coverage_status!r}); the gap must stay explicit"
        )
    candidate = LiveCheckpointV1(
        dataset_identity=previous.dataset_identity,
        source_semantics_id=previous.source_semantics_id,
        last_canonical_exchange_ts=Instant.parse(last_record.exchange_ts),
        last_canonical_trade_id=last_record.trade_id,
        last_observed_sequence=last_observed_sequence,
        catalog_dataset_id=durable_publication.catalog_dataset_id,
        partition_key=durable_publication.partition_key,
        revision=durable_publication.revision,
        partition_manifest_sha256=durable_publication.partition_manifest_sha256,
        coverage_segment_id=coverage_segment_id,
        generation=previous.generation + 1,
        created_at=created_at,
    )
    return advance_checkpoint(previous, candidate)


def load_current_durable_publication_state(
    *,
    checkpoint: LiveCheckpointV1,
    dsn: str | None,
) -> DurablePublicationState:
    """Read the current live catalog binding for a checkpoint's family.

    ``validate_publication_binding`` deliberately takes caller-supplied,
    freshly observed catalog values. A restart-only process must therefore
    query the catalog instead of deriving the binding from the checkpoint it
    is validating.
    """
    connection = _connect_catalog(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.partition_key, p.revision, p.manifest_sha256
                  FROM catalog.partitions p
                 WHERE p.dataset_id = %s
                   AND p.partition_key = %s
                   AND p.state IN (%s, %s, %s)
                 ORDER BY p.revision DESC
                 LIMIT 1
                """,
                (
                    checkpoint.catalog_dataset_id,
                    checkpoint.partition_key,
                    *K10_RESTART_DURABLE_STATES,
                ),
            )
            rows = cursor.fetchall()
    finally:
        connection.close()

    if not rows:
        raise CheckpointBindingError(
            "checkpoint's bound publication is absent from durable catalog state"
        )
    row = rows[0]
    return DurablePublicationState(
        catalog_dataset_id=checkpoint.catalog_dataset_id,
        partition_key=str(row[0]),
        revision=int(row[1]),
        partition_manifest_sha256=str(row[2]).strip(),
    )


def _persist_checkpoint_from_publication(
    *,
    publication: RealServerPublishProofReport,
    checkpoint_path: str | Path,
) -> tuple[CheckpointStore, LiveCheckpointV1] | None:
    if (
        publication.durable_publication is None
        or publication.coverage_id is None
        or not publication.acquisition.accepted_records
    ):
        return None

    store = CheckpointStore(checkpoint_path)
    previous = store.load()
    last_record = publication.acquisition.accepted_records[-1]
    if previous is None:
        checkpoint = advance_checkpoint(
            None,
            LiveCheckpointV1(
                dataset_identity=bybit_live_dataset_identity(),
                source_semantics_id=BYBIT_LIVE_SOURCE_SEMANTICS_V1,
                last_canonical_exchange_ts=Instant.parse(last_record.exchange_ts),
                last_canonical_trade_id=last_record.trade_id,
                last_observed_sequence=last_record.sequence,
                catalog_dataset_id=publication.durable_publication.catalog_dataset_id,
                partition_key=publication.durable_publication.partition_key,
                revision=publication.durable_publication.revision,
                partition_manifest_sha256=publication.durable_publication.partition_manifest_sha256,
                coverage_segment_id=publication.coverage_id,
                generation=1,
                created_at=Instant.parse(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
            ),
        )
    else:
        checkpoint = next_checkpoint(
            previous,
            last_record=last_record,
            last_observed_sequence=last_record.sequence,
            durable_publication=publication.durable_publication,
            coverage_segment_id=publication.coverage_id,
            coverage_status=publication.coverage_status or "",
            created_at=Instant.parse(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")),
        )
    store.save(checkpoint)
    return store, checkpoint


def run_real_server_restart_publish_phase(
    *,
    max_messages: int,
    max_seconds: float,
    storage_root: str | Path,
    storage_root_id: str,
    dsn: str | None,
    checkpoint_path: str | Path,
    producer: str,
    code_ref: str,
) -> RealServerRestartProofReport:
    """Publish one bounded real batch and durably persist the restart checkpoint.

    This phase intentionally returns immediately after the checkpoint write:
    it does not fetch the provider's recent-trade window and does not attempt
    restart reconciliation. Operators can then run the restart phase from a
    separate OS process against the same checkpoint path.
    """
    publication = run_real_server_publish_proof(
        max_messages=max_messages,
        max_seconds=max_seconds,
        storage_root=storage_root,
        storage_root_id=storage_root_id,
        dsn=dsn,
        producer=producer,
        code_ref=code_ref,
    )
    if publication.status != "PASS":
        return RealServerRestartProofReport(status=publication.status, publication=publication)

    persisted = _persist_checkpoint_from_publication(
        publication=publication,
        checkpoint_path=checkpoint_path,
    )
    if persisted is None:
        return RealServerRestartProofReport(
            status="REAL_RESTART_PROOF_PENDING",
            publication=publication,
        )
    store, checkpoint = persisted
    return RealServerRestartProofReport(
        status="CHECKPOINT_PERSISTED",
        publication=publication,
        checkpoint_path=str(store.path),
        checkpoint_identity=checkpoint.checkpoint_identity,
    )


def run_real_server_restart_phase(
    *,
    checkpoint_path: str | Path,
    dsn: str | None,
    storage_root: str | Path,
    storage_root_id: str,
    producer: str,
    code_ref: str,
    recent_limit: int = 1000,
    durable_publication: DurablePublicationState | None = None,
) -> RealServerRestartProofReport:
    """Load an existing checkpoint, reconcile, publish, then advance it.

    Standalone restart reads the current durable catalog binding for the
    checkpoint's partition family before fetching the provider's recent-trade
    window, so stale or superseded checkpoints fail closed before recovery
    work. When continuity is restored, the accepted reconciliation records
    are durably published under explicit reconciliation coverage before the
    checkpoint is advanced.
    """
    store = CheckpointStore(checkpoint_path)
    checkpoint = store.load()
    if checkpoint is None:
        return RealServerRestartProofReport(
            status="NO_CHECKPOINT_TO_RESTART_FROM",
            publication=None,
            checkpoint_path=str(store.path),
        )

    current_publication = durable_publication or load_current_durable_publication_state(
        checkpoint=checkpoint,
        dsn=dsn,
    )
    validate_publication_binding(
        checkpoint,
        catalog_dataset_id=current_publication.catalog_dataset_id,
        partition_key=current_publication.partition_key,
        revision=current_publication.revision,
        partition_manifest_sha256=current_publication.partition_manifest_sha256,
    )

    recent_records = fetch_recent_public_trades(limit=recent_limit)
    outcome = resume_live_ingest(
        checkpoint_store=store,
        recent_rest_records=recent_records,
        buffered_ws_records=(),
        durable_publication=current_publication,
    )
    status = "PASS" if outcome.status == "RESUMED" else outcome.status
    publication = None
    checkpoint_identity = (
        outcome.checkpoint.checkpoint_identity
        if outcome.checkpoint is not None
        else checkpoint.checkpoint_identity
    )
    if outcome.status == "RESUMED":
        if outcome.reconcile_result is None:
            raise CheckpointError("RESUMED restart outcome requires reconciliation evidence")
        publication = _publish_restart_reconciliation_records(
            accepted_records=outcome.accepted_records,
            reconcile_result=outcome.reconcile_result,
            storage_root=storage_root,
            storage_root_id=storage_root_id,
            dsn=dsn,
            producer=producer,
            code_ref=code_ref,
        )
        if publication.status != "PASS":
            status = publication.status
        else:
            persisted = _persist_checkpoint_from_publication(
                publication=publication,
                checkpoint_path=checkpoint_path,
            )
            if persisted is None:
                status = "REAL_RESTART_PROOF_PENDING"
            else:
                _store, advanced_checkpoint = persisted
                checkpoint_identity = advanced_checkpoint.checkpoint_identity
                outcome = RestartOutcome(
                    status=outcome.status,
                    checkpoint=advanced_checkpoint,
                    reconcile_result=outcome.reconcile_result,
                    accepted_records=outcome.accepted_records,
                )
    return RealServerRestartProofReport(
        status=status,
        publication=publication,
        checkpoint_path=str(store.path),
        checkpoint_identity=checkpoint_identity,
        restart_outcome=outcome,
        recent_records=len(recent_records),
        restart_accepted_records=len(outcome.accepted_records),
    )


def run_real_server_restart_proof(
    *,
    max_messages: int,
    max_seconds: float,
    storage_root: str | Path,
    storage_root_id: str,
    dsn: str | None,
    checkpoint_path: str | Path,
    producer: str,
    code_ref: str,
    recent_limit: int = 1000,
    phase: str = "both",
) -> RealServerRestartProofReport:
    """K10 real-server restart proof entry point.

    The function intentionally composes already-owned seams. ``phase=publish``
    performs only K02 publication plus checkpoint persistence, ``phase=restart``
    performs only checkpoint load plus bounded recent-trade reconciliation,
    and ``phase=both`` preserves the local/hermetic combined flow.
    """
    if phase not in {"publish", "restart", "both"}:
        raise ValueError("phase must be one of: publish, restart, both")

    if phase == "restart":
        return run_real_server_restart_phase(
            checkpoint_path=checkpoint_path,
            dsn=dsn,
            storage_root=storage_root,
            storage_root_id=storage_root_id,
            producer=producer,
            code_ref=code_ref,
            recent_limit=recent_limit,
        )

    published = run_real_server_restart_publish_phase(
        max_messages=max_messages,
        max_seconds=max_seconds,
        storage_root=storage_root,
        storage_root_id=storage_root_id,
        dsn=dsn,
        checkpoint_path=checkpoint_path,
        producer=producer,
        code_ref=code_ref,
    )
    if phase == "publish" or published.status != "CHECKPOINT_PERSISTED":
        return published

    restarted = run_real_server_restart_phase(
        checkpoint_path=checkpoint_path,
        dsn=dsn,
        storage_root=storage_root,
        storage_root_id=storage_root_id,
        producer=producer,
        code_ref=code_ref,
        recent_limit=recent_limit,
        durable_publication=(
            published.publication.durable_publication
            if published.publication is not None
            else None
        ),
    )
    return RealServerRestartProofReport(
        status=restarted.status,
        publication=published.publication,
        checkpoint_path=restarted.checkpoint_path,
        checkpoint_identity=restarted.checkpoint_identity,
        restart_outcome=restarted.restart_outcome,
        recent_records=restarted.recent_records,
        restart_accepted_records=restarted.restart_accepted_records,
    )


__all__ = [
    "BYBIT_PUBLIC_LINEAR_WS_URL",
    "BYBIT_RECENT_TRADES_URL",
    "DurablePublicationState",
    "K10_RESTART_DURABLE_STATES",
    "LiveProviderProofPending",
    "LiveProviderProofReport",
    "RealServerPublishProofReport",
    "RealServerRestartProofReport",
    "RestartOutcome",
    "build_arg_parser",
    "fetch_recent_public_trades",
    "load_current_durable_publication_state",
    "next_checkpoint",
    "resume_live_ingest",
    "run_bounded_live_provider_proof",
    "run_bounded_live_provider_proof_sync",
    "run_real_server_publish_proof",
    "run_real_server_restart_phase",
    "run_real_server_restart_publish_phase",
    "run_real_server_restart_proof",
]

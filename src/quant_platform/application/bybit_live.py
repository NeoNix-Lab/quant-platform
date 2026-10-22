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
    BybitLiveSessionEvidence,
    BybitLiveSourceError,
    BybitLiveTradeV1CertificationProfile,
    LiveSessionTracker,
    ReconnectResult,
    ReconnectStatus,
    TradeKeyV1,
    build_bybit_live_coverage_document,
    bybit_live_dataset_identity,
    bybit_live_topic,
    canonicalize_bybit_live_message,
    canonicalize_bybit_recent_public_trade,
    check_live_slice_coverage,
    deduplicate_live_records,
    reconcile_after_disconnect,
)
from quant_platform.source_adapters.bybit_live_slices import (
    SLICE_MAX_TRADES_DEFAULT,
    SLICE_SECONDS_DEFAULT,
    LiveSlice,
    plan_slices,
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
    # ADR-0075 section 3: the same slice window checks as the live profile.
    published_floor: Instant | None = None
    chain_start: Instant | None = None

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
        slices = check_live_slice_coverage(
            coverage_documents, published_floor=self.published_floor, chain_start=self.chain_start,
        )
        return {
            "source_semantics": BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1,
            "mapping": BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1,
            "documents": len(coverage_documents),
            "slices": slices,
        }

    def validate_records(self, identity, records: Sequence[TradeRecord]) -> Mapping[str, Any]:
        return self._live_profile().validate_records(identity, records)


def fetch_recent_public_trades(*, limit: int = 100, instrument: str = SUPPORTED_SYMBOL) -> tuple[Any, ...]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise BybitLiveSourceError("recent trade limit must be in [1, 1000]", field="limit")
    instrument = bybit_live_dataset_identity(instrument).instrument  # validates against the allow-list
    query = urlencode({"category": SUPPORTED_CATEGORY, "symbol": instrument, "limit": str(limit)})
    with urlopen(f"{BYBIT_RECENT_TRADES_URL}?{query}", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("retCode") != 0:
        raise LiveProviderProofPending(f"Bybit recent-trade request failed: {payload!r}")
    result = payload.get("result") or {}
    if result.get("category") != SUPPORTED_CATEGORY:
        raise BybitLiveSourceError("recent-trade response category does not match requested linear category")
    return tuple(canonicalize_bybit_recent_public_trade(item, instrument=instrument) for item in result.get("list") or ())


async def run_bounded_live_provider_proof(
    *,
    max_messages: int = 3,
    max_seconds: float = 20.0,
    instrument: str = SUPPORTED_SYMBOL,
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

    topic = bybit_live_topic(instrument)  # validates the instrument against the allow-list
    tracker = LiveSessionTracker(instrument)
    accepted = []
    errors: list[str] = []
    try:
        async with websockets.connect(BYBIT_PUBLIC_LINEAR_WS_URL, ping_interval=None, close_timeout=5) as socket:
            tracker.connected()
            await socket.send(json.dumps({"op": "subscribe", "args": [topic]}))
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
                    tracker.subscribed(topic=topic, conn_id=document.get("conn_id"))
                    continue
                if document.get("op") in {"pong", "ping"}:
                    tracker.heartbeat(conn_id=document.get("conn_id"))
                    continue
                if document.get("topic") != topic:
                    continue
                batch = canonicalize_bybit_live_message(document, instrument=instrument)
                tracker.observed_message(batch)
                accepted.extend(batch.records)
                messages += 1
    except TimeoutError:
        if not accepted:
            errors.append("bounded proof timed out before any trade messages arrived")
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
        topic=topic,
        messages=evidence.validated_messages,
        records=len(deduped),
        duplicates_removed=len(accepted) - len(deduped),
        final_state=evidence.final_state.value,
        errors=tuple(errors),
        accepted_records=deduped,
        session_evidence=evidence,
    )


def run_bounded_live_provider_proof_sync(
    *, max_messages: int = 3, max_seconds: float = 20.0, instrument: str = SUPPORTED_SYMBOL
) -> LiveProviderProofReport:
    return asyncio.run(
        run_bounded_live_provider_proof(max_messages=max_messages, max_seconds=max_seconds, instrument=instrument)
    )


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
    # ADR-0075: what a publication did. ``durable_publication`` and ``partition_key`` are those of the LAST slice (the one a
    # checkpoint binds); ``slice_keys`` lists every slice in order. ``ordering_violations`` counts trades of a live acquisition
    # before what is already published (nothing is published then); ``replayed_records`` counts trades dropped because their
    # slice is already published.
    slices: int = 0
    slice_keys: tuple[str, ...] = ()
    ordering_violations: int = 0
    replayed_records: int = 0


def _connect_catalog(dsn: str | None):
    import psycopg

    return psycopg.connect(dsn) if dsn else psycopg.connect()


def _published_floor(dsn: str | None, identity: Any) -> Instant | None:
    """The end of the latest durable partition of this dataset, read from the catalog (ADR-0075 section 4), or ``None``.

    A new window never starts inside coverage that is already declared, whatever declared it: a live slice, a day published
    before ADR-0075 (``dt=<day>``) or a day imported from the archive. A trade before this instant is a replay of a published
    slice or, on a live acquisition, an ordering violation.
    """

    connection = _connect_catalog(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT MAX(p.ts_end)
                  FROM catalog.partitions p
                  JOIN catalog.datasets d ON d.dataset_id = p.dataset_id
                 WHERE d.layer=%s AND d.kind=%s AND d.venue=%s
                   AND d.instrument=%s AND d.schema_id=%s
                   AND d.feature_set_def_id IS NULL
                   AND p.state IN (%s, %s, %s)
                """,
                (
                    identity.layer, identity.dataset_kind, identity.venue, identity.instrument,
                    identity.record_schema_id, *K10_RESTART_DURABLE_STATES,
                ),
            )
            value = cursor.fetchone()[0]
    finally:
        connection.close()
    return None if value is None else Instant.parse(value)


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


@dataclass(frozen=True, slots=True)
class _PublishedSlice:
    planned: LiveSlice
    run: Any
    coverage_id: str
    coverage_assertion_id: str
    coverage_status: str
    categories: tuple[tuple[str, str], ...]
    read_count: int
    eligibility_published: bool


def _read_back_slice(connection: Any, identity: Any, window: LiveSlice) -> list[Any]:
    """Read one slice's window through the DataGateway (valid partitions only, strict coverage)."""

    request = DataRequest(
        identity, window.window_start, window.window_end,
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
    return read_records


def _publish_slices(
    *,
    records: tuple[TradeRecord, ...],
    acquisition: LiveProviderProofReport,
    reconciling: bool,
    live_session_evidence: BybitLiveSessionEvidence | None,
    reconcile_result: ReconnectResult | None,
    storage_root: str | Path,
    storage_root_id: str,
    dsn: str | None,
    producer: str,
    code_ref: str,
    instrument: str,
    slice_seconds: int,
    slice_max_trades: int,
) -> RealServerPublishProofReport:
    """Publish the records of one acquisition as append-only slices (ADR-0075 sections 2 to 4).

    Each slice is revision 1 of its own partition ``dt=<day>/seg=<HHMMSS.mmm>``, certified and made eligible on its own and read
    back through the DataGateway; nothing is superseded. ``reconciling`` selects the restart reconciliation profile and
    coverage (evidence: ``reconcile_result``), otherwise the live session profile (evidence: ``live_session_evidence``). A slice
    already published is never republished: trades before the catalog's floor are a replay (reconciliation) or an ordering
    violation (live, fail closed).
    """

    pending_status = "REAL_RESTART_PROOF_PENDING" if reconciling else "LIVE_PROVIDER_PROOF_PENDING"
    identity = bybit_live_dataset_identity(instrument)
    floor = _published_floor(dsn, identity)
    plan = plan_slices(
        records, floor=floor, start_at_floor=reconciling and floor is not None,
        max_trades=slice_max_trades, slice_seconds=slice_seconds,
    )
    if plan.below_floor and not reconciling:
        return RealServerPublishProofReport(
            status="SLICE_ORDERING_VIOLATION", acquisition=acquisition, ordering_violations=len(plan.below_floor),
        )
    if not plan.slices:
        return RealServerPublishProofReport(
            status=pending_status, acquisition=acquisition, replayed_records=len(plan.below_floor),
        )

    storage_root_path = Path(storage_root)
    dataset_root = storage_root_path.joinpath(
        identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id,
    )
    dataset_manifest_path = dataset_root / "dataset-manifest.json"
    dataset_manifest_fields: dict[str, Any] = {}
    published: list[_PublishedSlice] = []
    previous_end: Instant | None = None
    for planned in plan.slices:
        outcome = _publish_one_slice(
            planned=planned, identity=identity, dataset_root=dataset_root,
            dataset_manifest_path=dataset_manifest_path, dataset_manifest_fields=dataset_manifest_fields,
            floor=floor, chain_start=previous_end, reconciling=reconciling,
            live_session_evidence=live_session_evidence, reconcile_result=reconcile_result,
            storage_root_id=storage_root_id, dsn=dsn, producer=producer, code_ref=code_ref,
            acquisition=acquisition, replayed_records=len(plan.below_floor),
        )
        if isinstance(outcome, RealServerPublishProofReport):
            return outcome
        published.append(outcome)
        previous_end = planned.window_end

    last = published[-1]
    total_read = sum(item.read_count for item in published)
    return RealServerPublishProofReport(
        status="PASS", acquisition=acquisition,
        partition_key=last.planned.partition_key,
        artifact_path=str(dataset_root / last.planned.partition_key / "part-001.parquet"),
        coverage_status=last.coverage_status,
        certification_status=last.run.certification.status,
        certification_categories=last.categories,
        eligibility_published=all(item.eligibility_published for item in published),
        datagateway_read_record_count=total_read,
        datagateway_read_matches_published=total_read == sum(len(item.planned.records) for item in published),
        durable_publication=DurablePublicationState(
            catalog_dataset_id=last.run.sealed_partition.dataset_id,
            partition_key=last.run.sealed_partition.natural_identity.partition_key,
            revision=last.run.sealed_partition.natural_identity.revision,
            partition_manifest_sha256=last.run.sealed_partition.manifest_sha256,
        ),
        coverage_id=last.coverage_id,
        coverage_assertion_id=last.coverage_assertion_id,
        slices=len(published),
        slice_keys=tuple(item.planned.partition_key for item in published),
        replayed_records=len(plan.below_floor),
        **dataset_manifest_fields,
    )


def _publish_one_slice(
    *,
    planned: LiveSlice,
    identity: Any,
    dataset_root: Path,
    dataset_manifest_path: Path,
    dataset_manifest_fields: dict[str, Any],
    floor: Instant | None,
    chain_start: Instant | None,
    reconciling: bool,
    live_session_evidence: BybitLiveSessionEvidence | None,
    reconcile_result: ReconnectResult | None,
    storage_root_id: str,
    dsn: str | None,
    producer: str,
    code_ref: str,
    acquisition: LiveProviderProofReport,
    replayed_records: int,
) -> RealServerPublishProofReport | _PublishedSlice:
    """S13 (seal, certify), S14 (eligibility) and a DataGateway read-back of one slice, as revision 1 of its own partition.

    Returns the failure report of the first step that did not pass, or the published slice.
    """

    key = planned.partition_key
    token = planned.token
    start_text, end_text = planned.window_start.isoformat(), planned.window_end.isoformat()
    rel_path = f"{key}/part-001.parquet"
    artifact_path = dataset_root / rel_path
    partition_manifest_path = dataset_root / key / "partition-manifest.json"
    coverage_manifest_path = dataset_root / key / "coverage-manifest.json"

    materialization = materialize_bybit_trade_v1(artifact_path, planned.records, dataset_identity=identity)
    # The dataset manifest describes the canonical DatasetIdentity as a whole (shared verbatim between the historical and live
    # producers), not one slice: it is registered once, and whatever is already durably registered at this shared path is
    # reused. Matches application/conformity.py's production emission (v2, origin=source_acquired).
    dataset_emission = None
    if not dataset_manifest_path.exists():
        dataset_emission = emit_dataset_manifest(
            dataset_manifest_path, dataset_identity=identity, created_at=end_text,
            schema_version=DATASET_MANIFEST_V2, origin="source_acquired",
            transform="canonicalize-trades-v1",
        )
        dataset_manifest_fields.update({
            "dataset_manifest_freshly_emitted": True,
            "dataset_manifest_sha256": dataset_emission.manifest_sha256,
            "dataset_manifest_rel_root": dataset_emission.document["rel_root"],
        })
    partition_emission = emit_partition_manifest(
        partition_manifest_path, materialization, dataset_identity=identity,
        dataset_root=dataset_root, partition_key=key, revision=1,
        rel_path=rel_path, created_at=start_text, closed_at=end_text,
        producer=producer, code_ref=code_ref,
    )
    # data/manifests.py's frozen _IDENTIFIER is `^[a-z0-9]+(?:[._-][a-z0-9]+)*$`.
    if reconciling:
        coverage_id = f"k10-restart-reconciliation-slice-{token}"
        assertion_id = f"k10-restart-reconciliation-slice-assertion-{token}"
        coverage_input = _build_restart_reconciliation_coverage_document(
            dataset_identity=identity, coverage_id=coverage_id,
            intent_start=start_text, intent_end=end_text,
            assertion_id=assertion_id,
            assertion_start=start_text, assertion_end=end_text,
            partition_key=key, revision=1, reconcile_result=reconcile_result,
            created_at=end_text, producer=producer, code_ref=code_ref,
        )
        profile: Any = BybitRestartReconciliationCertificationProfile(
            code_ref, published_floor=floor, chain_start=chain_start,
        )
        eligibility_profile = BYBIT_RESTART_RECONCILIATION_CERTIFICATION_PROFILE
        eligibility_suite = BYBIT_RESTART_RECONCILIATION_CHECK_SUITE
    else:
        coverage_id = f"live-slice-{token}"
        assertion_id = f"live-slice-assertion-{token}"
        coverage_input = build_bybit_live_coverage_document(
            dataset_identity=identity, coverage_id=coverage_id,
            intent_start=start_text, intent_end=end_text,
            assertion_id=assertion_id,
            assertion_start=start_text, assertion_end=end_text,
            partition_key=key, revision=1, session_evidence=live_session_evidence,
            created_at=end_text, producer=producer, code_ref=code_ref,
        )
        profile = BybitLiveTradeV1CertificationProfile(code_ref, published_floor=floor, chain_start=chain_start)
        eligibility_profile = BYBIT_LIVE_TRADE_V1_CERTIFICATION_PROFILE
        eligibility_suite = BYBIT_LIVE_TRADE_V1_CHECK_SUITE
    emit_coverage_manifest(
        coverage_manifest_path, dataset_identity=identity,
        partition_manifests=[partition_emission.document], **coverage_input,
    )
    coverage_status = coverage_input["assertions"][0]["status"]

    def failure(status: str, **fields: Any) -> RealServerPublishProofReport:
        return RealServerPublishProofReport(
            status=status, acquisition=acquisition, partition_key=key, artifact_path=str(artifact_path),
            coverage_status=coverage_status, replayed_records=replayed_records, **fields, **dataset_manifest_fields,
        )

    connection = _connect_catalog(dsn)
    try:
        try:
            run = PublicationCertification(CatalogPublicationWriter(connection), profile).run(
                SealedPartitionEvidence(
                    dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,),
                    artifact_path, storage_root_id,
                )
            )
        except CatalogPublicationConflict as exc:
            # Surface the freshly emitted dataset manifest's own hash/rel_root so an operator can decide whether to align
            # catalog.datasets to it (a lineage-pointer update, never touching existing sealed partitions) rather than crash.
            return failure("DATASET_MANIFEST_CONFLICT", certification_status=f"seal_refused: {exc}")
        categories = tuple((c.category, c.status) for c in run.certification.categories)
        if run.certification.status != "pass":
            return failure(
                "CERTIFICATION_FAILED", certification_status=run.certification.status,
                certification_categories=categories,
            )
        eligibility = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
            PublicationEligibilityEvidence(
                dataset_manifest_path, partition_manifest_path, (coverage_manifest_path,),
                storage_root_id, eligibility_profile, eligibility_suite,
            )
        )
    finally:
        connection.close()

    connection = _connect_catalog(dsn)
    try:
        read_records = _read_back_slice(connection, identity, planned)
    finally:
        connection.close()
    published_keys = sorted((r.exchange_ts.epoch_ns, r.trade_id) for r in planned.records)
    read_keys = sorted((r.exchange_ts.epoch_ns, r.trade_id) for r in read_records)
    if published_keys != read_keys:
        return failure(
            "SLICE_READ_BACK_MISMATCH", certification_status=run.certification.status,
            certification_categories=categories, datagateway_read_record_count=len(read_records),
            datagateway_read_matches_published=False,
        )
    return _PublishedSlice(
        planned, run, coverage_id, assertion_id, coverage_status, categories, len(read_records), eligibility is not None,
    )


def run_real_server_publish_proof(
    *,
    max_messages: int,
    max_seconds: float,
    storage_root: str | Path,
    storage_root_id: str,
    dsn: str | None,
    producer: str,
    code_ref: str,
    instrument: str = SUPPORTED_SYMBOL,
    slice_seconds: int = SLICE_SECONDS_DEFAULT,
    slice_max_trades: int = SLICE_MAX_TRADES_DEFAULT,
) -> RealServerPublishProofReport:
    """K02 (#108) minimum real-server proof, publishing append-only slices (ADR-0075).

    Reuses #107's bounded acquisition seam (never re-implements it), then publishes the acquired trades as slices through the
    same S13/S14/DataGateway production seams application/conformity.py already uses for the historical vertical, swapped to
    BybitLiveTradeV1CertificationProfile and live coverage.
    """
    report = run_bounded_live_provider_proof_sync(
        max_messages=max_messages, max_seconds=max_seconds, instrument=instrument,
    )
    if report.status != "PASS" or not report.accepted_records or report.session_evidence is None:
        return RealServerPublishProofReport(status="LIVE_PROVIDER_PROOF_PENDING", acquisition=report)
    return _publish_slices(
        records=report.accepted_records, acquisition=report, reconciling=False,
        live_session_evidence=report.session_evidence, reconcile_result=None, storage_root=storage_root, storage_root_id=storage_root_id, dsn=dsn,
        producer=producer, code_ref=code_ref, instrument=instrument,
        slice_seconds=slice_seconds, slice_max_trades=slice_max_trades,
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
    instrument: str = SUPPORTED_SYMBOL,
    slice_seconds: int = SLICE_SECONDS_DEFAULT,
    slice_max_trades: int = SLICE_MAX_TRADES_DEFAULT,
) -> RealServerPublishProofReport:
    topic = bybit_live_topic(instrument)
    if not accepted_records:
        return RealServerPublishProofReport(
            status="REAL_RESTART_PROOF_PENDING",
            acquisition=LiveProviderProofReport(
                status="REAL_RESTART_PROOF_PENDING",
                topic=topic,
                messages=0,
                records=0,
                duplicates_removed=0,
                final_state="RECONCILED",
                errors=("restart reconciliation produced no post-checkpoint records",),
            ),
        )
    report = LiveProviderProofReport(
        status="PASS",
        topic=topic,
        messages=0,
        records=len(accepted_records),
        duplicates_removed=0,
        final_state="RECONCILED",
        errors=(),
        accepted_records=accepted_records,
    )
    return _publish_slices(
        records=accepted_records, acquisition=report, reconciling=True, live_session_evidence=None,
        reconcile_result=reconcile_result,
        storage_root=storage_root, storage_root_id=storage_root_id, dsn=dsn, producer=producer, code_ref=code_ref,
        instrument=instrument, slice_seconds=slice_seconds, slice_max_trades=slice_max_trades,
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

    for record in (*recent_rest_records, *buffered_ws_records):
        if (record.venue, record.instrument) != (
            checkpoint.dataset_identity.venue, checkpoint.dataset_identity.instrument,
        ):
            raise CheckpointDomainMismatch(
                f"record ({record.venue}/{record.instrument}) does not belong to the checkpoint's dataset domain "
                f"({checkpoint.dataset_identity.venue}/{checkpoint.dataset_identity.instrument})"
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


def _require_checkpoint_domain(checkpoint: LiveCheckpointV1 | None, instrument: str) -> None:
    """A checkpoint is the recovery domain of one instrument (ADR-0042 S1, ADR-0074 section 2): an instance of another
    instrument pointed at it must stop before it acquires, publishes or reconciles anything."""

    if checkpoint is not None and checkpoint.dataset_identity != bybit_live_dataset_identity(instrument):
        raise CheckpointDomainMismatch(
            f"the checkpoint belongs to {checkpoint.dataset_identity.venue}/{checkpoint.dataset_identity.instrument}, "
            f"not to {instrument}"
        )


def _persist_checkpoint_from_publication(
    *,
    publication: RealServerPublishProofReport,
    checkpoint_path: str | Path,
    instrument: str = SUPPORTED_SYMBOL,
) -> tuple[CheckpointStore, LiveCheckpointV1] | None:
    if (
        publication.durable_publication is None
        or publication.coverage_id is None
        or not publication.acquisition.accepted_records
    ):
        return None

    store = CheckpointStore(checkpoint_path)
    previous = store.load()
    _require_checkpoint_domain(previous, instrument)
    last_record = publication.acquisition.accepted_records[-1]
    if (last_record.venue, last_record.instrument) != (
        bybit_live_dataset_identity(instrument).venue, instrument,
    ):
        raise CheckpointDomainMismatch(
            f"the published records ({last_record.venue}/{last_record.instrument}) are not those of {instrument}"
        )
    if previous is None:
        checkpoint = advance_checkpoint(
            None,
            LiveCheckpointV1(
                dataset_identity=bybit_live_dataset_identity(instrument),
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
    instrument: str = SUPPORTED_SYMBOL,
) -> RealServerRestartProofReport:
    """Publish one bounded real batch and durably persist the restart checkpoint.

    This phase intentionally returns immediately after the checkpoint write:
    it does not fetch the provider's recent-trade window and does not attempt
    restart reconciliation. Operators can then run the restart phase from a
    separate OS process against the same checkpoint path.

    An existing checkpoint of another instrument stops the phase before anything is acquired or published.
    """
    _require_checkpoint_domain(CheckpointStore(checkpoint_path).load(), instrument)
    publication = run_real_server_publish_proof(
        max_messages=max_messages,
        max_seconds=max_seconds,
        storage_root=storage_root,
        storage_root_id=storage_root_id,
        dsn=dsn,
        producer=producer,
        code_ref=code_ref,
        instrument=instrument,
    )
    if publication.status != "PASS":
        return RealServerRestartProofReport(status=publication.status, publication=publication)

    persisted = _persist_checkpoint_from_publication(
        publication=publication,
        checkpoint_path=checkpoint_path,
        instrument=instrument,
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
    instrument: str = SUPPORTED_SYMBOL,
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
    _require_checkpoint_domain(checkpoint, instrument)

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

    recent_records = fetch_recent_public_trades(limit=recent_limit, instrument=instrument)
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
            instrument=instrument,
        )
        if publication.status != "PASS":
            status = publication.status
        else:
            persisted = _persist_checkpoint_from_publication(
                publication=publication,
                checkpoint_path=checkpoint_path,
                instrument=instrument,
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
    instrument: str = SUPPORTED_SYMBOL,
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
            instrument=instrument,
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
        instrument=instrument,
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
        instrument=instrument,
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

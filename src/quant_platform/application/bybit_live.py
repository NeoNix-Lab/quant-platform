"""Application-owned bounded Bybit live proof orchestration."""

from __future__ import annotations

import argparse
import asyncio
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
    SUPPORTED_CATEGORY,
    SUPPORTED_SYMBOL,
    SUPPORTED_TOPIC,
    BybitLiveSessionEvidence,
    BybitLiveSourceError,
    BybitLiveTradeV1CertificationProfile,
    LiveSessionTracker,
    build_bybit_live_coverage_document,
    bybit_live_dataset_identity,
    canonicalize_bybit_live_message,
    canonicalize_bybit_recent_public_trade,
    deduplicate_live_records,
)


BYBIT_PUBLIC_LINEAR_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_RECENT_TRADES_URL = "https://api.bybit.com/v5/market/recent-trade"


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


def _connect_catalog(dsn: str | None):
    import psycopg

    return psycopg.connect(dsn) if dsn else psycopg.connect()


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
        dataset_root=dataset_root, partition_key=partition_key, revision=1,
        rel_path=rel_path, created_at=intent_start, closed_at=intent_end,
        producer=producer, code_ref=code_ref,
    )
    # data/manifests.py's frozen _IDENTIFIER is `^[a-z0-9]+(?:[._-][a-z0-9]+)*$`
    # -- lowercase only, no ISO "T"/"Z" separators.
    run_tag = f"{acquire_started_at.strftime('%Y%m%d')}-{suffix}"
    coverage_input = build_bybit_live_coverage_document(
        dataset_identity=identity, coverage_id=f"k02-real-server-{run_tag}",
        intent_start=intent_start, intent_end=intent_end,
        assertion_id=f"k02-real-server-assertion-{run_tag}",
        assertion_start=intent_start, assertion_end=intent_end,
        partition_key=partition_key, revision=1, session_evidence=session_evidence,
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
        **dataset_manifest_fields,
    )


__all__ = [
    "BYBIT_PUBLIC_LINEAR_WS_URL",
    "BYBIT_RECENT_TRADES_URL",
    "LiveProviderProofPending",
    "LiveProviderProofReport",
    "RealServerPublishProofReport",
    "build_arg_parser",
    "fetch_recent_public_trades",
    "run_bounded_live_provider_proof",
    "run_bounded_live_provider_proof_sync",
    "run_real_server_publish_proof",
]

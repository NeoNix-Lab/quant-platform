#!/usr/bin/env python3
"""Hermetic proof for finding 4 of the PR #112 adversarial review.

#107 Acceptance Criterion 10 requires accepted live records/evidence to
enter the *existing* canonical publication/quality mechanism rather than a
parallel live store. This proves live evidence actually flows through the
real, unmodified S13 PublicationCertification runtime via a dedicated
BybitLiveTradeV1CertificationProfile -- and that the frozen historical
profile (#107 AC3: "do not weaken or reinterpret the historical profile")
still refuses live-shaped evidence exactly as before.

Reuses FakeCatalogWriter from test_publication_certification_v1.py rather
than reimplementing a hermetic catalog double, per #107's "do not duplicate
... publication/catalog ... proof merely because A11 composes them."
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.data import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.publication import (  # noqa: E402
    PublicationCertification,
    SealedPartitionEvidence,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1CertificationProfile,
    build_bybit_trade_v1_source_extract_coverage,
    materialize_bybit_trade_v1,
)
from quant_platform.source_adapters.bybit_live import (  # noqa: E402
    BYBIT_LIVE_TRADE_V1_CHECK_SUITE,
    BybitLiveMessageEvidence,
    BybitLiveTradeV1CertificationProfile,
    LiveSessionTracker,
    LiveTradeBatch,
    build_bybit_live_coverage_document,
    canonicalize_bybit_live_trade,
)

from test_publication_certification_v1 import FakeCatalogWriter  # noqa: E402


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
START = "2024-01-15T00:00:00Z"
END = "2024-01-15T00:10:00Z"


def live_trade(timestamp: str, trade_id: str, sequence: str | None) -> TradeRecord:
    return TradeRecord(
        IDENTITY.venue, IDENTITY.instrument, Instant.parse(timestamp),
        "41731.10", "0.00400", "buy", None, trade_id, sequence,
    )


def healthy_session_evidence(records):
    """Drive a real LiveSessionTracker exactly as the WS proof loop does."""
    tracker = LiveSessionTracker()
    tracker.connected(conn_id="c1")
    tracker.subscribed(topic="publicTrade.BTCUSDT", conn_id="c1")
    for index, record in enumerate(records):
        row = {
            "T": Instant.parse(record.exchange_ts).epoch_ns // 1_000_000,
            "s": "BTCUSDT", "S": "Buy" if record.aggressor_side == "buy" else "Sell",
            "p": record.price, "v": record.size, "i": record.trade_id, "seq": int(record.sequence),
        }
        batch_record = canonicalize_bybit_live_trade(row)
        message_evidence = BybitLiveMessageEvidence(
            source_semantics_id="bybit-public-trades-websocket-v1",
            mapping_id="bybit-public-trade-live-v1", topic="publicTrade.BTCUSDT",
            provider_message_ts_ms=row["T"], trade_count=1,
            message_fingerprint_sha256="0" * 64,
        )
        tracker.observed_message(LiveTradeBatch((batch_record,), message_evidence))
    return tracker.evidence()


class BybitLiveCertificationProfileTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.data_path = self.root / "dt=2024-01-15" / "part-000.parquet"
        self.dataset_path = self.root / "dataset.json"
        self.partition_path = self.root / "partition.json"
        self.coverage_path = self.root / "coverage.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def emit_dataset_and_partition(self, materialization):
        emit_dataset_manifest(
            self.dataset_path, dataset_identity=IDENTITY,
            created_at="2026-09-23T10:00:00Z",
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        emit_partition_manifest(
            self.partition_path, materialization, dataset_identity=IDENTITY,
            dataset_root=self.root, partition_key="dt=2024-01-15", revision=1,
            rel_path="dt=2024-01-15/part-000.parquet",
            created_at="2026-09-23T10:00:00Z", closed_at="2026-09-23T10:00:01Z",
            producer="test-live-session", code_ref="producer-ref",
        )

    def live_evidence(self, records=None) -> SealedPartitionEvidence:
        """End-to-end: real session evidence -> build_bybit_live_coverage_document
        -> emit_coverage_manifest, exactly as production would compose them."""
        records = records if records is not None else [
            live_trade("2024-01-15T00:00:01Z", "a", "1001"),
            live_trade("2024-01-15T00:00:02Z", "b", "1002"),
        ]
        materialization = materialize_bybit_trade_v1(self.data_path, records, dataset_identity=IDENTITY)
        self.emit_dataset_and_partition(materialization)
        session_evidence = healthy_session_evidence(records)
        coverage_input = build_bybit_live_coverage_document(
            dataset_identity=IDENTITY, coverage_id="live-coverage-1",
            intent_start=START, intent_end=END,
            assertion_id="live-assertion-1", assertion_start=START, assertion_end=END,
            partition_key="dt=2024-01-15", revision=1, session_evidence=session_evidence,
            created_at="2026-09-23T10:00:02Z", producer="test-live-source", code_ref="source-ref",
        )
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY,
            partition_manifests=[json.loads(self.partition_path.read_text())],
            **coverage_input,
        )
        return SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,),
            self.data_path, "hot",
        )

    def historical_shaped_evidence(self) -> SealedPartitionEvidence:
        """The real historical builder's output, run through the live profile."""
        records = [live_trade("2024-01-15T00:00:01Z", "a", None), live_trade("2024-01-15T00:00:02Z", "b", None)]
        materialization = materialize_bybit_trade_v1(self.data_path, records, dataset_identity=IDENTITY)
        self.emit_dataset_and_partition(materialization)
        coverage_input = build_bybit_trade_v1_source_extract_coverage(
            dataset_identity=IDENTITY, coverage_id="hist-coverage-1",
            intent_start=START, intent_end=END,
            assertion_id="hist-assertion-1", assertion_start=START, assertion_end=END,
            partition_key="dt=2024-01-15", revision=1,
            source_extract_detail="sqlite extract complete",
            created_at="2026-09-23T10:00:02Z", producer="test-historical-source", code_ref="source-ref",
        )
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY,
            partition_manifests=[json.loads(self.partition_path.read_text())],
            **coverage_input,
        )
        return SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,),
            self.data_path, "hot",
        )

    def runtime(self, profile) -> PublicationCertification:
        return PublicationCertification(FakeCatalogWriter(), profile, batch_size=1)

    def category(self, run, name: str):
        return next(item for item in run.certification.categories if item.category == name)

    def test_live_evidence_passes_through_live_profile(self):
        run = self.runtime(BybitLiveTradeV1CertificationProfile("certifier-ref")).run(self.live_evidence())
        self.assertEqual(run.certification.status, "pass")
        self.assertEqual(run.sealed_partition.state, "closed")
        self.assertEqual(run.quality_report.status, "pass")
        self.assertEqual(run.quality_report.check_suite, BYBIT_LIVE_TRADE_V1_CHECK_SUITE)
        self.assertEqual(self.category(run, "source").status, "pass")
        self.assertEqual(self.category(run, "canonical").status, "pass")

    def test_historical_profile_refuses_live_coverage_semantics(self):
        # #107 AC3: the frozen historical profile must keep rejecting
        # anything that is not its own SQLite-extract source semantics --
        # live coverage must not be silently accepted through it.
        run = self.runtime(BybitTradeV1CertificationProfile("certifier-ref")).run(self.live_evidence())
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(self.category(run, "source").status, "fail")

    def test_live_profile_refuses_historical_coverage_semantics(self):
        run = self.runtime(BybitLiveTradeV1CertificationProfile("certifier-ref")).run(
            self.historical_shaped_evidence()
        )
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(self.category(run, "source").status, "fail")

    def test_live_profile_refuses_records_missing_sequence(self):
        # Coverage/session evidence and the physically materialized records
        # are independently asserted and independently re-verified by
        # PublicationCertification; a genuinely complete session (so the
        # partition can be sealed at all) plus a physical record with no
        # sequence isolates validate_records' own sequence requirement.
        records = [live_trade("2024-01-15T00:00:01Z", "a", None)]
        materialization = materialize_bybit_trade_v1(self.data_path, records, dataset_identity=IDENTITY)
        self.emit_dataset_and_partition(materialization)
        session_records = [live_trade("2024-01-15T00:00:01Z", "z", "9999")]
        coverage_input = build_bybit_live_coverage_document(
            dataset_identity=IDENTITY, coverage_id="live-coverage-no-seq",
            intent_start=START, intent_end=END,
            assertion_id="live-assertion-no-seq", assertion_start=START, assertion_end=END,
            partition_key="dt=2024-01-15", revision=1,
            session_evidence=healthy_session_evidence(session_records),
            created_at="2026-09-23T10:00:02Z", producer="test-live-source", code_ref="source-ref",
        )
        emit_coverage_manifest(
            self.coverage_path, dataset_identity=IDENTITY,
            partition_manifests=[json.loads(self.partition_path.read_text())],
            **coverage_input,
        )
        evidence = SealedPartitionEvidence(
            self.dataset_path, self.partition_path, (self.coverage_path,),
            self.data_path, "hot",
        )
        run = self.runtime(BybitLiveTradeV1CertificationProfile("certifier-ref")).run(evidence)
        self.assertEqual(run.certification.status, "fail")
        self.assertEqual(self.category(run, "canonical").status, "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)

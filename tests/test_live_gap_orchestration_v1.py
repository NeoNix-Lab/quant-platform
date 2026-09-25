#!/usr/bin/env python3
"""ADR-0044 long-gap orchestration v1 tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_live import RealServerRestartProofReport, RestartOutcome  # noqa: E402
from quant_platform.application.live_gap_orchestration import (  # noqa: E402
    BYBIT_PUBLIC_ARCHIVE_SOURCE_ID,
    REPAIR_SOURCE_EVALUATION_INCONCLUSIVE,
    REPAIR_SOURCE_UNPROVEN,
    SESSION_RESUMED_WITH_EXPLICIT_GAP,
    inconclusive_repair_source_evaluation,
    load_open_long_gap_records,
    long_gap_interval_from_restart_report,
    open_gap_payload,
    record_explicit_long_gap,
    repair_candidate_state_for_evaluation,
)
from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.source_adapters.bybit_live import ReconnectResult, ReconnectStatus  # noqa: E402


class LiveGapOrchestrationTests(unittest.TestCase):
    @staticmethod
    def _gap_report() -> RealServerRestartProofReport:
        return RealServerRestartProofReport(
            status="GAP_DETECTED",
            publication=None,
            checkpoint_path="/tmp/checkpoint.json",
            checkpoint_identity="live-checkpoint-v1:sha256:" + ("a" * 64),
            restart_outcome=RestartOutcome(
                status="GAP_DETECTED",
                checkpoint=None,
                accepted_records=(),
                reconcile_result=ReconnectResult(
                    status=ReconnectStatus.UNRESOLVED_GAP,
                    accepted_records=(),
                    evidence={
                        "reason": "last durable TradeKeyV1 absent from bounded recent-public-trades window",
                        "last_durable_key": {
                            "venue": "bybit",
                            "instrument": "BTCUSDT",
                            "exchange_ts": "2026-09-25T10:00:00.123456789Z",
                            "trade_id": "anchor",
                        },
                        "recent_window_records": 1000,
                        "buffered_ws_records": 0,
                    },
                ),
            ),
        )

    def test_records_known_gap_without_partition_and_unproven_source_evaluation(self):
        with tempfile.TemporaryDirectory() as tempdir:
            interval = long_gap_interval_from_restart_report(
                self._gap_report(), detected_at=Instant.parse("2026-09-25T10:00:02.987654321Z")
            )
            record = record_explicit_long_gap(
                storage_root=tempdir,
                interval=interval,
                producer="test",
                code_ref="test",
                detected_at=Instant.parse("2026-09-25T10:00:02.987654321Z"),
            )

            self.assertEqual(record.session_state, SESSION_RESUMED_WITH_EXPLICIT_GAP)
            self.assertEqual(record.interval_state, REPAIR_SOURCE_UNPROVEN)
            self.assertEqual(record.repair_source_evaluation.source_id, BYBIT_PUBLIC_ARCHIVE_SOURCE_ID)
            self.assertTrue(Path(record.coverage_manifest_path).exists())
            document = json.loads(Path(record.coverage_manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(document["assertions"][0]["status"], "known_gap")
            self.assertEqual(document["assertions"][0]["partitions"], [])
            self.assertEqual(document["acquisition"]["intent_start"], "2026-09-25T10:00:00.123456Z")
            self.assertEqual(document["acquisition"]["intent_end"], "2026-09-25T10:00:02.987655Z")
            self.assertIn("repair_state=REPAIR_SOURCE_UNPROVEN", document["assertions"][0]["evidence"][0]["detail"])
            self.assertEqual(open_gap_payload((record,))["open_gap_count"], 1)

    def test_repeated_detection_supersedes_prior_gap_manifest_for_same_anchor(self):
        with tempfile.TemporaryDirectory() as tempdir:
            first_interval = long_gap_interval_from_restart_report(
                self._gap_report(), detected_at=Instant.parse("2026-09-25T10:00:02Z")
            )
            first = record_explicit_long_gap(
                storage_root=tempdir,
                interval=first_interval,
                producer="test",
                code_ref="test",
                detected_at=Instant.parse("2026-09-25T10:00:02Z"),
            )
            second_interval = long_gap_interval_from_restart_report(
                self._gap_report(), detected_at=Instant.parse("2026-09-25T10:00:04Z")
            )
            second = record_explicit_long_gap(
                storage_root=tempdir,
                interval=second_interval,
                producer="test",
                code_ref="test",
                detected_at=Instant.parse("2026-09-25T10:00:04Z"),
            )

            self.assertEqual(second.supersedes_coverage_id, first.coverage_id)
            document = json.loads(Path(second.coverage_manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(document["supersedes"], first.coverage_id)
            self.assertEqual(document["assertions"][0]["start"], first_interval.coverage_start)
            self.assertEqual(document["assertions"][0]["end"], second_interval.coverage_end)
            loaded = load_open_long_gap_records(storage_root=tempdir)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].coverage_id, second.coverage_id)
            self.assertEqual(loaded[0].supersedes_coverage_id, first.coverage_id)
            self.assertEqual(loaded[0].interval.start_key.stable_dict(), second.interval.start_key.stable_dict())

    def test_inconclusive_source_evaluation_is_retryable_and_not_unproven(self):
        evaluation = inconclusive_repair_source_evaluation(
            source_id="candidate-source-v1", reason="source endpoint timed out before completeness proof"
        )

        self.assertFalse(evaluation.completed)
        self.assertEqual(evaluation.interval_state, REPAIR_SOURCE_EVALUATION_INCONCLUSIVE)
        self.assertEqual(
            repair_candidate_state_for_evaluation(evaluation),
            REPAIR_SOURCE_EVALUATION_INCONCLUSIVE,
        )


if __name__ == "__main__":
    unittest.main()

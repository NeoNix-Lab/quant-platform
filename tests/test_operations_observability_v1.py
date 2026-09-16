#!/usr/bin/env python3
"""K03 OperationalSignal v1 semantic runtime foundation proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant
from quant_platform.operations.observability import (
    EvidenceReference,
    HealthState,
    InvalidOperationalSignal,
    OperationalSignalV1,
    OPERATIONAL_SIGNAL_V1_SCHEMA_VERSION,
    SignalKind,
    SignalRelation,
    SubjectReference,
    build_signal_stream,
    compare_signals,
)


OBSERVED = Instant.parse("2026-09-14T10:30:00Z")
OBSERVED_LATER = Instant.parse("2026-09-14T10:31:00Z")
OCCURRED = Instant.parse("2026-09-14T10:29:55Z")
SUBJECT = SubjectReference("dataset", "canonical/trade/bybit/BTCUSDT")


def health_signal(health: HealthState, **overrides) -> OperationalSignalV1:
    kwargs = dict(
        subject=SUBJECT,
        capability_id="access.gateway",
        kind=SignalKind.HEALTH_SNAPSHOT,
        observed_at=OBSERVED,
        payload={"health": health.value},
    )
    kwargs.update(overrides)
    return OperationalSignalV1(**kwargs)


class SchemaAndEnvelopeTests(unittest.TestCase):
    def test_schema_version_is_fixed_and_not_caller_settable(self):
        signal = health_signal(HealthState.HEALTHY)
        self.assertEqual(OPERATIONAL_SIGNAL_V1_SCHEMA_VERSION, signal.schema_version)
        self.assertEqual("operational-signal-v1", signal.schema_version)

    def test_signal_is_frozen(self):
        signal = health_signal(HealthState.HEALTHY)
        with self.assertRaises(FrozenInstanceError):
            signal.capability_id = "other"  # type: ignore[misc]

    def test_subject_and_evidence_reference_require_non_empty_typed_identity(self):
        with self.assertRaises(InvalidOperationalSignal):
            SubjectReference("", "x")
        with self.assertRaises(InvalidOperationalSignal):
            SubjectReference("dataset", "")
        with self.assertRaises(InvalidOperationalSignal):
            EvidenceReference("manifest", "")


class DeterministicSignalIdentityTests(unittest.TestCase):
    def test_semantically_identical_inputs_produce_the_same_signal_id(self):
        a = health_signal(HealthState.HEALTHY)
        b = health_signal(HealthState.HEALTHY)
        self.assertEqual(a.signal_id, b.signal_id)

    def test_semantic_field_change_produces_a_different_signal_id(self):
        baseline = health_signal(HealthState.HEALTHY)
        variants = [
            health_signal(HealthState.DEGRADED),
            health_signal(HealthState.HEALTHY, capability_id="access.other"),
            health_signal(HealthState.HEALTHY, observed_at=OBSERVED_LATER),
            health_signal(HealthState.HEALTHY, subject=SubjectReference("dataset", "other")),
            health_signal(
                HealthState.HEALTHY, correlation_id="scan-1", sequence=1
            ),
        ]
        for variant in variants:
            with self.subTest(variant=variant.payload):
                self.assertNotEqual(baseline.signal_id, variant.signal_id)

    def test_diagnostic_only_metadata_does_not_change_identity(self):
        plain = health_signal(HealthState.HEALTHY)
        with_diagnostics = health_signal(
            HealthState.HEALTHY,
            diagnostics={
                "exporter_id": "otel-collector-7",
                "dashboard_id": "grafana-42",
                "presentation_text": "All systems nominal.",
            },
        )
        self.assertEqual(plain.signal_id, with_diagnostics.signal_id)
        self.assertEqual(
            "otel-collector-7", with_diagnostics.diagnostics["exporter_id"]
        )

    def test_equivalent_evidence_reference_ordering_yields_same_identity(self):
        e1 = EvidenceReference("manifest", "m-1")
        e2 = EvidenceReference("manifest", "m-2")
        first = health_signal(HealthState.HEALTHY, evidence=(e1, e2))
        second = health_signal(HealthState.HEALTHY, evidence=(e2, e1))
        self.assertEqual(first.signal_id, second.signal_id)
        self.assertEqual(first.evidence, second.evidence)

    def test_duplicate_evidence_references_collapse(self):
        e1 = EvidenceReference("manifest", "m-1")
        signal = health_signal(HealthState.HEALTHY, evidence=(e1, e1))
        self.assertEqual((e1,), signal.evidence)


class HealthSnapshotTests(unittest.TestCase):
    def test_healthy_degraded_failed_unknown_are_all_accepted(self):
        for health in HealthState:
            with self.subTest(health=health):
                signal = health_signal(health)
                self.assertEqual(health.value, signal.payload["health"])

    def test_missing_or_non_canonical_health_value_is_refused(self):
        with self.assertRaises(InvalidOperationalSignal):
            health_signal(HealthState.HEALTHY, payload={})
        with self.assertRaises(InvalidOperationalSignal):
            health_signal(HealthState.HEALTHY, payload={"health": "NOMINAL"})


class ObservationUnavailableTests(unittest.TestCase):
    def _unavailable(self, **payload) -> OperationalSignalV1:
        return OperationalSignalV1(
            subject=SUBJECT,
            capability_id="access.gateway",
            kind=SignalKind.OBSERVATION_UNAVAILABLE,
            observed_at=OBSERVED,
            payload=payload,
        )

    def test_reason_required(self):
        with self.assertRaises(InvalidOperationalSignal):
            self._unavailable()

    def test_supports_unknown_health(self):
        signal = self._unavailable(reason="observer offline", health=HealthState.UNKNOWN.value)
        self.assertEqual("UNKNOWN", signal.payload["health"])

    def test_cannot_claim_healthy_or_failed(self):
        with self.assertRaises(InvalidOperationalSignal):
            self._unavailable(reason="observer offline", health=HealthState.HEALTHY.value)
        with self.assertRaises(InvalidOperationalSignal):
            self._unavailable(reason="observer offline", health=HealthState.FAILED.value)


class FailureSignalTests(unittest.TestCase):
    def test_requires_stable_non_empty_failure_code_and_context(self):
        with self.assertRaises(InvalidOperationalSignal):
            OperationalSignalV1(
                subject=SUBJECT,
                capability_id="access.gateway",
                kind=SignalKind.FAILURE,
                observed_at=OBSERVED,
                payload={"context": {}},
            )
        with self.assertRaises(InvalidOperationalSignal):
            OperationalSignalV1(
                subject=SUBJECT,
                capability_id="access.gateway",
                kind=SignalKind.FAILURE,
                observed_at=OBSERVED,
                payload={"failure_code": "", "context": {}},
            )

    def test_accepts_machine_readable_code_with_structured_context(self):
        signal = OperationalSignalV1(
            subject=SUBJECT,
            capability_id="access.gateway",
            kind=SignalKind.FAILURE,
            observed_at=OBSERVED,
            payload={
                "failure_code": "SCHEMA_MISMATCH",
                "context": {"expected_schema_id": "trade-v1", "actual_schema_id": "trade-v0"},
            },
        )
        self.assertEqual("SCHEMA_MISMATCH", signal.payload["failure_code"])


class LifecycleTransitionTests(unittest.TestCase):
    def test_requires_states_and_non_empty_context(self):
        with self.assertRaises(InvalidOperationalSignal):
            OperationalSignalV1(
                subject=SUBJECT,
                capability_id="access.gateway",
                kind=SignalKind.LIFECYCLE_TRANSITION,
                observed_at=OBSERVED,
                payload={"previous_state": "open", "resulting_state": "reading"},
            )

    def test_accepts_owner_defined_opaque_states(self):
        signal = OperationalSignalV1(
            subject=SUBJECT,
            capability_id="access.gateway",
            kind=SignalKind.LIFECYCLE_TRANSITION,
            observed_at=OBSERVED,
            payload={
                "previous_state": "open",
                "resulting_state": "reading",
                "context": {"trigger": "first_batch_consumed"},
            },
        )
        self.assertEqual("open", signal.payload["previous_state"])


class TimeSemanticsTests(unittest.TestCase):
    def test_occurred_at_absent_is_accepted_and_not_fabricated(self):
        signal = health_signal(HealthState.HEALTHY)
        self.assertIsNone(signal.occurred_at)

    def test_occurred_at_must_be_a_canonical_instant_when_supplied(self):
        signal = health_signal(HealthState.HEALTHY, occurred_at=OCCURRED)
        self.assertEqual(OCCURRED, signal.occurred_at)
        with self.assertRaises(InvalidOperationalSignal):
            health_signal(HealthState.HEALTHY, occurred_at="2026-09-14T10:29:55Z")

    def test_observed_at_is_mandatory_and_must_be_canonical(self):
        with self.assertRaises(InvalidOperationalSignal):
            OperationalSignalV1(
                subject=SUBJECT,
                capability_id="access.gateway",
                kind=SignalKind.HEALTH_SNAPSHOT,
                observed_at="2026-09-14T10:30:00Z",  # not an Instant
                payload={"health": "HEALTHY"},
            )


class CorrelationAndSequenceTests(unittest.TestCase):
    def test_sequence_without_correlation_is_refused(self):
        with self.assertRaises(InvalidOperationalSignal):
            health_signal(HealthState.HEALTHY, sequence=1)

    def test_non_positive_sequence_is_refused(self):
        for bad in (0, -1):
            with self.subTest(sequence=bad):
                with self.assertRaises(InvalidOperationalSignal):
                    health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=bad)

    def test_standalone_snapshot_may_omit_correlation_and_sequence(self):
        signal = health_signal(HealthState.HEALTHY)
        self.assertIsNone(signal.correlation_id)
        self.assertIsNone(signal.sequence)


class SignalComparisonTests(unittest.TestCase):
    def test_equivalent_signal_at_same_position_is_a_duplicate(self):
        first = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        second = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        self.assertEqual(SignalRelation.DUPLICATE, compare_signals(first, second))

    def test_incompatible_signal_at_same_position_is_a_conflict(self):
        first = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        second = health_signal(HealthState.DEGRADED, correlation_id="scan-1", sequence=1)
        self.assertEqual(SignalRelation.CONFLICT, compare_signals(first, second))

    def test_incomparable_signals_are_refused(self):
        first = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        second = health_signal(HealthState.HEALTHY, correlation_id="scan-2", sequence=1)
        with self.assertRaises(InvalidOperationalSignal):
            compare_signals(first, second)


class SignalStreamTests(unittest.TestCase):
    def test_out_of_order_delivery_is_canonically_ordered_by_sequence(self):
        s3 = health_signal(HealthState.DEGRADED, correlation_id="scan-1", sequence=3)
        s1 = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        s2 = health_signal(HealthState.DEGRADED, correlation_id="scan-1", sequence=2)

        stream = build_signal_stream([s3, s1, s2])

        self.assertEqual((1, 2, 3), tuple(s.sequence for s in stream.ordered_signals))
        self.assertEqual((), stream.gaps)
        self.assertEqual((), stream.conflicts)

    def test_missing_sequence_reports_an_explicit_gap(self):
        s1 = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        s3 = health_signal(HealthState.DEGRADED, correlation_id="scan-1", sequence=3)

        stream = build_signal_stream([s1, s3])

        self.assertEqual(1, len(stream.gaps))
        self.assertEqual(2, stream.gaps[0].missing_sequence)

    def test_duplicate_delivery_at_the_same_sequence_collapses(self):
        first = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        duplicate = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)

        stream = build_signal_stream([first, duplicate])

        self.assertEqual(1, len(stream.ordered_signals))
        self.assertEqual((), stream.conflicts)

    def test_conflicting_delivery_at_the_same_sequence_is_explicit(self):
        first = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        conflicting = health_signal(HealthState.FAILED, correlation_id="scan-1", sequence=1)

        stream = build_signal_stream([first, conflicting])

        self.assertEqual(1, len(stream.conflicts))
        self.assertEqual(1, stream.conflicts[0].sequence)
        self.assertEqual(2, len(stream.conflicts[0].signals))

    def test_stream_requires_one_subject_and_correlation_id(self):
        one = health_signal(HealthState.HEALTHY, correlation_id="scan-1", sequence=1)
        other_subject = OperationalSignalV1(
            subject=SubjectReference("dataset", "other"),
            capability_id="access.gateway",
            kind=SignalKind.HEALTH_SNAPSHOT,
            observed_at=OBSERVED,
            payload={"health": "HEALTHY"},
            correlation_id="scan-1",
            sequence=2,
        )
        with self.assertRaises(InvalidOperationalSignal):
            build_signal_stream([one, other_subject])


class PayloadCanonicalizationTests(unittest.TestCase):
    def test_unsupported_payload_value_types_are_explicitly_refused(self):
        with self.assertRaises(InvalidOperationalSignal):
            health_signal(HealthState.HEALTHY, payload={"health": "HEALTHY", "ratio": 0.5})
        with self.assertRaises(InvalidOperationalSignal):
            health_signal(HealthState.HEALTHY, payload={"health": "HEALTHY", "tags": {1, 2}})

        class NotCanonical:
            pass

        with self.assertRaises(InvalidOperationalSignal):
            health_signal(
                HealthState.HEALTHY, payload={"health": "HEALTHY", "obj": NotCanonical()}
            )

    def test_nested_mapping_and_list_payload_values_are_supported(self):
        signal = health_signal(
            HealthState.HEALTHY,
            payload={
                "health": "HEALTHY",
                "context": {"partitions": ["p1", "p2"], "nested": {"b": 2, "a": 1}},
            },
        )
        self.assertEqual(("p1", "p2"), signal.payload["context"]["partitions"])


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)

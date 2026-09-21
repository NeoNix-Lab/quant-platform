#!/usr/bin/env python3
"""Canonical unit and integration tests for F07 outcome-derived labels, censoring and lockbox v1."""

from __future__ import annotations

from fractions import Fraction
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest
from quant_platform.validation import (
    CandidateClassification,
    DependencyCutoffRole,
    DependencyEvidence,
    DependencyLifecycle,
    DependencyMaturity,
    Embargo,
    LabelCensoringPolicy,
    LabelDefinition,
    LabelError,
    LabelResult,
    LabelStatus,
    LabelTransformKind,
    Lockbox,
    LockboxError,
    LockboxVisibility,
    OutcomeEvidence,
    OutcomeState,
    ValidationCandidate,
    WalkForwardFold,
    as_training_dependency_evidence,
    classify_candidate,
    evaluate_label,
)


class TestLabelDefinition(unittest.TestCase):
    """Canonical tests for LabelDefinition construction, validation and identity."""

    def test_identity_value_definition_valid(self):
        defn = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version="1",
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        self.assertEqual(defn.label_key, "returns.fwd_15m")
        self.assertEqual(defn.semantic_version, "1")
        self.assertEqual(defn.transform_kind, LabelTransformKind.IDENTITY_VALUE)
        self.assertEqual(defn.censoring_policy, LabelCensoringPolicy.REQUIRE_COMPLETE)
        self.assertTrue(defn.label_definition_id.startswith("label-definition-v1:sha256:"))

    def test_ordered_thresholds_definition_valid(self):
        defn = LabelDefinition(
            label_key="direction.ternary",
            semantic_version=1,
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "b" * 64,
            transform_kind="ordered_thresholds",
            parameters={
                "thresholds": ("-1/100", "1/100"),
                "classes": ("DOWN", "FLAT", "UP"),
            },
        )
        self.assertEqual(defn.transform_kind, LabelTransformKind.ORDERED_THRESHOLDS)
        self.assertEqual(defn.parameters["thresholds"], ("-1/100", "1/100"))
        self.assertEqual(defn.parameters["classes"], ("DOWN", "FLAT", "UP"))

    def test_definition_identity_deterministic_and_sensitive(self):
        d1 = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version="1",
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        d2 = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version=1,
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
            transform_kind="identity_value",
        )
        self.assertEqual(d1.label_definition_id, d2.label_definition_id)

        # Changing semantic_version changes identity
        d3 = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version="2",
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        self.assertNotEqual(d1.label_definition_id, d3.label_definition_id)

        # Changing source_outcome_spec_id changes identity
        d4 = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version="1",
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "c" * 64,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        self.assertNotEqual(d1.label_definition_id, d4.label_definition_id)

        # Changing threshold parameters changes identity
        t1 = LabelDefinition(
            label_key="direction.binary",
            semantic_version=1,
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
            transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
            parameters={"thresholds": ("0",), "classes": (0, 1)},
        )
        t2 = LabelDefinition(
            label_key="direction.binary",
            semantic_version=1,
            source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
            transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
            parameters={"thresholds": ("1/1000",), "classes": (0, 1)},
        )
        self.assertNotEqual(t1.label_definition_id, t2.label_definition_id)

    def test_invalid_keys_and_versions_rejected(self):
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="Invalid-Key",
                semantic_version="1",
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.IDENTITY_VALUE,
            )
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="valid.key",
                semantic_version="v1",
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.IDENTITY_VALUE,
            )

    def test_identity_value_rejects_parameters(self):
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="returns.fwd_15m",
                semantic_version="1",
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.IDENTITY_VALUE,
                parameters={"extra": 123},
            )

    def test_ordered_thresholds_validation(self):
        # Thresholds not strictly increasing
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="test.key",
                semantic_version=1,
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
                parameters={"thresholds": ("1/10", "1/10"), "classes": (0, 1, 2)},
            )
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="test.key",
                semantic_version=1,
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
                parameters={"thresholds": ("2/10", "1/10"), "classes": (0, 1, 2)},
            )
        # Class cardinality mismatch: N thresholds require N + 1 classes
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="test.key",
                semantic_version=1,
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
                parameters={"thresholds": ("0",), "classes": (0,)},
            )
        with self.assertRaises(LabelError):
            LabelDefinition(
                label_key="test.key",
                semantic_version=1,
                source_outcome_spec_id="outcome-spec-v1:sha256:" + "a" * 64,
                transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
                parameters={"thresholds": ("0",), "classes": (0, 1, 2)},
            )


class TestOutcomeEvidence(unittest.TestCase):
    """Canonical tests for Validation-owned OutcomeEvidence projection."""

    def test_complete_outcome_evidence_valid(self):
        ev = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "1" * 64,
            outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
            event_id="event-001",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="3/200",
        )
        self.assertEqual(ev.state, OutcomeState.COMPLETE)
        self.assertEqual(ev.realized_value, "3/200")

    def test_non_complete_outcome_must_not_carry_realized_value(self):
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.CENSORED_END_OF_DATA,
                realized_value="0",
            )
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.INSUFFICIENT_COVERAGE,
                realized_value="1/100",
            )

    def test_complete_outcome_requires_realized_value(self):
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.COMPLETE,
                realized_value=None,
            )

    def test_fraction_lowest_terms_enforced(self):
        # Reducible fraction rejected
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.COMPLETE,
                realized_value="2/4",
            )
        # Signed zero rejected
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.COMPLETE,
                realized_value="-0",
            )

    def test_temporal_invariants_enforced(self):
        # horizon_end < horizon_start rejected
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:15:00Z",
                horizon_end="2026-01-01T00:00:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.COMPLETE,
                realized_value="1/100",
            )
        # causal_available_at < horizon_end rejected
        with self.assertRaises(LabelError):
            OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + "1" * 64,
                outcome_spec_id="outcome-spec-v1:sha256:" + "2" * 64,
                event_id="event-001",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:14:59Z",
                state=OutcomeState.COMPLETE,
                realized_value="1/100",
            )


class TestEvaluateLabel(unittest.TestCase):
    """Canonical tests for evaluate_label under ADR-0036."""

    def setUp(self):
        self.spec_id = "outcome-spec-v1:sha256:" + "a" * 64
        self.id_defn = LabelDefinition(
            label_key="returns.continuous",
            semantic_version=1,
            source_outcome_spec_id=self.spec_id,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        self.thresh_defn = LabelDefinition(
            label_key="direction.ternary",
            semantic_version=1,
            source_outcome_spec_id=self.spec_id,
            transform_kind=LabelTransformKind.ORDERED_THRESHOLDS,
            parameters={
                "thresholds": ("-1/100", "1/100"),
                "classes": (-1, 0, 1),
            },
        )

    def test_identity_value_evaluation(self):
        ev = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "1" * 64,
            outcome_spec_id=self.spec_id,
            event_id="ev-1",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="7/1000",
        )
        result = evaluate_label(self.id_defn, ev)
        self.assertEqual(result.status, LabelStatus.LABELED)
        self.assertEqual(result.value, "7/1000")
        self.assertEqual(result.causal_available_at, ev.causal_available_at)
        self.assertTrue(result.result_id.startswith("label-result-v1:sha256:"))

    def test_ordered_thresholds_interval_semantics(self):
        # thresholds: -1/100, 1/100; classes: -1, 0, 1
        # x < -1/100 -> -1
        # -1/100 <= x < 1/100 -> 0
        # 1/100 <= x -> 1

        cases = [
            ("-1/50", -1),       # strictly below lower threshold (-1/50 < -1/100)
            ("-1/100", 0),       # exact lower threshold: belongs to UPPER interval!
            ("-1/200", 0),       # middle
            ("0", 0),            # zero
            ("99/10000", 0),     # just below upper threshold
            ("1/100", 1),        # exact upper threshold: belongs to UPPER interval!
            ("1/20", 1),         # above upper threshold (1/20 > 1/100)
        ]
        for val, expected_class in cases:
            ev = OutcomeEvidence(
                outcome_id="outcome-v1:sha256:" + hashlib.sha256(val.encode()).hexdigest(),
                outcome_spec_id=self.spec_id,
                event_id="ev-test",
                horizon_start="2026-01-01T00:00:00Z",
                horizon_end="2026-01-01T00:15:00Z",
                causal_available_at="2026-01-01T00:15:00Z",
                state=OutcomeState.COMPLETE,
                realized_value=val,
            )
            res = evaluate_label(self.thresh_defn, ev)
            self.assertEqual(
                res.value,
                expected_class,
                f"value {val} failed: expected class {expected_class}, got {res.value}",
            )

    def test_censored_and_insufficient_no_fabrication(self):
        # Censored
        ev_censored = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "c" * 64,
            outcome_spec_id=self.spec_id,
            event_id="ev-c",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.CENSORED_END_OF_DATA,
            realized_value=None,
        )
        res_censored = evaluate_label(self.thresh_defn, ev_censored)
        self.assertEqual(res_censored.status, LabelStatus.NO_VALUE_CENSORED)
        self.assertIsNone(res_censored.value)
        self.assertIsNone(res_censored.source_realized_value)

        # Insufficient coverage
        ev_insufficient = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "i" * 64,
            outcome_spec_id=self.spec_id,
            event_id="ev-i",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.INSUFFICIENT_COVERAGE,
            realized_value=None,
        )
        res_insufficient = evaluate_label(self.thresh_defn, ev_insufficient)
        self.assertEqual(res_insufficient.status, LabelStatus.NO_VALUE_INSUFFICIENT)
        self.assertIsNone(res_insufficient.value)

    def test_outcome_spec_mismatch_fails_closed(self):
        mismatched_ev = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "m" * 64,
            outcome_spec_id="outcome-spec-v1:sha256:" + "9" * 64,  # different spec
            event_id="ev-m",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="1/10",
        )
        with self.assertRaises(LabelError):
            evaluate_label(self.id_defn, mismatched_ev)

    def test_label_result_identity_sensitivity(self):
        ev = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "1" * 64,
            outcome_spec_id=self.spec_id,
            event_id="ev-1",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="7/1000",
        )
        res1 = evaluate_label(self.id_defn, ev)
        res2 = evaluate_label(self.id_defn, ev)
        self.assertEqual(res1.result_id, res2.result_id)

        # Different definition -> different identity
        res_diff_defn = evaluate_label(self.thresh_defn, ev)
        self.assertNotEqual(res1.result_id, res_diff_defn.result_id)

        # Different outcome value -> different identity
        ev_diff_val = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "1" * 64,
            outcome_spec_id=self.spec_id,
            event_id="ev-1",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="1/125",
        )
        res_diff_val = evaluate_label(self.id_defn, ev_diff_val)
        self.assertNotEqual(res1.result_id, res_diff_val.result_id)


class TestTargetSupportAndF06Integration(unittest.TestCase):
    """Canonical tests for target support projection and F06 purge/admission."""

    def test_target_support_preserves_consumed_terminal_instant(self):
        spec_id = "outcome-spec-v1:sha256:" + "a" * 64
        defn = LabelDefinition(
            label_key="returns.fwd",
            semantic_version=1,
            source_outcome_spec_id=spec_id,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        ev = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "1" * 64,
            outcome_spec_id=spec_id,
            event_id="ev-1",
            horizon_start="2026-01-01T00:00:00Z",
            horizon_end="2026-01-01T00:15:00Z",
            causal_available_at="2026-01-01T00:15:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="1/100",
        )
        result = evaluate_label(defn, ev)
        dep = as_training_dependency_evidence(result)

        self.assertEqual(dep.cutoff_role, DependencyCutoffRole.FOLD_COMPLETION)
        self.assertTrue(dep.sufficient)
        self.assertIsNotNone(dep.support)
        self.assertEqual(dep.support.start, Instant.parse("2026-01-01T00:00:00Z"))
        # End must be horizon_end + 1ns!
        expected_end = Instant(Instant.parse("2026-01-01T00:15:00Z").epoch_ns + 1)
        self.assertEqual(dep.support.end, expected_end)
        self.assertEqual(dep.causal_available_at, ev.causal_available_at)

    def test_critical_boundary_proof_horizon_end_equals_test_start_purges(self):
        """CRITICAL PROOF: if horizon_end == test_start, the target support consumed
        evidence at the held-out boundary and the training candidate MUST be PURGED by F06.
        """
        # Fold: train [00:00, 01:00), test [01:00, 02:00)
        fold = WalkForwardFold(
            fold_index=0,
            train=CoverageInterval(
                Instant.parse("2026-01-01T00:00:00Z"),
                Instant.parse("2026-01-01T01:00:00Z"),
            ),
            test=CoverageInterval(
                Instant.parse("2026-01-01T01:00:00Z"),
                Instant.parse("2026-01-01T02:00:00Z"),
            ),
        )
        embargo = Embargo(duration_ns=0)

        # Candidate decision time d = 00:45:00Z (inside train)
        # Target horizon ends exactly at test_start: 01:00:00Z!
        spec_id = "outcome-spec-v1:sha256:" + "a" * 64
        defn = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version=1,
            source_outcome_spec_id=spec_id,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        ev_boundary = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "b" * 64,
            outcome_spec_id=spec_id,
            event_id="ev-boundary",
            horizon_start="2026-01-01T00:45:00Z",
            horizon_end="2026-01-01T01:00:00Z",  # exactly test_start!
            causal_available_at="2026-01-01T01:00:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="1/200",
        )
        res_boundary = evaluate_label(defn, ev_boundary)
        target_dep = as_training_dependency_evidence(res_boundary)

        # Candidate with this target dependency
        candidate = ValidationCandidate(
            d=Instant.parse("2026-01-01T00:45:00Z"),
            dependencies=(target_dep,),
        )

        # Classification must be PURGED because target support [00:45, 01:00 + 1ns)
        # overlaps held-out test interval [01:00, 02:00)!
        classification = classify_candidate(fold, embargo, candidate)
        self.assertEqual(classification.classification, CandidateClassification.PURGED)
        self.assertIn(target_dep.identity, classification.reasons)

    def test_valid_training_target_before_boundary_is_admitted(self):
        """A training target ending strictly before test_start is ADMITTED."""
        fold = WalkForwardFold(
            fold_index=0,
            train=CoverageInterval(
                Instant.parse("2026-01-01T00:00:00Z"),
                Instant.parse("2026-01-01T01:00:00Z"),
            ),
            test=CoverageInterval(
                Instant.parse("2026-01-01T01:00:00Z"),
                Instant.parse("2026-01-01T02:00:00Z"),
            ),
        )
        embargo = Embargo(duration_ns=0)

        # Candidate d = 00:30:00Z, horizon ends at 00:45:00Z (strictly before test_start 01:00:00Z)
        spec_id = "outcome-spec-v1:sha256:" + "a" * 64
        defn = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version=1,
            source_outcome_spec_id=spec_id,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        ev_clean = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "c" * 64,
            outcome_spec_id=spec_id,
            event_id="ev-clean",
            horizon_start="2026-01-01T00:30:00Z",
            horizon_end="2026-01-01T00:45:00Z",
            causal_available_at="2026-01-01T00:45:00Z",
            state=OutcomeState.COMPLETE,
            realized_value="1/500",
        )
        res_clean = evaluate_label(defn, ev_clean)
        target_dep = as_training_dependency_evidence(res_clean)

        candidate = ValidationCandidate(
            d=Instant.parse("2026-01-01T00:30:00Z"),
            dependencies=(target_dep,),
        )

        classification = classify_candidate(fold, embargo, candidate)
        self.assertEqual(classification.classification, CandidateClassification.ADMITTED)

    def test_censored_target_emits_insufficient_support(self):
        """A censored source outcome yields sufficient=False and F06 INSUFFICIENT_SUPPORT."""
        fold = WalkForwardFold(
            fold_index=0,
            train=CoverageInterval(
                Instant.parse("2026-01-01T00:00:00Z"),
                Instant.parse("2026-01-01T01:00:00Z"),
            ),
            test=CoverageInterval(
                Instant.parse("2026-01-01T01:00:00Z"),
                Instant.parse("2026-01-01T02:00:00Z"),
            ),
        )
        embargo = Embargo(duration_ns=0)

        spec_id = "outcome-spec-v1:sha256:" + "a" * 64
        defn = LabelDefinition(
            label_key="returns.fwd_15m",
            semantic_version=1,
            source_outcome_spec_id=spec_id,
            transform_kind=LabelTransformKind.IDENTITY_VALUE,
        )
        ev_censored = OutcomeEvidence(
            outcome_id="outcome-v1:sha256:" + "c" * 64,
            outcome_spec_id=spec_id,
            event_id="ev-c",
            horizon_start="2026-01-01T00:30:00Z",
            horizon_end="2026-01-01T00:45:00Z",
            causal_available_at="2026-01-01T00:45:00Z",
            state=OutcomeState.CENSORED_END_OF_DATA,
        )
        res_censored = evaluate_label(defn, ev_censored)
        dep_censored = as_training_dependency_evidence(res_censored)
        self.assertFalse(dep_censored.sufficient)

        candidate = ValidationCandidate(
            d=Instant.parse("2026-01-01T00:30:00Z"),
            dependencies=(dep_censored,),
        )
        classification = classify_candidate(fold, embargo, candidate)
        self.assertEqual(classification.classification, CandidateClassification.INSUFFICIENT_SUPPORT)


class TestLockboxV1(unittest.TestCase):
    """Canonical tests for Lockbox v1 semantic isolation and reveal."""

    def setUp(self):
        self.holdout = CoverageInterval(
            Instant.parse("2026-02-01T00:00:00Z"),
            Instant.parse("2026-03-01T00:00:00Z"),
        )
        self.lockbox = Lockbox(lockbox_id="lockbox-2026-02", holdout=self.holdout)

    def test_lockbox_membership(self):
        self.assertTrue(self.lockbox.is_untouched)
        self.assertEqual(self.lockbox.visibility, LockboxVisibility.UNREVEALED)

        # Inside holdout
        self.assertTrue(self.lockbox.is_member("2026-02-01T00:00:00Z"))
        self.assertTrue(self.lockbox.is_member("2026-02-15T12:00:00Z"))
        # End is exclusive
        self.assertFalse(self.lockbox.is_member("2026-03-01T00:00:00Z"))
        # Before start
        self.assertFalse(self.lockbox.is_member("2026-01-31T23:59:59Z"))

    def test_development_candidate_inside_lockbox_rejected(self):
        candidate = ValidationCandidate(
            d=Instant.parse("2026-02-10T00:00:00Z"),  # inside lockbox!
            dependencies=(),
        )
        with self.assertRaises(LockboxError):
            self.lockbox.check_development_candidate(candidate)

    def test_development_candidate_with_support_intersecting_lockbox_rejected(self):
        # Candidate d = 2026-01-31T23:50:00Z (before lockbox)
        # But target support [23:50, 00:05 + 1ns) intersects lockbox start 00:00:00Z!
        target_dep = DependencyEvidence(
            identity="target-001",
            cutoff_role=DependencyCutoffRole.FOLD_COMPLETION,
            sufficient=True,
            support=CoverageInterval(
                Instant.parse("2026-01-31T23:50:00Z"),
                Instant.parse("2026-02-01T00:05:00.000000001Z"),
            ),
            required_maturity=DependencyMaturity.AVAILABLE,
            lifecycle=DependencyLifecycle.FINAL,
            causal_available_at=Instant.parse("2026-02-01T00:05:00Z"),
        )
        candidate = ValidationCandidate(
            d=Instant.parse("2026-01-31T23:50:00Z"),
            dependencies=(target_dep,),
        )
        with self.assertRaises(LockboxError):
            self.lockbox.check_development_candidate(candidate)

    def test_clean_development_candidate_accepted(self):
        # Candidate d = 2026-01-15T00:00:00Z, target ends 2026-01-15T01:00:00Z
        target_dep = DependencyEvidence(
            identity="target-clean",
            cutoff_role=DependencyCutoffRole.FOLD_COMPLETION,
            sufficient=True,
            support=CoverageInterval(
                Instant.parse("2026-01-15T00:00:00Z"),
                Instant.parse("2026-01-15T01:00:00.000000001Z"),
            ),
            required_maturity=DependencyMaturity.AVAILABLE,
            lifecycle=DependencyLifecycle.FINAL,
            causal_available_at=Instant.parse("2026-01-15T01:00:00Z"),
        )
        candidate = ValidationCandidate(
            d=Instant.parse("2026-01-15T00:00:00Z"),
            dependencies=(target_dep,),
        )
        # Should not raise
        self.lockbox.check_development_candidate(candidate)

    def test_reveal_transition_is_monotonic_and_irreversible(self):
        revealed = self.lockbox.reveal("End of validation campaign 2026-Q1")
        self.assertEqual(revealed.visibility, LockboxVisibility.REVEALED)
        self.assertFalse(revealed.is_untouched)
        self.assertIsNotNone(revealed.reveal_evidence)
        self.assertEqual(revealed.reveal_evidence["reason"], "End of validation campaign 2026-Q1")

        # Attempting to reveal again fails
        with self.assertRaises(LockboxError):
            revealed.reveal("Second reveal attempt")

        # Attempting to construct an UNREVEALED lockbox with reveal_evidence fails
        with self.assertRaises(LockboxError):
            Lockbox(
                lockbox_id="test",
                holdout=self.holdout,
                visibility=LockboxVisibility.UNREVEALED,
                reveal_evidence={"reason": "bad"},
            )

        # Attempting to construct a REVEALED lockbox without reveal_evidence fails
        with self.assertRaises(LockboxError):
            Lockbox(
                lockbox_id="test",
                holdout=self.holdout,
                visibility=LockboxVisibility.REVEALED,
                reveal_evidence=None,
            )


if __name__ == "__main__":
    unittest.main()

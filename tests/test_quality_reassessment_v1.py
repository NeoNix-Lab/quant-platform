"""DG-R R01: the argument rules of the reassessment writer (the catalog behaviour is the PostgreSQL gate)."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data import Instant  # noqa: E402
from quant_platform.data.quality_lifecycle import QualityLifecycleRefusal  # noqa: E402
from quant_platform.data.quality_reassessment import (  # noqa: E402
    REASSESSABLE_STATES,
    REASSESSMENT_STATUSES,
    LifecycleInputs,
    QualityReassessmentRefusal,
    reassess_live_partition,
)


class Untouchable:
    """A connection that must not be used: a refused argument never reaches the catalog."""

    def cursor(self):
        raise AssertionError("the catalog was touched")

    def commit(self):
        raise AssertionError("the catalog was touched")

    def rollback(self):
        raise AssertionError("the catalog was touched")


INPUTS = LifecycleInputs({}, "d" * 64, {}, "p" * 64, ("c",), ("a",), ("s",))
NOW = Instant.parse("2024-01-15T00:00:00Z")


def call(**overrides):
    arguments = dict(inputs=INPUTS, coverage_start=NOW, coverage_end=NOW, storage_root_id="hot", expected_profile="profile",
                     expected_check_suite="suite", status="fail", code_ref="commit", archive_identity={"url": "u"})
    arguments.update(overrides)
    return reassess_live_partition(Untouchable(), **arguments)


class ArgumentRuleTests(unittest.TestCase):
    def test_only_a_pass_or_a_fail_is_a_reassessment(self) -> None:
        self.assertEqual({"pass", "fail"}, set(REASSESSMENT_STATUSES))
        for status in ("warn", "closed", "", None):
            with self.assertRaises(QualityReassessmentRefusal):
                call(status=status)

    def test_the_producing_commit_is_required(self) -> None:
        for code_ref in ("", "   ", None):
            with self.assertRaises(QualityReassessmentRefusal):
                call(code_ref=code_ref)

    def test_a_fail_names_the_archive_it_is_measured_against_a_pass_does_not_need_one(self) -> None:
        for identity in (None, {}):
            with self.assertRaises(QualityReassessmentRefusal):
                call(archive_identity=identity)
        with self.assertRaises(AssertionError):  # a valid pass gets as far as the catalog
            call(status="pass", archive_identity=None)

    def test_the_states_a_reassessment_may_touch_exclude_closed_writing_and_superseded(self) -> None:
        self.assertEqual({"valid", "degraded", "invalid"}, set(REASSESSABLE_STATES))

    def test_a_refusal_is_a_lifecycle_refusal(self) -> None:
        self.assertTrue(issubclass(QualityReassessmentRefusal, QualityLifecycleRefusal))


if __name__ == "__main__":
    unittest.main()

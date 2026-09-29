from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quant_platform.application.storage_relocation import (
    RelocationCrashPoint,
    RelocationInterrupted,
    relocate_storage_tier,
)
from quant_platform.data.models import DatasetIdentity, Instant
from quant_platform.operations.relocation import RelocationPhase, RelocationPlan, RelocationRecordV1
from quant_platform.operations.relocation import RelocationVerificationFailed


PAYLOAD = b"k07 sealed partition bytes\n"
CONTENT_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()
REL_ROOT = "canonical/trades/bybit/BTCUSDT/trade-v1"
REL_PATH = "dt=2026-01-01/part-000.parquet"


class FakeRelocationCatalog:
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


def _plan() -> RelocationPlan:
    return RelocationPlan(
        dataset_identity=DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"),
        catalog_partition_id="22222222-2222-4222-8222-222222222222",
        partition_key="dt=2026-01-01",
        revision=1,
        source_storage_root_id="hot",
        target_storage_root_id="cold",
        dataset_rel_root=REL_ROOT,
        rel_path=REL_PATH,
        expected_content_sha256=CONTENT_SHA256,
        expected_size_bytes=len(PAYLOAD),
        partition_state="valid",
        pressure_decision_identity="pressure-decision-v1:sha256:" + "1" * 64,
        protection_assessment_identity="protection-assessment-v1:sha256:" + "2" * 64,
    )


def _write_source(root: Path, plan: RelocationPlan) -> Path:
    path = root / plan.dataset_rel_root / plan.rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PAYLOAD)
    return path


def _target_path(root: Path, plan: RelocationPlan) -> Path:
    return root / plan.dataset_rel_root / plan.rel_path


def _authoritative_path(catalog: FakeRelocationCatalog, hot: Path, cold: Path, plan: RelocationPlan) -> Path:
    root = hot if catalog.storage_root_id == "hot" else cold
    return root / plan.dataset_rel_root / plan.rel_path


class StorageRelocationApplicationTests(unittest.TestCase):
    def test_relocation_converges_to_target_and_removes_source_after_catalog_switch(self):
        with TemporaryDirectory() as tmp:
            plan = _plan()
            hot = Path(tmp) / "hot"
            cold = Path(tmp) / "cold"
            source = _write_source(hot, plan)
            catalog = FakeRelocationCatalog()

            record = relocate_storage_tier(
                plan=plan,
                catalog=catalog,
                source_storage_root=hot,
                target_storage_root=cold,
            )

            self.assertEqual(record.phase, RelocationPhase.CLEANED_UP)
            self.assertEqual(catalog.storage_root_id, "cold")
            self.assertFalse(source.exists())
            self.assertEqual(_target_path(cold, plan).read_bytes(), PAYLOAD)

    def test_every_crash_point_preserves_an_authoritative_readable_copy_and_resumes(self):
        for crash_point in RelocationCrashPoint:
            with self.subTest(crash_point=crash_point):
                with TemporaryDirectory() as tmp:
                    plan = _plan()
                    hot = Path(tmp) / "hot"
                    cold = Path(tmp) / "cold"
                    _write_source(hot, plan)
                    catalog = FakeRelocationCatalog()

                    with self.assertRaises(RelocationInterrupted):
                        relocate_storage_tier(
                            plan=plan,
                            catalog=catalog,
                            source_storage_root=hot,
                            target_storage_root=cold,
                            crash_after=crash_point,
                        )

                    authoritative = _authoritative_path(catalog, hot, cold, plan)
                    self.assertTrue(authoritative.is_file())
                    self.assertEqual(authoritative.read_bytes(), PAYLOAD)

                    record = relocate_storage_tier(
                        plan=plan,
                        catalog=catalog,
                        source_storage_root=hot,
                        target_storage_root=cold,
                    )

                    self.assertEqual(record.phase, RelocationPhase.CLEANED_UP)
                    self.assertEqual(catalog.storage_root_id, "cold")
                    self.assertEqual(_target_path(cold, plan).read_bytes(), PAYLOAD)

    def test_restart_from_verified_reruns_pre_switch_identity_check(self):
        with TemporaryDirectory() as tmp:
            plan = _plan()
            hot = Path(tmp) / "hot"
            cold = Path(tmp) / "cold"
            _write_source(hot, plan)
            target = _target_path(cold, plan)
            catalog = FakeRelocationCatalog()

            with self.assertRaises(RelocationInterrupted):
                relocate_storage_tier(
                    plan=plan,
                    catalog=catalog,
                    source_storage_root=hot,
                    target_storage_root=cold,
                    crash_after=RelocationCrashPoint.AFTER_VERIFIED,
                )
            target.write_bytes(b"tampered")

            with self.assertRaises(RelocationVerificationFailed):
                relocate_storage_tier(
                    plan=plan,
                    catalog=catalog,
                    source_storage_root=hot,
                    target_storage_root=cold,
                )
            self.assertEqual(catalog.storage_root_id, "hot")
            self.assertEqual((_authoritative_path(catalog, hot, cold, plan)).read_bytes(), PAYLOAD)


if __name__ == "__main__":
    unittest.main()

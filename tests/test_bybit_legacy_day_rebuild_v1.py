"""DG-R R02: the pure rules of the legacy-day rebuild (the catalog behaviour is the PostgreSQL gate)."""

from __future__ import annotations

from copy import deepcopy
import io
from contextlib import redirect_stderr
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.application import bybit_legacy_day_rebuild as rebuild  # noqa: E402
from quant_platform.application.bybit_archive_import import rebuild_partition_key  # noqa: E402
from quant_platform.data import Instant  # noqa: E402


def record(price="100.1", size="0.5", side="buy", ts_ns=1_700_000_000_123_000_000, venue="bybit", instrument="BTCUSDT"):
    return SimpleNamespace(price=price, size=size, aggressor_side=side, exchange_ts=Instant(ts_ns), venue=venue, instrument=instrument)


INCUMBENT = {
    "partition_id": "p", "key": "dt=2026-10-20", "revision": 7, "state": "valid", "rel_path": "dt=2026-10-20/part-007.parquet",
    "ts_start": "2026-10-20T00:00:00Z", "ts_end": "2026-10-21T00:00:00Z", "row_count": 10, "content_sha256": "a" * 64,
    "manifest_sha256": "b" * 64, "check_suite": "suite", "certification_profile": "profile",
    "partition_manifest_file": "dt=2026-10-20/partition-manifest-007.json", "coverage_manifest_file": "dt=2026-10-20/coverage-manifest-007.json",
    "coverage_manifest_sha256": "c" * 64,
}
ENTRY = {
    "day": "2026-10-20", "key_to_create": "dt=2026-10-20/seg=000000.000", "action": "retire+import", "refusals": [], "incumbent": INCUMBENT,
    "archive": {"file_name": "BTCUSDT2026-10-20.csv.gz", "sha256": "d" * 64, "rows": 3, "size_bytes": 9, "url": "u", "observed_at": "t"},
    "conservation": {"rows": 10, "days": {"2026-10-20": 10}, "duplicate_trade_ids": 0, "text_only_differences": 0},
}
PLAN = {"schema": rebuild.PLAN_SCHEMA, "instrument": "BTCUSDT", "storage_root_id": "hot", "code_ref": "commit", "days": [ENTRY]}


class RequestTests(unittest.TestCase):
    def test_the_days_are_named_never_chosen_and_bounded(self) -> None:
        self.assertEqual(["2026-10-19", "2026-10-20"], rebuild._validated_days(["2026-10-19", "2026-10-20"]))
        for bad in ([], ["2026-10-20", "2026-10-19"], ["2026-10-20", "2026-10-20"], ["2026-13-01"], ["20261020"], [""]):
            with self.assertRaises(rebuild.RebuildRefused):
                rebuild._validated_days(bad)
        days = [f"2026-09-{day:02d}" for day in range(1, 31)] + ["2026-10-01", "2026-10-02"]
        with self.assertRaises(rebuild.RebuildRefused):
            rebuild._validated_days(days)
        self.assertEqual(31, rebuild.MAX_DAYS_PER_PLAN)

    def test_only_btcusdt_and_the_fixed_key(self) -> None:
        self.assertEqual("BTCUSDT", rebuild.INSTRUMENT)
        self.assertEqual("dt=2026-10-20/seg=000000.000", rebuild_partition_key("2026-10-20"))


class ManifestNamingTests(unittest.TestCase):
    def test_a_revision_names_its_manifests_by_the_suffix_of_its_artifact(self) -> None:
        root = Path("/data")
        self.assertEqual((root / "dt=d/partition-manifest.json", root / "dt=d/coverage-manifest.json"),
                         rebuild._manifest_files(root, "dt=d/part-001.parquet"))
        self.assertEqual((root / "dt=d/partition-manifest-412.json", root / "dt=d/coverage-manifest-412.json"),
                         rebuild._manifest_files(root, "dt=d/part-412.parquet"))


class LegacyManifestLocationTests(unittest.TestCase):
    """The live publisher before the slices (#510) wrote its manifests in the dataset root, ``HHMMSS`` without the date."""

    def test_the_manifests_beside_the_artifact_or_in_the_dataset_root_by_the_catalog_hash(self) -> None:
        import hashlib

        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            (root / "dt=d").mkdir()
            rel = "dt=d/part-235942.parquet"
            mine = b'{"day": "d"}'
            digest = hashlib.sha256(mine).hexdigest()
            # nothing on disk: the first location is named, so that the refusal can say what is missing
            self.assertEqual((root / "dt=d/partition-manifest-235942.json", root / "dt=d/coverage-manifest-235942.json"),
                             rebuild._manifest_files(root, rel, digest))
            # the legacy layout: only the dataset root holds them
            (root / "partition-manifest-235942.json").write_bytes(mine)
            self.assertEqual((root / "partition-manifest-235942.json", root / "coverage-manifest-235942.json"),
                             rebuild._manifest_files(root, rel, digest))
            # a file of the same second of another day overwrote the root one: it is still the only one found, and the caller
            # (the evidence check) refuses it because it does not hash to the catalog's
            (root / "partition-manifest-235942.json").write_bytes(b'{"day": "other"}')
            found = rebuild._manifest_files(root, rel, digest)[0]
            self.assertEqual(root / "partition-manifest-235942.json", found)
            self.assertNotEqual(digest, hashlib.sha256(found.read_bytes()).hexdigest())
            # both locations hold one: the one with the catalog's hash wins, whichever it is
            (root / "dt=d/partition-manifest-235942.json").write_bytes(mine)
            self.assertEqual(root / "dt=d/partition-manifest-235942.json", rebuild._manifest_files(root, rel, digest)[0])
            (root / "dt=d/partition-manifest-235942.json").write_bytes(b'{"day": "x"}')
            (root / "partition-manifest-235942.json").write_bytes(mine)
            self.assertEqual(root / "partition-manifest-235942.json", rebuild._manifest_files(root, rel, digest)[0])


class DuplicateTradeIdTests(unittest.TestCase):
    def conservation(self, *records):
        with mock.patch.object(rebuild, "scan_trade_v1_all", lambda *a, **k: iter([list(records)])):
            return rebuild._conservation(Path("incumbent.parquet"), "2026-10-20", Path("."))

    def with_id(self, trade_id, **kwargs):
        item = record(**kwargs)
        item.trade_id = trade_id
        return item

    def test_a_trade_id_twice_with_different_content_refuses_the_day(self) -> None:
        refusals, summary, outside = self.conservation(self.with_id("t1", price="100.1"), self.with_id("t1", price="100.2"))
        self.assertEqual(({}, {}), (summary, outside))
        self.assertEqual(1, len(refusals))
        self.assertIn("twice with different content", refusals[0])

    def test_a_trade_id_twice_with_the_same_content_is_counted_not_hidden(self) -> None:
        refusals, summary, _outside = self.conservation(self.with_id("t1"), self.with_id("t1"))
        # the archive file of the day is absent in this directory, so the day still refuses; the duplicate is counted
        self.assertEqual(1, summary["duplicate_trade_ids"])
        self.assertEqual(1, summary["rows"])
        self.assertFalse(any("twice" in item for item in refusals))


def dependent(day, depends, action="retire+import"):
    """A plan entry whose incumbent holds records of other days; ``depends`` maps each such day to whether a valid partition holds them."""

    return {"day": day, "action": action, "refusals": [], "conservation": {"days": {d: 2 for d in depends}, "outside_held": dict(depends)}}


class DependencyTests(unittest.TestCase):
    """The records of another day must stay served: held by a valid partition there, or that day rebuilt by this very plan."""

    def resolve(self, *entries):
        entries = list(entries)
        rebuild._resolve_dependencies(entries)
        return {e["day"]: e["action"] for e in entries}

    def test_records_held_by_a_valid_partition_of_their_day_stay_served(self) -> None:
        self.assertEqual({"a": "retire+import"}, self.resolve(dependent("a", {"b": True})))

    def test_records_held_nowhere_refuse_the_day_before_the_retire(self) -> None:
        entry = dependent("a", {"b": False})
        self.assertEqual({"a": "refused"}, self.resolve(entry))
        self.assertIn("they would stop being served", entry["refusals"][0])
        self.assertIn("not in this plan", entry["refusals"][0])

    def test_a_day_of_the_plan_serves_them_only_if_it_is_not_refused(self) -> None:
        self.assertEqual({"a": "retire+import", "b": "retire+import"}, self.resolve(dependent("a", {"b": False}), dependent("b", {})))
        refused = dependent("b", {}, action="refused")
        entry = dependent("a", {"b": False})
        self.assertEqual({"a": "refused", "b": "refused"}, self.resolve(entry, refused))
        self.assertIn("refused in this plan", entry["refusals"][0])
        # a refused day changes nothing, so what its valid partition holds is still served
        self.assertEqual({"a": "retire+import", "b": "refused"}, self.resolve(dependent("a", {"b": True}), dependent("b", {}, action="refused")))

    def test_a_refusal_propagates_along_the_chain(self) -> None:
        a, b = dependent("a", {"b": False}), dependent("b", {"c": False})
        self.assertEqual({"a": "refused", "b": "refused"}, self.resolve(a, b))

    def test_a_day_already_retired_is_not_judged_again(self) -> None:
        self.assertEqual({"a": "import"}, self.resolve(dependent("a", {"b": False}, action="import")))


class DifferenceTests(unittest.TestCase):
    def test_a_value_difference_is_a_difference_and_a_trailing_zero_is_not(self) -> None:
        self.assertEqual(([], False), rebuild._differences(record(), record()))
        self.assertEqual(([], True), rebuild._differences(record(price="100.10"), record(price="100.1")))
        self.assertEqual(([], True), rebuild._differences(record(size="0.50"), record(size="0.5")))
        for kwargs, name in (({"price": "100.2"}, "price_value"), ({"size": "0.6"}, "size_value"), ({"side": "sell"}, "aggressor_side"),
                             ({"venue": "x"}, "venue"), ({"instrument": "ETHUSDT"}, "instrument")):
            self.assertEqual(([name], False), rebuild._differences(record(), record(**kwargs)), name)

    def test_the_exchange_time_is_compared_at_the_millisecond_of_the_archive(self) -> None:
        archive = record(ts_ns=1_700_000_000_123_000_000)
        self.assertEqual(([], False), rebuild._differences(archive, record(ts_ns=1_700_000_000_123_456_789)))  # the live row is truncated
        self.assertEqual((["exchange_ts_ms"], False), rebuild._differences(archive, record(ts_ns=1_700_000_000_124_000_000)))


class DigestTests(unittest.TestCase):
    def digest(self, plan) -> str:
        return rebuild._digest(plan)

    def test_the_digest_binds_what_the_operation_depends_on(self) -> None:
        base = self.digest(PLAN)
        changes = {
            "code_ref": lambda p: p.update(code_ref="other"),
            "storage_root": lambda p: p.update(storage_root_id="cold"),
            "archive hash": lambda p: p["days"][0]["archive"].update(sha256="e" * 64),
            "archive observed": lambda p: p["days"][0]["archive"].update(observed_at="later"),
            "incumbent hash": lambda p: p["days"][0]["incumbent"].update(content_sha256="f" * 64),
            "incumbent partition": lambda p: p["days"][0]["incumbent"].update(partition_id="q"),
            "incumbent revision": lambda p: p["days"][0]["incumbent"].update(revision=8),
            "incumbent coverage": lambda p: p["days"][0]["incumbent"].update(coverage_manifest_sha256="0" * 64),
            "incumbent coverage ids": lambda p: p["days"][0]["incumbent"].update(coverage_ids=["c1"]),
            "incumbent assertion ids": lambda p: p["days"][0]["incumbent"].update(assertion_ids=["a1"]),
            "conservation": lambda p: p["days"][0]["conservation"].update(rows=11),
            "neighbour archive": lambda p: p["days"][0]["conservation"].update(archives={"2026-10-19": "e" * 64}),
            "records held elsewhere": lambda p: p["days"][0]["conservation"].update(outside_held={"2026-10-19": True}),
            "refusals": lambda p: p["days"][0].update(refusals=["not closed"]),
            "key": lambda p: p["days"][0].update(key_to_create="dt=2026-10-20/seg=000001.000"),
            "days": lambda p: p["days"].append(deepcopy(p["days"][0]) | {"day": "2026-10-21"}),
        }
        for name, change in changes.items():
            changed = deepcopy(PLAN)
            change(changed)
            self.assertNotEqual(base, self.digest(changed), name)

    def test_progress_is_not_part_of_the_digest(self) -> None:
        base = self.digest(PLAN)
        for change in (lambda p: p["days"][0].update(action="import"), lambda p: p["days"][0]["incumbent"].update(state="invalid"),
                       lambda p: p["days"][0].update(created={"partition_id": "n", "state": "valid"}, action="verify"),
                       lambda p: p.update(digest="anything")):
            moved = deepcopy(PLAN)
            change(moved)
            self.assertEqual(base, self.digest(moved))


class CommandLineTests(unittest.TestCase):
    def run_cli(self, argv, environment=None):
        import rebuild_bybit_legacy_days as tool

        with mock.patch.dict(os.environ, environment or {}, clear=False), redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as raised:
                tool.main(argv)
        return raised.exception.code, error.getvalue()

    def test_a_missing_dsn_variable_and_an_existing_plan_file_are_usage_errors(self) -> None:
        plan_args = ["plan", "--days", "2026-10-20", "--archive-dir", ".", "--checkpoint-path", "c.json", "--code-ref", "x"]
        os.environ.pop("REBUILD_TEST_DSN", None)
        code, message = self.run_cli(["--dsn-env", "REBUILD_TEST_DSN", *plan_args, "--out", "plan.json"])
        self.assertEqual(2, code)
        self.assertIn("REBUILD_TEST_DSN", message)
        with tempfile.TemporaryDirectory() as holder:
            existing = Path(holder) / "plan.json"
            existing.write_text("{}")
            code, message = self.run_cli(["--dsn-env", "REBUILD_TEST_DSN", *plan_args, "--out", str(existing)], {"REBUILD_TEST_DSN": "dbname=x"})
            self.assertEqual(2, code)
            self.assertIn("never overwritten", message)

    def test_the_dsn_is_never_taken_from_the_command_line(self) -> None:
        code, message = self.run_cli(["--dsn-env", "dbname=x password=y", "verify", "--days", "2026-10-20", "--archive-dir", "."])
        self.assertEqual(2, code)
        self.assertIn("not a connection string", message)


class RunbookTests(unittest.TestCase):
    def test_the_runbook_documents_the_four_commands_the_refusals_and_the_way_back(self) -> None:
        text = (ROOT / "docs" / "engineering" / "BYBIT_ARCHIVE_IMPORT_RUNBOOK.md").read_text(encoding="utf-8")
        section = " ".join(text.split("## 6. Rebuild damaged legacy live days", 1)[1].split())
        for needed in ("tools/rebuild_bybit_legacy_days.py", " plan ", " execute ", " verify ", " restore ", "--digest", "STALE",
                       "dt=<day>/seg=000000.000", "legacy_publication_incomplete_day", "not closed in UTC", "conservation",
                       "no partial acceptance", "never a `--force`", "does not run itself, choose days, delete", "Start with `2026-10-20`",
                       "stops at the first day that fails"):
            self.assertIn(needed, section, needed)
        self.assertTrue((ROOT / "tools" / "rebuild_bybit_legacy_days.py").is_file())


if __name__ == "__main__":
    unittest.main()

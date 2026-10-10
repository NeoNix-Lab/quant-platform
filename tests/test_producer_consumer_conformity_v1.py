#!/usr/bin/env python3
"""Contract/governance checks for the Producer-Consumer Conformity Gate v1.

IMPORTANT — what this suite is and is not:

Every test below is a CONTRACT-CONSISTENCY check: it verifies that ADR-0023,
docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md, the governance documents and
the existing narrow DataGateway v1 implementation agree with each other and
have not silently drifted apart. It does NOT implement, exercise or prove any
runtime behavior (no Parquet writer, no bridge or no certifier is exercised
here). A passing test here means "the frozen
documents are internally consistent," never "the described runtime behavior
has been observed to work." The required future BEHAVIORAL/RUNTIME tests
(bounded memory growth, CanonicalContentHashV1 reproducibility against a real
Parquet artifact, certification refusing a null trade_id in practice, catalog
rebuild equality against a real rebuilt database, etc.) are enumerated in
PRODUCER_CONSUMER_CONFORMITY.md S20 and are deliberately not implemented
here; implementing them is Conformity Implementation Gate work (ADR-0023 S7),
not Contract Freeze Gate work.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from quant_platform.access.models import DataRequest  # noqa: E402
from quant_platform.data.parquet import _OPTIONAL, _REQUIRED  # noqa: E402
from golden_conformity_support import load_golden_expectation  # noqa: E402

ADR_PATH = ROOT / "docs" / "decisions" / "ADR-0023-producer-consumer-conformity-gate-v1.md"
ADR_0024_PATH = ROOT / "docs" / "decisions" / "ADR-0024-package-boundary-modular-monolith-v1.md"
CONTRACT_PATH = ROOT / "docs" / "contracts" / "PRODUCER_CONSUMER_CONFORMITY.md"
DECISIONS_INDEX = ROOT / "docs" / "decisions" / "README.md"
DOCS_INDEX = ROOT / "docs" / "README.md"
ROADMAP_PATH = ROOT / "docs" / "product" / "ROADMAP.md"
CAPABILITY_MAP_PATH = ROOT / "docs" / "product" / "CAPABILITY_MAP.md"
OPEN_DECISIONS_PATH = ROOT / "docs" / "architecture" / "OPEN_DECISIONS.md"
GOLDEN_INTEGRATION_TEST = ROOT / "tests" / "integration_bybit_trades_2024_01_15.py"
TRADE_V1_SCHEMA = ROOT / "schemas" / "trade-v1.json"

DATAGATEWAY_ORDERING_IDENTITY = "bybit-trade-v1-exchange-ts-trade-id-v1"
CANDLE_ORDERING_IDENTITY = "trades@1-canonical-total-order-v1"

GOLDEN_FIXTURE = ROOT / "fixtures" / "conformity" / "golden-bybit-btcusdt-2024-01-15.json"

CONTRACT_FREEZE_GATE = "Contract Freeze Gate"
CONFORMITY_IMPLEMENTATION_GATE = "Conformity Implementation Gate"

# Canonical-JSON SHA-256 of the accepted schemas/trade-v1.json content,
# computed with json.dumps(sort_keys=True, separators=(",", ":"),
# ensure_ascii=True) -- the same fingerprint convention
# quant_platform.access.models._fingerprint() already uses -- over the
# *parsed* schema, not its raw bytes. Pinned once; see
# BybitEligibilityProfileIsDistinctFromGenericSchema below.
PINNED_TRADE_V1_CANONICAL_SHA256 = "8ac391b0d03073ccf06a40676cb66a1a91934eb973efb79d3d05e2bb1bd943e2"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _normalize(text: str) -> str:
    """Collapse whitespace runs so a multi-word phrase check is not fragile
    against incidental Markdown line-wrapping."""
    return re.sub(r"\s+", " ", text)


def _golden_facts_from_contract() -> dict[str, object]:
    text = _normalize(_read(CONTRACT_PATH))
    match = re.search(
        r"declared interval: \[([^,]+), ([^)]+)\) rows = ([\d,]+) buy = ([\d,]+) sell = ([\d,]+) "
        r"first observed: ([^ ]+) last observed: ([^ ]+)",
        text,
    )
    if match is None:
        raise AssertionError("frozen Golden facts are missing from the conformity contract")
    start, end, rows, buy, sell, first, last = match.groups()
    return {
        "interval_start": start,
        "interval_end": end,
        "row_count": int(rows.replace(",", "")),
        "buy": int(buy.replace(",", "")),
        "sell": int(sell.replace(",", "")),
        "first_exchange_ts": first,
        "last_exchange_ts": last,
    }


class GovernanceDocumentsExistAndAreLinked(unittest.TestCase):
    def test_adr_0023_exists_with_accepted_contract_freeze_status(self):
        self.assertTrue(ADR_PATH.is_file(), ADR_PATH)
        text = _read(ADR_PATH)
        status_match = re.search(r"^\*\*Status:\*\*\s*([^\r\n]+)$", text, re.MULTILINE)
        self.assertIsNotNone(status_match, "ADR-0023 current status line not found")
        self.assertEqual(status_match.group(1).strip(), "ACCEPTED")
        self.assertNotIn("PROPOSED", status_match.group(1))
        self.assertRegex(
            text,
            re.compile(r"^\*\*Contract Freeze Gate:\*\*\s*PASSED$", re.MULTILINE),
        )

    def test_adr_0024_exists_with_accepted_status(self):
        self.assertTrue(ADR_0024_PATH.is_file(), ADR_0024_PATH)
        text = _read(ADR_0024_PATH)
        status_match = re.search(r"^\*\*Status:\*\*\s*([^\r\n]+)$", text, re.MULTILINE)
        self.assertIsNotNone(status_match, "ADR-0024 current status line not found")
        self.assertEqual(status_match.group(1).strip(), "ACCEPTED")
        self.assertIn("APPROVE", text)
        self.assertIn("BLOCKERS: NONE", text)

    def test_conformity_contract_exists(self):
        self.assertTrue(CONTRACT_PATH.is_file(), CONTRACT_PATH)

    def test_adr_0023_indexed_in_decisions_readme(self):
        text = _read(DECISIONS_INDEX)
        self.assertIn("0023", text)
        self.assertIn("Producer", text)

    def test_conformity_contract_linked_from_docs_index(self):
        text = _read(DOCS_INDEX)
        self.assertIn("PRODUCER_CONSUMER_CONFORMITY.md", text)

    def test_roadmap_references_the_gates(self):
        text = _read(ROADMAP_PATH)
        self.assertIn(CONTRACT_FREEZE_GATE, text)
        self.assertIn(CONFORMITY_IMPLEMENTATION_GATE, text)

    def test_capability_map_references_the_gates(self):
        text = _read(CAPABILITY_MAP_PATH)
        self.assertIn(CONTRACT_FREEZE_GATE, text)

    def test_open_decisions_references_the_gate(self):
        text = _read(OPEN_DECISIONS_PATH)
        self.assertIn("ADR-0023", text)


class TwoStageGateModelHasNoCircularity(unittest.TestCase):
    """The structural defect the second review round found: a single 'gate
    PASS' whose exit criteria required its own unlock. This class checks the
    fix is textually present and internally consistent -- it cannot check
    that the two gates behave correctly at runtime, because no runtime
    exists yet."""

    def test_both_named_gates_are_defined(self):
        adr_text = _read(ADR_PATH)
        contract_text = _read(CONTRACT_PATH)
        for text in (adr_text, contract_text):
            self.assertIn(CONTRACT_FREEZE_GATE, text)
            self.assertIn(CONFORMITY_IMPLEMENTATION_GATE, text)

    def test_contract_freeze_gate_exit_is_documentation_level(self):
        adr_text = _read(ADR_PATH)
        # The Contract Freeze Gate exit-criteria block must not itself demand
        # runtime existence -- that demand belongs only to the Conformity
        # Implementation Gate's own exit-criteria block.
        freeze_block_match = re.search(
            r"### 6\. Contract Freeze Gate exit criteria(.*?)### 7\. Conformity Implementation Gate exit criteria",
            adr_text,
            re.DOTALL,
        )
        self.assertIsNotNone(freeze_block_match, "Contract Freeze Gate exit-criteria block not found")
        freeze_block = freeze_block_match.group(1)
        self.assertNotIn("is implemented and pass", freeze_block)
        self.assertNotIn("exists and satisfies", freeze_block)

    def test_conformity_implementation_gate_exit_requires_runtime(self):
        adr_text = _read(ADR_PATH)
        impl_block_match = re.search(
            r"### 7\. Conformity Implementation Gate exit criteria(.*?)### 8\. After Contract Freeze Gate PASS",
            adr_text,
            re.DOTALL,
        )
        self.assertIsNotNone(impl_block_match, "Conformity Implementation Gate exit-criteria block not found")
        impl_block = impl_block_match.group(1)
        self.assertIn("exists and satisfies", impl_block)

    def test_only_implementation_gate_reopens_vertical_expansion(self):
        adr_text = _read(ADR_PATH)
        self.assertIn(
            "After Contract Freeze Gate PASS",
            adr_text,
        )
        after_freeze = re.search(r"### 8\. After Contract Freeze Gate PASS(.*?)### 9\.", adr_text, re.DOTALL)
        self.assertIsNotNone(after_freeze)
        self.assertIn("remain suspended", _normalize(after_freeze.group(1)))
        after_impl = re.search(r"### 9\. After Conformity Implementation Gate PASS(.*)", adr_text, re.DOTALL)
        self.assertIsNotNone(after_impl)
        self.assertIn("resume", after_impl.group(1))

    def test_no_document_claims_gate_requires_own_runtime(self):
        # Guards against the exact circular phrasing living on as a *current,
        # unqualified* claim. Both documents are allowed to quote the retired
        # phrasing -- the ADR's "Second review round" audit trail, and
        # Decision S1's own "no document may state ... and ... about the
        # same gate" explanation, which necessarily quotes the forbidden
        # pattern to name it. A quoted appearance (preceded by `"`) is
        # exactly that kind of meta-reference, not a live violation; only an
        # *unquoted* occurrence would mean the defect resurfaced.
        pattern = re.compile(r'(?<!")gate PASS requires (conformity )?runtime')
        adr_text = _read(ADR_PATH)
        self.assertIsNone(
            pattern.search(adr_text),
            "ADR-0023 reintroduces the circular phrasing as an unquoted claim",
        )
        contract_text = _read(CONTRACT_PATH)
        self.assertIsNone(
            pattern.search(contract_text),
            "PRODUCER_CONSUMER_CONFORMITY.md reintroduces the circular phrasing as an unquoted claim",
        )


class BybitEligibilityProfileIsDistinctFromGenericSchema(unittest.TestCase):
    """Resolves B9: trade-v1 stays generically nullable for trade_id; Bybit
    publication eligibility for this vertical does not."""

    def test_generic_trade_v1_schema_still_allows_null_trade_id(self):
        text = _read(TRADE_V1_SCHEMA)
        # trade_id must still appear as an optional/nullable field in the
        # frozen generic schema -- this pass must not have touched it.
        self.assertIn('"trade_id"', text)

    def test_trade_v1_schema_semantic_content_is_unchanged_by_this_pass(self):
        # A byte-level digest is fragile against line-ending conversion
        # (Windows checkout vs. Linux CI) and proves nothing on its own: a
        # digest of any length always "is 64 hex characters." This instead
        # canonicalizes the *parsed* JSON content (sorted keys, no
        # incidental whitespace, ASCII-escaped) before hashing, so the
        # result depends only on schemas/trade-v1.json's semantic content --
        # exactly what a frozen-contract guard must protect -- and is
        # platform-independent because JSON parsing and this fixed
        # serialization are both invariant to source-file line endings and
        # to insignificant whitespace/key-order differences on disk.
        #
        # Canonicalization matches this repository's own convention for
        # stable fingerprints (see _fingerprint() in
        # src/quant_platform/access/models.py): json.dumps with sort_keys=True,
        # separators=(",", ":"), ensure_ascii=True.
        #
        # PINNED_TRADE_V1_CANONICAL_SHA256 was computed once, from the
        # accepted schemas/trade-v1.json content at the time this contract
        # pass was authored, and is not re-derived from the file at test
        # time -- an accidental semantic edit to the frozen schema must fail
        # this test, not silently update the expectation.
        with TRADE_V1_SCHEMA.open(encoding="utf-8") as handle:
            parsed = json.load(handle)
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(digest, PINNED_TRADE_V1_CANONICAL_SHA256)

    def test_contract_freezes_the_distinction_in_those_words(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("generic record-schema validity", text)
        self.assertIn("source/profile publication eligibility", text)

    def test_contract_states_non_null_unique_ordering_key_requirement(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("trade_id IS NOT NULL", text)
        self.assertIn('trade_id != ""', text)

    def test_contract_forbids_a_fallback_ordering_key(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("No fallback ordering key is invented", text)

    def test_contract_states_null_trade_id_partition_cannot_become_valid(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("MUST NOT** become `state = valid`", text)


class OrderingIdentityCompatibilityIsFrozenAndConsistent(unittest.TestCase):
    """Resolves B8: DataGateway and CandleDefinition ordering identities."""

    def test_datagateway_ordering_policy_is_explicitly_required(self):
        field = DataRequest.__dataclass_fields__["ordering_policy"]
        self.assertIsNone(field.default)

    def test_candle_golden_fixture_matches_frozen_identity(self):
        golden_path = ROOT / "fixtures" / "candle-definition-v1" / "golden-5m.json"
        text = _read(golden_path)
        self.assertIn(f'"ordering_policy": "{CANDLE_ORDERING_IDENTITY}"', text)

    def test_conformity_contract_states_both_identities_and_the_mapping(self):
        text = _read(CONTRACT_PATH)
        self.assertIn(DATAGATEWAY_ORDERING_IDENTITY, text)
        self.assertIn(CANDLE_ORDERING_IDENTITY, text)
        self.assertIn("satisfied by", text)

    def test_ordering_mapping_now_requires_the_eligibility_profile_too(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("only for `trade-v1` records that pass the Bybit first-vertical", text)

    def test_candle_definition_contract_points_at_the_mapping(self):
        text = _read(ROOT / "docs" / "contracts" / "CANDLE_DEFINITION.md")
        self.assertIn("PRODUCER_CONSUMER_CONFORMITY.md", text)

    def test_data_gateway_contract_points_at_the_mapping(self):
        text = _read(ROOT / "docs" / "contracts" / "DATA_GATEWAY.md")
        self.assertIn("PRODUCER_CONSUMER_CONFORMITY.md", text)


class OrderingProofCorrectlyAttributesNonOverlapEnforcement(unittest.TestCase):
    """Resolves the ordering-proof precision defect: cross-partition
    non-overlap must be attributed to DataGateway's fail-closed
    CatalogConflict check, not to declared-coverage contiguity alone."""

    def test_contract_names_both_legs_of_the_proof(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("OR4a", text)
        self.assertIn("OR4b", text)
        self.assertIn("intra-partition contiguity", text)
        self.assertIn("inter-partition non-overlap", text)

    def test_contract_explicitly_rejects_the_overclaim(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("says nothing, by\nitself, about whether two different partitions", text)

    def test_gateway_still_enforces_the_catalog_conflict_check(self):
        text = _read(ROOT / "src" / "quant_platform" / "access" / "gateway.py")
        self.assertIn("unexplained temporal overlap", text)
        self.assertIn("CatalogConflict", text)


class PhysicalParquetContractMatchesTheReader(unittest.TestCase):
    """Resolves B1 (original): the frozen physical column contract must
    match what src/quant_platform/data/parquet.py already reads, so a future
    writer and the existing reader cannot silently diverge."""

    def test_required_columns_are_declared_in_the_contract(self):
        text = _read(CONTRACT_PATH)
        for column in _REQUIRED:
            with self.subTest(column=column):
                self.assertIn(f"`{column}`", text)

    def test_optional_columns_are_declared_in_the_contract(self):
        text = _read(CONTRACT_PATH)
        for column in _OPTIONAL:
            with self.subTest(column=column):
                self.assertIn(f"`{column}`", text)

    def test_contract_column_set_has_no_unknown_extra_columns(self):
        text = _read(CONTRACT_PATH)
        table_row_match = re.search(r"\| Columns \| Exactly (.+?) \|\n", text)
        self.assertIsNotNone(table_row_match, "physical column contract row not found")
        declared = set(re.findall(r"`([a-z_]+)`", table_row_match.group(1)))
        self.assertEqual(declared, set(_REQUIRED) | set(_OPTIONAL))


class RecordTimeBoundsIsDistinctFromCoverageInterval(unittest.TestCase):
    """Resolves B6: RecordTimeBounds must not reuse the coverage type."""

    def test_contract_defines_record_time_bounds_as_a_distinct_concept(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("RecordTimeBounds", text)
        self.assertIn("MUST NOT reuse `CoverageInterval`", text)

    def test_contract_freezes_single_record_bounds_as_valid(self):
        text = _read(CONTRACT_PATH)
        self.assertIn("first == last", text)


class CanonicalContentHashV1AlgorithmIsFullySpecified(unittest.TestCase):
    """Resolves B7 precision: the algorithm must be reproducible, not just
    conceptually named. Checks every element the review asked for is
    present: domain separation, record scope, order, framing, field
    encoding, hash function, empty-content behavior, and scope/version."""

    def setUp(self):
        self.text = _read(CONTRACT_PATH)

    def test_versioned_name(self):
        self.assertIn("CanonicalContentHashV1", self.text)
        self.assertIn("canonical-content-hash-v1", self.text)

    def test_domain_separation_tag_is_frozen(self):
        self.assertIn("quant-platform/canonical-content-hash-v1/trade-v1", self.text)
        self.assertIn("DOMAIN_TAG", self.text)

    def test_record_field_order_is_frozen(self):
        order = [
            "venue",
            "instrument",
            "exchange_ts",
            "receive_ts",
            "price",
            "size",
            "aggressor_side",
            "trade_id",
            "sequence",
        ]
        # The exact ordered block appears verbatim as a fenced list.
        block = "\n".join(order)
        self.assertIn(block, self.text)

    def test_record_order_is_the_canonical_total_order(self):
        self.assertIn("ordered**-content identity", self.text)
        self.assertIn("(exchange_ts parsed instant, trade_id opaque string)", self.text)

    def test_null_distinct_from_empty_string_framing(self):
        self.assertIn("0x00", self.text)
        self.assertIn("0x01", self.text)
        self.assertIn("null-tag byte", self.text)

    def test_length_delimited_framing_not_json(self):
        self.assertIn("LENGTH", self.text)
        self.assertIn("not** JSON", self.text)

    def test_timestamp_encoding_has_no_float_conversion_and_fixed_precision(self):
        self.assertIn("No float conversion is used", self.text)
        self.assertIn("nine fractional digits", self.text)

    def test_hash_function_is_named(self):
        self.assertIn("SHA-256(", self.text)

    def test_empty_content_digest_is_defined(self):
        self.assertIn("CanonicalContentHashV1(empty)", self.text)
        self.assertIn("0x0000000000000000", self.text)

    def test_scope_is_partition_content_not_dataset_wide(self):
        self.assertIn("one canonical partition's ordered\nlogical record content", self.text)
        self.assertIn("not** a dataset-wide or\nrequest-result-wide hash", self.text)

    def test_worked_example_present(self):
        self.assertIn("Worked example", self.text)


class PhysicalVsSemanticVsResultIdentityMatrixIsConsistent(unittest.TestCase):
    """Resolves B7 (original) decision: three distinct identities, stated
    without contradiction."""

    def setUp(self):
        self.text = _read(CONTRACT_PATH)

    def test_all_three_identities_are_named(self):
        self.assertIn("PhysicalArtifactHash", self.text)
        self.assertIn("CanonicalContentHashV1", self.text)
        self.assertIn("result_identity", self.text)

    def test_physical_and_canonical_hash_are_explicitly_distinct(self):
        self.assertIn("PhysicalArtifactHash", self.text)
        # The identity matrix table must show opposite sensitivity to
        # compression/layout for these two.
        self.assertIn("**No** — invariant to compression", self.text)
        self.assertIn("**Yes** — any byte difference changes it", self.text)

    def test_result_identity_stated_as_physical_evidence_sensitive(self):
        self.assertIn("result_identity` keeps identifying exact source artifact provenance", self.text)
        self.assertIn("remains, by design and unchanged, sensitive to exact\nphysical evidence", self.text)

    def test_no_contradiction_wording_is_present(self):
        self.assertIn("This is not a contradiction.", self.text)

    def test_semantic_identity_phrase_is_pinned_to_canonical_hash_not_result_identity(self):
        self.assertIn("means\n`CanonicalContentHashV1`, **never** the current ADR-0019 `result_identity`", self.text)

    def test_current_implementation_still_couples_content_hashes_into_result_identity(self):
        # Documents today's actual coupling this section reasons about; if a
        # future change removes content_hashes from the stable payload this
        # test must be revisited together with PRODUCER_CONSUMER_CONFORMITY.md S12.
        text = _read(ROOT / "src" / "quant_platform" / "access" / "gateway.py")
        self.assertIn('"content_hashes": list(content_hashes)', text)


class DurableCertificationEvidenceModelUsesExistingTable(unittest.TestCase):
    """Resolves the durable-evidence-location gap: quality_reports, no DDL
    change, with a frozen application-level metrics shape."""

    def setUp(self):
        self.text = _read(CONTRACT_PATH)
        self.ddl_text = _read(ROOT / "db" / "init" / "001_catalog.sql")

    def test_contract_names_quality_reports_as_the_persistence_target(self):
        self.assertIn("quality_reports", self.text)
        self.assertIn("no schema/DDL change", self.text)

    def test_quality_reports_table_actually_has_the_needed_columns(self):
        # Ground the contract's claim against the real DDL rather than only
        # against itself.
        create_stmt = re.search(r"CREATE TABLE quality_reports \((.*?)\n\);", self.ddl_text, re.DOTALL)
        self.assertIsNotNone(create_stmt, "quality_reports DDL not found")
        columns = create_stmt.group(1)
        for column in ("partition_id", "dataset_id", "check_suite", "status", "metrics", "violations", "code_ref"):
            with self.subTest(column=column):
                self.assertIn(column, columns)
        self.assertIn("jsonb", columns)

    def test_this_pass_did_not_modify_the_catalog_ddl(self):
        # No CREATE/ALTER statement mentioning a certification-specific
        # table or column was introduced.
        self.assertNotIn("certification", self.ddl_text.lower())
        self.assertNotIn("canonical_content_hash", self.ddl_text.lower())

    def test_status_to_lifecycle_mapping_is_frozen(self):
        self.assertIn("quality_reports.status = 'pass'", self.text)
        self.assertIn("quality_reports.status = 'fail'", self.text)

    def test_evidence_must_precede_eligibility(self):
        self.assertIn("evidence precedes eligibility, always", self.text)

    def test_redundant_natural_identity_encoding_is_required_and_justified(self):
        self.assertIn("not stable across a catalog rebuild", self.text)
        self.assertIn("natural_partition_identity", self.text)


class CertifierIdentityIsMandatoryForValid(unittest.TestCase):
    """Resolves the final review's sole remaining blocker: a PASS report
    with a null/empty code_ref must not be able to authorize state=valid.
    Uses the existing quality_reports.code_ref column -- no new field."""

    def setUp(self):
        self.text = _read(CONTRACT_PATH)
        self.ddl_text = _read(ROOT / "db" / "init" / "001_catalog.sql")

    def test_pass_report_requires_non_null_non_empty_code_ref(self):
        self.assertIn("Invariant CE6", self.text)
        self.assertIn("quality_reports.code_ref IS NOT NULL", self.text)
        self.assertIn("trim(quality_reports.code_ref) != ''", self.text)

    def test_null_or_empty_code_ref_cannot_authorize_valid(self):
        self.assertIn(
            "A report with `status = 'pass'` and `code_ref = null`, or `status = 'pass'`",
            self.text,
        )
        self.assertIn("**MUST NOT** authorize `state = 'valid'`", self.text)

    def test_missing_code_ref_is_refusal_not_degraded(self):
        self.assertIn(
            "never used to encode a coverage, eligibility-profile,",
            self.text,
        )
        self.assertIn("physical, or certifier-identity defect", self.text)
        self.assertIn("certifier-identity failure (CE6/CE7", self.text)

    def test_generic_ddl_column_stays_nullable_the_profile_is_stricter(self):
        # Ground both halves of the distinction against reality: the DDL
        # really does leave code_ref nullable, and this pass did not tighten
        # it -- the stricter rule lives only in the certification profile.
        create_stmt = re.search(r"CREATE TABLE quality_reports \((.*?)\n\);", self.ddl_text, re.DOTALL)
        self.assertIsNotNone(create_stmt)
        code_ref_line = next(
            line for line in create_stmt.group(1).splitlines() if line.strip().startswith("code_ref")
        )
        self.assertNotIn("NOT NULL", code_ref_line)
        self.assertIn("generic quality_reports structural validity", self.text)
        self.assertIn("Bybit conformity PASS eligibility", self.text)

    def test_certifier_and_producer_code_ref_are_distinct_roles(self):
        self.assertIn("Invariant CE7", self.text)
        self.assertIn("identifies *the\ncertifier implementation*", self.text)
        self.assertIn("identifies *the\nproducer*", self.text)
        # Ground the producer-role half against the real DDL comment/constraint.
        self.assertIn("code_ref        text        NOT NULL", self.ddl_text)

    def test_no_duplicate_certifier_field_was_invented_in_metrics(self):
        # CE2's frozen metrics shape must still not carry a second
        # certifier-identity field; CE6/CE7 rely on the existing column only.
        metrics_block_match = re.search(r"MUST contain at least:\n\n```json\n(.*?)\n```", self.text, re.DOTALL)
        self.assertIsNotNone(metrics_block_match, "CE2 metrics example block not found")
        self.assertNotIn("code_ref", metrics_block_match.group(1))
        self.assertNotIn("certifier", metrics_block_match.group(1))


class CertificationExampleRevisionIsPositive(unittest.TestCase):
    """Second important correction: the CE2 example used revision=0, which
    contradicts NaturalPartitionIdentity's own positive-revision rule."""

    def test_ce2_example_uses_a_positive_revision(self):
        text = _read(CONTRACT_PATH)
        self.assertNotIn('"revision": 0', text)
        self.assertIn('"revision": 1', text)

    def test_positive_revision_rule_is_grounded_in_the_real_model(self):
        models_text = _read(ROOT / "src" / "quant_platform" / "data" / "models.py")
        self.assertIn("if self.revision < 1:", models_text)

    def test_adr_audit_trail_quotes_the_defect_without_reintroducing_it(self):
        # The ADR's "Third review round" section legitimately quotes the
        # retired "revision": 0 example once, in past tense; the contract
        # itself (which has no audit-trail section) must have zero
        # occurrences.
        contract_text = _read(CONTRACT_PATH)
        self.assertEqual(contract_text.count('"revision": 0'), 0)


class CertificationSequencingAvoidsCircularity(unittest.TestCase):
    def setUp(self):
        self.text = _read(CONTRACT_PATH)

    def test_five_phases_are_named_in_order(self):
        for phase in ("Phase 1 — SEAL", "Phase 2 — CERTIFY", "Phase 3 — RECORD EVIDENCE",
                       "Phase 4 — PUBLISH ELIGIBILITY", "Phase 5 — VERIFY"):
            with self.subTest(phase=phase):
                self.assertIn(phase, self.text)

    def test_certification_never_depends_on_its_own_output(self):
        self.assertIn("each phase depends only on the *output* of\nan earlier phase, never on its own eventual output", self.text)


class CatalogRebuildEqualityIgnoresGeneratedIdentifiers(unittest.TestCase):
    def setUp(self):
        self.text = _read(CONTRACT_PATH)

    def test_uuid_fields_are_explicitly_not_semantic(self):
        self.assertIn("not** reproducible\nidentities", self.text)
        self.assertIn("never** means byte-identical or UUID-identical", self.text)

    def test_semantic_field_table_present(self):
        self.assertIn("Rebuild-authoritative (must match)", self.text)
        self.assertIn("Runtime/generated (may differ)", self.text)

    def test_request_and_result_identity_survive_rebuild(self):
        self.assertIn("request_identity` and `result_identity`", self.text)


class GoldenVerticalNumbersAreConsistentAcrossDocuments(unittest.TestCase):
    """The executable fixture must agree with the frozen contract facts."""

    def test_buy_plus_sell_equals_rows(self):
        golden = load_golden_expectation(GOLDEN_FIXTURE)
        self.assertEqual(golden.buy + golden.sell, golden.row_count)

    def test_fixture_matches_frozen_contract_facts(self):
        golden = load_golden_expectation(GOLDEN_FIXTURE)
        facts = _golden_facts_from_contract()
        for field in (
            "interval_start",
            "interval_end",
            "row_count",
            "buy",
            "sell",
            "first_exchange_ts",
            "last_exchange_ts",
        ):
            with self.subTest(field=field):
                self.assertEqual(getattr(golden, field), facts[field])

    def test_integration_reuses_shared_fixture_support(self):
        self.assertTrue(GOLDEN_INTEGRATION_TEST.is_file(), GOLDEN_INTEGRATION_TEST)
        text = _read(GOLDEN_INTEGRATION_TEST)
        self.assertIn("from golden_conformity_support import load_golden_expectation", text)
        self.assertIn("GOLDEN = load_golden_expectation()", text)


class FrozenContractsAreNotMutatedInPlace(unittest.TestCase):
    """Resolves the frozen-contract-guard gap: ADR-0021 was missing from the
    first candidate's guard even though CanonicalContentHashV1 and Candle
    ordering compatibility both depend on it."""

    def test_accepted_adrs_still_say_accepted(self):
        for name in (
            "ADR-0019-datagateway-boundary.md",
            "ADR-0020-consumer-api-boundary.md",
            "ADR-0021-candle-definition-v1.md",
            "ADR-0022-declared-coverage-contract.md",
        ):
            with self.subTest(adr=name):
                text = _read(ROOT / "docs" / "decisions" / name)
                self.assertTrue(
                    re.search(r"\*\*Status:\*\*\s*ACCEPTED", text, re.IGNORECASE),
                    f"{name} status line changed unexpectedly",
                )

    def test_contract_prose_names_all_four_accepted_adrs(self):
        text = _read(CONTRACT_PATH)
        self.assertIn(
            "This contract does not rewrite ADR-0019, ADR-0020, ADR-0021 or ADR-0022",
            text,
        )

    def test_frozen_schemas_are_not_touched_by_this_pass(self):
        # sha256 stability is already covered by their own contract test
        # suites; this only guards that this pass did not add a stray schema
        # version file outside the explicitly authorized manifest-v2 evolution.
        schemas_dir = ROOT / "schemas"
        expected = {
            "candle-v1.json",
            "coverage-manifest-v1.json",
            "dataset-manifest-v1.json",
            "dataset-manifest-v2.json",
            "l2-book-event-v1.json",
            "partition-manifest-v1.json",
            "trade-v1.json",
        }
        actual = {path.name for path in schemas_dir.glob("*.json")}
        self.assertEqual(actual, expected)


class SectionCrossReferencesAreInternallyConsistent(unittest.TestCase):
    """Resolves the incorrect '(S10 below)' reference defect: every '(Decision
    SN ...)' back-reference inside ADR-0023 must point at a Decision item
    that actually exists as '### N.' in the same file."""

    def test_no_dangling_section_10_exit_criteria_reference(self):
        text = _read(ADR_PATH)
        self.assertNotIn("(§10 below)", text)

    def test_decision_item_headers_are_sequential(self):
        text = _read(ADR_PATH)
        headers = re.findall(r"^### (\d+)\. ", text, re.MULTILINE)
        numbers = [int(item) for item in headers]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_contract_section_headers_are_sequential(self):
        text = _read(CONTRACT_PATH)
        headers = re.findall(r"^## (\d+)\. ", text, re.MULTILINE)
        numbers = [int(item) for item in headers]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)

#!/usr/bin/env python3
"""C02 -- ASS-02 semantic selector resolution for trades@1.

Proves only that a semantic consumer query resolves deterministically into the
canonical ``DataRequest`` without the caller supplying dataset, policy, catalog
or storage identity, and that unsupported representations, options and venues
are refused before reaching any lower layer.

Read execution, result envelopes and stable API error translation are C03 and
are deliberately not exercised here.  The reference request is checked against
the already-accepted canonical access request rather than by rerunning the
Golden or PostgreSQL evidence.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access.models import DataRequest, LifecyclePolicy  # noqa: E402
from quant_platform.application import (  # noqa: E402
    ConsumerMarketDataQuery,
    RepresentationRef,
    UnsupportedOption,
    UnsupportedRepresentation,
    UnsupportedVenue,
    resolve_market_data_request,
)
from quant_platform.data.models import DatasetIdentity, InvalidRequest  # noqa: E402
from quant_platform.source_adapters.bybit import BYBIT_TRADE_V1_ORDERING_POLICY  # noqa: E402


TRADES_V1 = RepresentationRef("trades", 1)
DAY_START = "2024-01-15T00:00:00Z"
DAY_END = "2024-01-16T00:00:00Z"

# The canonical access request already proven by the accepted Bybit historical
# vertical.  C02 must reproduce exactly this from semantic input alone.
GOLDEN_REQUEST = DataRequest(
    dataset_selector=DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"),
    start=DAY_START,
    end=DAY_END,
    schema_requirement="trade-v1",
    lifecycle_policy=LifecyclePolicy.VALID_ONLY,
    coverage_policy="strict",
    ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
)


def query(**overrides) -> ConsumerMarketDataQuery:
    base = {
        "venue": "bybit",
        "instrument": "BTCUSDT",
        "start": DAY_START,
        "end": DAY_END,
        "representation": TRADES_V1,
    }
    base.update(overrides)
    return ConsumerMarketDataQuery(**base)


class SemanticSelectorResolutionTests(unittest.TestCase):
    def test_reference_query_resolves_to_the_accepted_canonical_request(self):
        resolved = resolve_market_data_request(query())
        self.assertEqual(GOLDEN_REQUEST.stable_dict(), resolved.stable_dict())
        self.assertEqual(GOLDEN_REQUEST.request_identity, resolved.request_identity)

    def test_platform_owned_values_are_supplied_not_asked_for(self):
        resolved = resolve_market_data_request(query())
        self.assertEqual("canonical", resolved.dataset_selector.layer)
        self.assertEqual("trades", resolved.dataset_selector.dataset_kind)
        self.assertEqual("trade-v1", resolved.dataset_selector.record_schema_id)
        self.assertEqual("trade-v1", resolved.schema_requirement)
        self.assertEqual(LifecyclePolicy.VALID_ONLY, resolved.lifecycle_policy)
        self.assertEqual("strict", resolved.coverage_policy)
        self.assertEqual(BYBIT_TRADE_V1_ORDERING_POLICY, resolved.ordering_policy)

    def test_caller_surface_exposes_no_dataset_policy_or_storage_selector(self):
        self.assertEqual(
            {"venue", "instrument", "start", "end", "representation", "options"},
            {f.name for f in dataclasses.fields(ConsumerMarketDataQuery)},
        )
        self.assertEqual(
            {"kind", "version", "definition"},
            {f.name for f in dataclasses.fields(RepresentationRef)},
        )
        forbidden = (
            "layer", "dataset", "schema", "policy", "catalog",
            "partition", "storage", "path", "root", "ordering", "lifecycle", "coverage",
        )
        names = {f.name for f in dataclasses.fields(ConsumerMarketDataQuery)}
        names |= {f.name for f in dataclasses.fields(RepresentationRef)}
        for name in names:
            for token in forbidden:
                self.assertNotIn(token, name.lower(), f"{name} leaks {token}")

    def test_equivalent_venue_spellings_resolve_identically(self):
        canonical = resolve_market_data_request(query()).request_identity
        for spelling in ("Bybit", "BYBIT", "  bybit  ", "\tByBit\n"):
            with self.subTest(venue=spelling):
                resolved = resolve_market_data_request(query(venue=spelling))
                self.assertEqual(canonical, resolved.request_identity)

    def test_equivalent_interval_spellings_resolve_identically(self):
        canonical = resolve_market_data_request(query()).request_identity
        resolved = resolve_market_data_request(
            query(
                start=datetime(2024, 1, 15, tzinfo=timezone.utc),
                end=datetime(2024, 1, 16, tzinfo=timezone.utc),
            )
        )
        self.assertEqual(canonical, resolved.request_identity)

    def test_accepted_utc_timestamp_form_is_not_reinterpreted(self):
        # Instant.parse accepts RFC 3339 ending in Z and refuses a numeric
        # offset spelling.  That is frozen lower-layer temporal semantics; C02
        # delegates to it and must not widen or normalise it.
        with self.assertRaises(InvalidRequest):
            resolve_market_data_request(
                query(start="2024-01-15T00:00:00+00:00", end="2024-01-16T00:00:00+00:00")
            )

    def test_unsupported_representation_is_refused(self):
        unsupported = (("candle", 1), ("footprint", 1), ("trades", 2), ("trades", 0), ("book", 1))
        for kind, version in unsupported:
            with self.subTest(kind=kind, version=version):
                with self.assertRaises(UnsupportedRepresentation):
                    resolve_market_data_request(
                        query(representation=RepresentationRef(kind, version))
                    )

    def test_malformed_representation_version_is_refused(self):
        # True == 1 and 1.0 == 1, so an equality-only check would admit these
        # as trades@1.  The accepted version is an exact integer.
        for version in (True, 1.0):
            with self.subTest(version=repr(version)):
                with self.assertRaises(UnsupportedRepresentation):
                    resolve_market_data_request(
                        query(representation=RepresentationRef("trades", version))
                    )
        accepted = resolve_market_data_request(
            query(representation=RepresentationRef("trades", 1))
        )
        self.assertEqual(GOLDEN_REQUEST.request_identity, accepted.request_identity)

    def test_unsupported_definition_or_options_are_refused_not_ignored(self):
        with self.assertRaises(UnsupportedOption):
            resolve_market_data_request(
                query(representation=RepresentationRef("trades", 1, {"tick_size": "0.1"}))
            )
        with self.assertRaises(UnsupportedOption):
            resolve_market_data_request(query(options={"projection": ["price"]}))
        # An empty definition/options mapping is absence, not an unsupported option.
        self.assertEqual(
            GOLDEN_REQUEST.request_identity,
            resolve_market_data_request(
                query(representation=RepresentationRef("trades", 1, {}), options={})
            ).request_identity,
        )

    def test_unknown_venue_is_refused_without_fabricating_a_policy(self):
        for venue in ("binance", "okx", "not-a-venue"):
            with self.subTest(venue=venue):
                with self.assertRaises(UnsupportedVenue):
                    resolve_market_data_request(query(venue=venue))

    def test_existing_temporal_semantics_are_preserved(self):
        empty = resolve_market_data_request(query(end=DAY_START))
        self.assertTrue(empty.empty)
        self.assertEqual(empty.start, empty.end)
        with self.assertRaises(InvalidRequest):
            resolve_market_data_request(query(start=DAY_END, end=DAY_START))

    def test_primitive_selector_validation_is_delegated_not_reimplemented(self):
        blanks = (("", "BTCUSDT"), ("   ", "BTCUSDT"), ("bybit", ""), ("bybit", "  "))
        for venue, instrument in blanks:
            with self.subTest(venue=venue, instrument=instrument):
                with self.assertRaises(InvalidRequest):
                    resolve_market_data_request(query(venue=venue, instrument=instrument))

    def test_resolution_does_not_construct_access_or_configuration(self):
        # The module now also hosts C03, which legitimately *names* gateway
        # types (DataGatewayError) and calls scan() on an injected capability.
        # The invariant that must hold is that nothing here CONSTRUCTS a
        # gateway, catalog, connection or configuration -- that is C05.
        import quant_platform.application.market_data as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "DataGateway(", "Catalog(", "psycopg", "os.environ", "getenv",
            "sys.argv", "argparse", "connect(",
        ):
            self.assertNotIn(forbidden, source, f"application must not construct {forbidden}")

    def test_resolution_alone_needs_no_gateway(self):
        # Resolution is pure: it produces a request without any access capability.
        self.assertIsNotNone(resolve_market_data_request(query()).dataset_selector)


if __name__ == "__main__":
    unittest.main()

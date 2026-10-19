"""Regression guard for the multi-instrument Bybit trades design contract (ADR-0074, design gate #377).

The ADR is PROPOSED. This test pins, on today's baseline, the facts it rests on (the BTCUSDT-only places in the code), pins the
BTCUSDT identities that must not change, and states the target properties as ``expectedFailure`` tests: they fail on the
current baseline and turn into an "unexpected success" (a failing run) the day an implementation slice makes them true, so the
marker is then removed on purpose.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.application.market_data import (  # noqa: E402
    ConsumerMarketDataQuery,
    RepresentationRef,
    resolve_market_data_request,
)
from quant_platform.data.models import DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.models import TradeRecord  # noqa: E402
from quant_platform.source_adapters import bybit  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    bybit_trade_v1_applies_to,
    bybit_trade_v1_ordering_key,
)

ADR = ROOT / "docs/decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md"
ALLOW_LIST = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")


def identity(instrument: str) -> DatasetIdentity:
    return DatasetIdentity("canonical", "trades", "bybit", instrument, "trade-v1")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class NoCatalog:
    def resolve_dataset(self, _identity):
        raise AssertionError("the catalog was reached")


def scan(instrument: str):
    gateway = DataGateway(NoCatalog(), batch_reader=None, ordering_providers=(BYBIT_ORDERING_PROVIDER,))
    query = ConsumerMarketDataQuery(
        "bybit", instrument, "2024-01-01T00:00:00Z", "2024-01-01T02:00:00Z", RepresentationRef("trades", 1),
    )
    return gateway.scan(resolve_market_data_request(query))


class MultiInstrumentTradesContractV1Tests(unittest.TestCase):
    # -- the ADR says what the guard relies on -------------------------------------------------------------

    def test_the_adr_states_the_six_decisions_and_what_it_amends(self) -> None:
        text = ADR.read_text(encoding="utf-8")
        for required in (
            "**Status:** PROPOSED",
            "BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 = (\"BTCUSDT\", \"ETHUSDT\", \"SOLUSDT\", \"XRPUSDT\", \"DOGEUSDT\")",
            "### 1. A closed allow-list of instruments",
            "### 2. Identity",
            "### 3. Process model: one process per instrument",
            "### 4. Gaps and history",
            "### 5. Existing Parquet of the maintainer",
            "### 6. Capacity gate",
            "### 7. Fees and execution",
            "quant-platform-live-ingest@<INSTRUMENT>.service",
            "ADR-0044 stands",
            "**365 days**",
            "one hour of real provider traffic for\n`ETHUSDT`",
            "any other symbol (for example `ADAUSDT`) are not admitted.",
            "## Open items to answer before `ACCEPTED`",
        ):
            self.assertIn(required, text)

    # -- the baseline facts the ADR rests on (true today) --------------------------------------------------

    def test_today_the_ordering_applicability_is_btcusdt_only(self) -> None:
        self.assertTrue(bybit_trade_v1_applies_to(identity("BTCUSDT")))
        for instrument in ("ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"):
            self.assertFalse(bybit_trade_v1_applies_to(identity(instrument)), instrument)
        self.assertIn('identity.instrument == "BTCUSDT"', read("src/quant_platform/source_adapters/bybit.py"))

    def test_today_the_gateway_refuses_a_non_btc_dataset_before_the_catalog(self) -> None:
        with self.assertRaises(AssertionError) as reached:
            scan("BTCUSDT")
        self.assertEqual("the catalog was reached", str(reached.exception))
        for instrument in ("ETHUSDT", "SOLUSDT"):
            with self.assertRaises(Exception) as refused:
                scan(instrument)
            self.assertEqual("UnsupportedDatasetKind", type(refused.exception).__name__, instrument)

    def test_today_the_other_places_are_bound_to_btcusdt(self) -> None:
        live = read("src/quant_platform/source_adapters/bybit_live.py")
        self.assertIn('SUPPORTED_SYMBOL = "BTCUSDT"', live)
        self.assertIn('SUPPORTED_TOPIC = "publicTrade.BTCUSDT"', live)
        self.assertIn('subject_identity=f"bybit:BTCUSDT:{config.storage_root_id}"', read("src/quant_platform/application/live_ingest_server.py"))
        self.assertIn('SUPPORTED_INSTRUMENT = "BTCUSDT"', read("src/quant_platform/application/bybit_import.py"))
        self.assertIn("Canonical Bybit BTCUSDT linear perpetual VIP0 rates", read("src/quant_platform/execution/__init__.py"))
        self.assertIn("exactly one venue, instrument and", read("docs/decisions/ADR-0043-live-ingest-server-composition-v1.md"))

    # -- BTCUSDT must not change (true today and after) -----------------------------------------------------

    def test_the_btcusdt_identities_and_ordering_key_are_pinned(self) -> None:
        self.assertEqual(
            {"layer": "canonical", "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
             "record_schema_id": "trade-v1"},
            identity("BTCUSDT").stable_dict(),
        )
        stamp = Instant.parse("2024-01-01T00:00:00.123456Z")
        record = TradeRecord(
            venue="bybit", instrument="BTCUSDT", exchange_ts=stamp, price="42000.5", size="0.01",
            aggressor_side="buy", trade_id="t-1",
        )
        self.assertEqual((stamp, "t-1"), bybit_trade_v1_ordering_key(record))

    # -- the target properties: they fail on the baseline (expectedFailure) ----------------------------------

    @unittest.expectedFailure
    def test_target_the_allow_list_is_one_closed_constant(self) -> None:
        self.assertEqual(ALLOW_LIST, tuple(bybit.BYBIT_LINEAR_TRADE_INSTRUMENTS_V1))

    @unittest.expectedFailure
    def test_target_the_applicability_follows_the_allow_list_and_nothing_else(self) -> None:
        for instrument in ALLOW_LIST:
            self.assertTrue(bybit_trade_v1_applies_to(identity(instrument)), instrument)
        for instrument in ("ADAUSDT", "BTCUSD", "BTCUSDC", ""):
            self.assertFalse(bybit_trade_v1_applies_to(identity(instrument)), instrument)

    @unittest.expectedFailure
    def test_target_a_non_btc_dataset_reaches_the_catalog(self) -> None:
        with self.assertRaises(AssertionError) as reached:
            scan("ETHUSDT")
        self.assertEqual("the catalog was reached", str(reached.exception))

    @unittest.expectedFailure
    def test_target_the_live_subject_and_checkpoint_are_per_instrument(self) -> None:
        source = read("src/quant_platform/application/live_ingest_server.py")
        self.assertNotIn('subject_identity=f"bybit:BTCUSDT:', source)


if __name__ == "__main__":
    unittest.main()

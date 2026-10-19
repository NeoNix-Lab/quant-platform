"""Read-only check of the catalog for the Bybit linear allow-list (issue #485, ADR-0074 section 5).

For each instrument of ``BYBIT_LINEAR_TRADE_INSTRUMENTS_V1`` it reports whether the canonical ``trades`` / ``trade-v1`` dataset
is registered and how many of its partitions are in each state. It issues one ``SELECT`` on the connection the caller supplies,
changes nothing and knows no credential: what is missing is registered only through the producer path (materialize, certify,
publish), never from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..source_adapters.bybit import BYBIT_LINEAR_TRADE_INSTRUMENTS_V1

_QUERY = (
    "SELECT d.instrument, p.state, count(p.partition_id) "
    "FROM catalog.datasets d LEFT JOIN catalog.partitions p ON p.dataset_id = d.dataset_id "
    "WHERE d.layer = 'canonical' AND d.kind = 'trades' AND d.venue = 'bybit' AND d.schema_id = 'trade-v1' "
    "AND d.instrument = ANY(%s) "
    "GROUP BY d.instrument, p.state"
)


@dataclass(frozen=True, slots=True)
class InstrumentCatalogStatus:
    """What the catalog holds for one instrument of the allow-list."""

    instrument: str
    registered: bool
    partitions_by_state: Mapping[str, int]

    @property
    def valid_partitions(self) -> int:
        return self.partitions_by_state.get("valid", 0)

    @property
    def readable(self) -> bool:
        """Registered with at least one ``valid`` partition: the least a read can use."""
        return self.registered and self.valid_partitions > 0


def check_trades_catalog(connection: Any) -> tuple[InstrumentCatalogStatus, ...]:
    """One status per allow-list instrument, in the allow-list's order; an unregistered one has no partitions."""

    with connection.cursor() as cursor:
        cursor.execute(_QUERY, (list(BYBIT_LINEAR_TRADE_INSTRUMENTS_V1),))
        rows = cursor.fetchall()
    registered: dict[str, dict[str, int]] = {}
    for instrument, state, count in rows:
        states = registered.setdefault(instrument, {})
        if state is not None:  # a dataset with no partition has a single row with a NULL state
            states[state] = int(count)
    return tuple(
        InstrumentCatalogStatus(
            instrument=instrument,
            registered=instrument in registered,
            partitions_by_state=dict(sorted(registered.get(instrument, {}).items())),
        )
        for instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1
    )


def report(statuses: tuple[InstrumentCatalogStatus, ...]) -> dict[str, Any]:
    """A JSON-ready report: per instrument and the lists a registration slice acts on."""

    return {
        "instruments": [
            {
                "instrument": item.instrument,
                "registered": item.registered,
                "readable": item.readable,
                "partitions_by_state": dict(item.partitions_by_state),
            }
            for item in statuses
        ],
        "missing": [item.instrument for item in statuses if not item.registered],
        "registered_without_valid_partitions": [
            item.instrument for item in statuses if item.registered and not item.readable
        ],
    }


__all__ = ["InstrumentCatalogStatus", "check_trades_catalog", "report"]

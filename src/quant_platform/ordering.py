"""Generic ordering compatibility primitives.

Concrete ordering identities and their key functions are supplied by source
adapters.  This module only models the abstract compatibility relationship.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


TRADES_CANONICAL_TOTAL_ORDER_V1 = "trades@1-canonical-total-order-v1"
OrderingKey = Callable[[Any], tuple[Any, ...]]
ApplicabilityPredicate = Callable[[Any], bool]


class DuplicateOrderingProviderError(ValueError):
    """Raised when provider resolution is ambiguous for one policy identity."""


@dataclass(frozen=True, slots=True)
class OrderingProvider:
    """A source-supplied concrete ordering policy and its compatibility claims."""

    identity: str
    satisfies_requirements: frozenset[str]
    key: OrderingKey
    applies_to: ApplicabilityPredicate

    def satisfies(self, abstract_requirement: str) -> bool:
        return abstract_requirement in self.satisfies_requirements

    def applies(self, dataset_identity: Any) -> bool:
        return self.applies_to(dataset_identity)


def ordering_policy_satisfies(
    concrete_policy: str,
    abstract_requirement: str,
    providers: Iterable[OrderingProvider],
) -> bool:
    """Ask a supplied provider set whether a concrete policy satisfies a requirement."""

    return any(
        provider.identity == concrete_policy and provider.satisfies(abstract_requirement)
        for provider in providers
    )


def provider_for(
    concrete_policy: str,
    providers: Iterable[OrderingProvider],
) -> OrderingProvider | None:
    """Return the unique supplied provider for a concrete policy, if present."""

    matches = [provider for provider in providers if provider.identity == concrete_policy]
    if len(matches) > 1:
        raise DuplicateOrderingProviderError(
            f"multiple ordering providers supplied for identity {concrete_policy!r}"
        )
    return matches[0] if matches else None


__all__ = [
    "OrderingKey",
    "ApplicabilityPredicate",
    "DuplicateOrderingProviderError",
    "OrderingProvider",
    "TRADES_CANONICAL_TOTAL_ORDER_V1",
    "ordering_policy_satisfies",
    "provider_for",
]

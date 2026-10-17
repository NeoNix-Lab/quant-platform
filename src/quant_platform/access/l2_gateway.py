"""DataGateway read path for canonical ``l2-book-event-v1`` (ADR-0070 §6, A14e).

A sibling of :class:`DataGateway`: the scan machinery (``DataScan``, coverage
intersection and gaps, partition-boundary and ordering validation, provenance)
is record-type-agnostic, so the only trade-specific step, ``_validate_support``,
is overridden and the default batch reader is the L2 one.  ``DataGateway`` and
its ``trade-v1`` behaviour are not modified.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .catalog import Catalog
from .gateway import BatchReader, DataGateway
from .models import DataRequest, LiveStreamCursorV1, LiveStreamRequest
from ..data.l2_parquet import L2_BOOK_EVENT_SCHEMA_ID, scan_l2_book_event_v1
from ..data.models import UnsupportedDatasetKind, UnsupportedSchema
from ..data.parquet import resolve_partition_path
from ..ordering import (
    L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1,
    OrderingProvider,
    provider_for,
)


class L2DataGateway(DataGateway):
    """Read canonical L2 book events through the PostgreSQL catalog."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        batch_reader: BatchReader = scan_l2_book_event_v1,
        path_resolver: Callable[[str, str, str], Any] = resolve_partition_path,
        ordering_providers: tuple[OrderingProvider, ...] = (),
    ):
        super().__init__(
            catalog,
            batch_reader=batch_reader,
            path_resolver=path_resolver,
            ordering_providers=ordering_providers,
        )

    def live_stream(
        self,
        request: LiveStreamRequest,
        *,
        cursor: LiveStreamCursorV1 | None = None,
        batch_size: int = 0,
    ) -> Any:
        """Live L2 is batch ``v2`` (A14f); the historical gateway never serves it."""

        raise UnsupportedDatasetKind(
            "L2DataGateway serves historical reads only; live L2 is not available",
            context={"dataset_identity": request.dataset_selector.stable_dict()},
        )

    def _validate_support(self, request: DataRequest) -> OrderingProvider:
        identity = request.dataset_selector
        if identity.dataset_kind != "l2" or identity.layer != "canonical":
            raise UnsupportedDatasetKind(
                "L2DataGateway supports canonical l2 only",
                context={"dataset_identity": identity.stable_dict()},
            )
        if (
            identity.record_schema_id != L2_BOOK_EVENT_SCHEMA_ID
            or request.schema_requirement != L2_BOOK_EVENT_SCHEMA_ID
        ):
            raise UnsupportedSchema(
                "L2DataGateway supports l2-book-event-v1 only",
                context={"requested_schema": request.schema_requirement},
            )
        ordering_provider = provider_for(request.ordering_policy, self._ordering_providers)
        if ordering_provider is None or not ordering_provider.satisfies(
            L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1
        ):
            raise UnsupportedSchema(
                "requested ordering policy has no compatible provider",
                context={"ordering_policy": request.ordering_policy},
            )
        if not ordering_provider.applies(identity):
            raise UnsupportedDatasetKind(
                "ordering provider is not applicable to the requested dataset",
                context={
                    "ordering_policy": request.ordering_policy,
                    "dataset_identity": identity.stable_dict(),
                },
            )
        return ordering_provider


__all__ = ["L2DataGateway"]

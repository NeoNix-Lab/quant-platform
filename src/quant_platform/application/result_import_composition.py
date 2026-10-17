"""K13 composition: the output-role registry and the import-service factory (issue #392).

ADR-0072 section 2. ``GovernedResultImportService`` (K13) and the
``j02-result-import-v1`` family exist as libraries; this module composes the
service, as the API host will in a later issue (S03), from three things the
ADR fixes:

- **The closed registry of six output roles** (:data:`K13_OUTPUT_ROLES_V1`): the
  four supervised roles and two replay roles. A role outside it, or a role with
  another kind or schema, is refused by the service. The two replay roles are
  :class:`ReplayOutputContractV1`, so the family also requires
  ``configuration.replay_feature_provider`` for them (ADR-0069 section 7); the
  two replay kinds reuse the identity domains ``replay-result-v1`` and
  ``replay-summary-v1`` that the replay package already declares.
- **The content identity of an output** is the K13 checksum of the uploaded
  bytes (``sha256:<hex>``): the service's verifier is the staging store's
  :meth:`StagedOutputStore.verify_output`, which hashes the staged bytes again,
  and ``ResultOutputEvidenceV1`` already requires the registration's content
  identity to equal that checksum.
- **One connection per request** of the API login: the admission store, the
  Experiment repository and the K13 evidence ledger share it, so the Experiment
  registration and the evidence row are one transaction (ADR-0068 section 4;
  the service itself refuses anything else on PostgreSQL). The deck holds no
  database login, and the worker login has no privilege on these tables, so a
  composition handed the wrong login fails at the first write, not silently.

Staging keeps the shipped bounds (:data:`K13_STAGING_BOUNDS_V1`): 64 KiB frames
(the client's default, so no layout negotiation), 8 MiB per output, 32 MiB
staged, 8 uploads in flight, 900 s. An output above the bound is not
registrable in v1 and stays deck-local evidence (ADR-0069 section 6).

Nothing here opens a connection, reads configuration or names a host: the caller
supplies the connection factory.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager

from psycopg import Connection

from quant_platform.experiments.persistence import ExperimentRepository

from .admitted_input import AdmittedInputStore
from .feature_provider_identity import ReplayOutputContractV1
from .governed_result_import import (
    GovernedResultEvidenceStore,
    GovernedResultImportService,
    ResultOutputContractV1,
)
from .result_import_transport import ImportServiceProvider, StagedOutputStore, StagingBounds

MIB = 1024 * 1024

K13_OUTPUT_ROLES_V1: tuple[ResultOutputContractV1, ...] = (
    ResultOutputContractV1("normalizer", "supervised-normalizer", "supervised-normalizer-v1"),
    ResultOutputContractV1("model", "supervised-model", "supervised-model-v1"),
    ResultOutputContractV1("predictions", "supervised-predictions", "supervised-predictions-v1"),
    ResultOutputContractV1("metrics", "supervised-metrics", "supervised-metrics-v1"),
    ReplayOutputContractV1("replay-result", "replay-result", "replay-result-v1"),
    ReplayOutputContractV1("replay-summary", "replay-summary", "replay-summary-v1"),
)

# The shipped StagingBounds defaults, stated: ADR-0072 section 2 keeps them.
K13_STAGING_BOUNDS_V1 = StagingBounds(
    frame_payload_bytes=64 * 1024,
    max_output_bytes=8 * MIB,
    max_staged_bytes=32 * MIB,
    max_in_flight_uploads=8,
    retention_seconds=900,
)

RuntimeConnectionFactory = Callable[[], AbstractContextManager[Connection]]


def governed_result_import_service(
    connection: Connection, staging: StagedOutputStore,
) -> GovernedResultImportService:
    """One K13 service over one autocommit connection of the API login.

    The admission store, the Experiment repository and the evidence ledger are
    built here on that connection, so they cannot be on different ones; the
    stores themselves refuse a connection that is not autocommit.
    """

    if not isinstance(connection, Connection):
        raise TypeError("connection must be a psycopg connection")
    if not isinstance(staging, StagedOutputStore):
        raise TypeError("staging must be a StagedOutputStore")
    return GovernedResultImportService(
        AdmittedInputStore(connection),
        ExperimentRepository(connection),
        GovernedResultEvidenceStore(connection),
        staging.verify_output,
        K13_OUTPUT_ROLES_V1,
    )


def governed_result_import_service_provider(
    connections: RuntimeConnectionFactory, staging: StagedOutputStore,
) -> ImportServiceProvider:
    """The ``service_provider`` of :func:`result_import_family_handlers`.

    ``connections`` yields a new autocommit connection of the API login per
    request and closes it on exit (the stores are used from worker threads).
    """

    if not callable(connections):
        raise TypeError("connections must be callable")
    if not isinstance(staging, StagedOutputStore):
        raise TypeError("staging must be a StagedOutputStore")

    @contextmanager
    def session():
        with connections() as connection:
            yield governed_result_import_service(connection, staging)

    return session


__all__ = [
    "K13_OUTPUT_ROLES_V1",
    "K13_STAGING_BOUNDS_V1",
    "RuntimeConnectionFactory",
    "governed_result_import_service",
    "governed_result_import_service_provider",
]

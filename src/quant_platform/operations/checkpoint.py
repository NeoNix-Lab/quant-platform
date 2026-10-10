"""K10 live-ingest checkpoint/recovery v1: a pure identity/validation seam.

Mirrors ``quant_platform.operations.recovery``'s discipline: this module
proves nothing about a WebSocket session, a catalog connection or a
filesystem beyond one local checkpoint file.  It binds caller-supplied,
already-authoritative evidence (the canonical dataset identity, the live
source/semantics domain, the last durably published canonical trade key,
and the durable publication generation that key was published under) into
one deterministic ``LiveCheckpointV1`` identity, and enforces ADR-0042's
monotonic-advancement invariants -- it never connects to Bybit, queries a
catalog, or decides whether reconnection is possible; that composition
belongs to ``quant_platform.application.bybit_live``, exactly as K08's
``RecoverySetV1`` composes into ``application.backup_restore``.

A checkpoint represents *durably published canonical progress*, never a
transport/message position (ADR-0042 S1): there is deliberately no field
for "last WebSocket frame" or "last parsed message".  ``last_observed_sequence``
is retained only as diagnostic source evidence -- ADR-0040 already forbids
treating Bybit ``seq`` as a gap-free cursor, and this module must not
smuggle that assumption back in through the checkpoint.

The last durable canonical key is stored as its own ``(exchange_ts,
trade_id)`` fields rather than as a
``quant_platform.source_adapters.bybit_live.TradeKeyV1`` instance: this
module is "operations"-owned and the package-boundary seam forbids
"operations" from depending on "source" (see
``tests/test_package_boundaries_v1.py``), exactly why ``RecoverySetV1``
carries manifest digests as plain hex strings instead of importing
producer-layer classes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..data.models import DatasetIdentity, Instant
from quant_platform.canonical import canonical_bytes


LIVE_CHECKPOINT_IDENTITY_DOMAIN = "live-checkpoint-v1"


class CheckpointError(ValueError):
    """A K10 checkpoint value or transition violates the frozen v1 contract."""


class CheckpointDomainMismatch(CheckpointError):
    """A candidate checkpoint's dataset/live-semantics domain differs from the prior one.

    ADR-0042 S4: a changed ``DatasetIdentity`` or source/live-semantic
    identity starts a *distinct* recovery domain rather than silently
    reusing an old checkpoint.  Raising here forces the caller to make that
    an explicit decision (a fresh generation-1 checkpoint under the new
    domain) instead of quietly overwriting unrelated recovery state.
    """


class CheckpointRegressionError(CheckpointError):
    """A candidate checkpoint would regress or fail to strictly advance durable progress."""


class CheckpointCorruptError(CheckpointError):
    """Persisted checkpoint state is malformed and must fail closed (ADR-0042 S6)."""


class CheckpointBindingError(CheckpointError):
    """A checkpoint's bound publication generation no longer matches durable catalog state."""


@dataclass(frozen=True, slots=True)
class LiveCheckpointV1:
    """The durable K10 recovery unit for one live-ingest dataset/semantics domain.

    Fields are exactly ADR-0042 S1's minimum bound evidence -- nothing is
    carried "because it might be useful later":

    - ``dataset_identity``/``source_semantics_id``: the recovery domain.
      Two checkpoints with different values here are never comparable
      (:class:`CheckpointDomainMismatch`).
    - ``last_canonical_exchange_ts``/``last_canonical_trade_id``: the last
      durably published canonical ``(exchange_ts, trade_id)`` order key --
      the same reduction ``TradeKeyV1.order_key``/
      ``bybit_trade_v1_ordering_key`` already use, kept as raw fields so
      this module does not import the source-layer class (see module
      docstring).
    - ``last_observed_sequence``: diagnostic only, never authoritative
      (ADR-0040/ADR-0042: Bybit ``seq`` is not a gap-free cursor).
    - ``catalog_dataset_id``/``partition_key``/``revision``/
      ``partition_manifest_sha256``: the durable publication generation
      this checkpoint's progress was published under (S13-sealed evidence,
      carried as opaque identifiers exactly like ``RecoverySetV1`` carries
      manifest digests -- never a live ``SealedCatalogPartition`` object).
    - ``coverage_segment_id``: the currently governed live coverage
      assertion identity (the ``coverage_id`` a live coverage document was
      built under), so a checkpoint can be rejected if it references a
      coverage segment that has since been superseded/interrupted.
    - ``generation``: the monotonic checkpoint version. The first
      checkpoint in a domain is generation 1; :func:`advance_checkpoint`
      enforces every subsequent one advances by exactly one.
    """

    dataset_identity: DatasetIdentity
    source_semantics_id: str
    last_canonical_exchange_ts: Instant
    last_canonical_trade_id: str
    last_observed_sequence: str | None
    catalog_dataset_id: str
    partition_key: str
    revision: int
    partition_manifest_sha256: str
    coverage_segment_id: str
    generation: int
    created_at: Instant

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise CheckpointError("dataset_identity must be DatasetIdentity")
        object.__setattr__(
            self, "source_semantics_id",
            _non_empty_text(self.source_semantics_id, "source_semantics_id"),
        )
        if not isinstance(self.last_canonical_exchange_ts, Instant):
            raise CheckpointError("last_canonical_exchange_ts must be a canonical Instant")
        object.__setattr__(
            self, "last_canonical_trade_id",
            _non_empty_text(self.last_canonical_trade_id, "last_canonical_trade_id"),
        )
        if self.last_observed_sequence is not None:
            object.__setattr__(
                self, "last_observed_sequence",
                _non_empty_text(self.last_observed_sequence, "last_observed_sequence"),
            )
        object.__setattr__(
            self, "catalog_dataset_id", _non_empty_text(self.catalog_dataset_id, "catalog_dataset_id"),
        )
        object.__setattr__(
            self, "partition_key", _non_empty_text(self.partition_key, "partition_key"),
        )
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        object.__setattr__(
            self, "partition_manifest_sha256",
            _sha256_hex(self.partition_manifest_sha256, "partition_manifest_sha256"),
        )
        object.__setattr__(
            self, "coverage_segment_id", _non_empty_text(self.coverage_segment_id, "coverage_segment_id"),
        )
        object.__setattr__(self, "generation", _positive_int(self.generation, "generation"))
        if not isinstance(self.created_at, Instant):
            raise CheckpointError("created_at must be a canonical Instant")

    @property
    def last_canonical_order_key(self) -> tuple[Instant, str]:
        return (self.last_canonical_exchange_ts, self.last_canonical_trade_id)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": LIVE_CHECKPOINT_IDENTITY_DOMAIN,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "source_semantics_id": self.source_semantics_id,
            "last_canonical_exchange_ts": self.last_canonical_exchange_ts.isoformat(),
            "last_canonical_trade_id": self.last_canonical_trade_id,
            "last_observed_sequence": self.last_observed_sequence,
            "catalog_dataset_id": self.catalog_dataset_id,
            "partition_key": self.partition_key,
            "revision": self.revision,
            "partition_manifest_sha256": self.partition_manifest_sha256,
            "coverage_segment_id": self.coverage_segment_id,
            "generation": self.generation,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def checkpoint_identity(self) -> str:
        return f"{LIVE_CHECKPOINT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    def stable_dict(self) -> dict[str, Any]:
        return {"checkpoint_identity": self.checkpoint_identity, "canonical_payload": self.canonical_payload()}

    def is_same_domain(self, other: "LiveCheckpointV1") -> bool:
        return (
            self.dataset_identity == other.dataset_identity
            and self.source_semantics_id == other.source_semantics_id
        )


def advance_checkpoint(
    previous: LiveCheckpointV1 | None, candidate: LiveCheckpointV1,
) -> LiveCheckpointV1:
    """Validate and return ``candidate`` as the next durable checkpoint.

    ADR-0042 S2/S4 in code:

    - ``previous is None``: ``candidate`` must be generation 1 -- the first
      checkpoint in a fresh recovery domain.
    - domain mismatch (``dataset_identity``/``source_semantics_id`` differ):
      refused with :class:`CheckpointDomainMismatch`. A genuinely new
      domain is a deliberate fresh call with ``previous=None``, never an
      implicit reuse of unrelated recovery state.
    - same domain, identical ``checkpoint_identity``: idempotent no-op,
      returns ``previous`` unchanged (repeated equivalent durable
      checkpoint inputs must not be treated as a regression).
    - otherwise: ``generation`` must advance by exactly one, and the
      candidate's canonical order key must strictly exceed the previous
      one -- "canonical publication becomes durable BEFORE checkpoint may
      advance past that publication" only holds if advancement always
      means real forward progress, never a same-or-earlier key replayed
      under a bumped generation number.
    """
    if previous is None:
        if candidate.generation != 1:
            raise CheckpointRegressionError(
                "the first checkpoint in a recovery domain must be generation 1"
            )
        return candidate
    if not previous.is_same_domain(candidate):
        raise CheckpointDomainMismatch(
            "candidate checkpoint's dataset_identity/source_semantics_id domain differs "
            "from the prior checkpoint; start a fresh domain explicitly (previous=None) "
            "instead of advancing across domains"
        )
    if candidate.checkpoint_identity == previous.checkpoint_identity:
        return previous
    if candidate.generation != previous.generation + 1:
        raise CheckpointRegressionError(
            f"checkpoint generation must advance by exactly one "
            f"(previous={previous.generation}, candidate={candidate.generation})"
        )
    if candidate.last_canonical_order_key <= previous.last_canonical_order_key:
        raise CheckpointRegressionError(
            "candidate checkpoint does not strictly advance durable canonical progress"
        )
    return candidate


def validate_publication_binding(
    checkpoint: LiveCheckpointV1,
    *,
    catalog_dataset_id: str,
    partition_key: str,
    revision: int,
    partition_manifest_sha256: str,
) -> None:
    """Refuse a checkpoint whose bound publication generation no longer matches
    the durable catalog state a restart actually observes (ADR-0042 S4/S6:
    "reference an older/incompatible publication generation silently" is
    forbidden). The caller supplies freshly queried catalog values; this
    function never opens a connection itself."""
    if checkpoint.catalog_dataset_id != catalog_dataset_id:
        raise CheckpointBindingError("checkpoint references a different catalog dataset_id")
    if checkpoint.partition_key != partition_key:
        raise CheckpointBindingError("checkpoint references a different partition_key")
    if checkpoint.revision != revision:
        raise CheckpointBindingError(
            f"checkpoint references revision {checkpoint.revision}, "
            f"durable catalog state is at revision {revision}"
        )
    if checkpoint.partition_manifest_sha256 != partition_manifest_sha256:
        raise CheckpointBindingError(
            "checkpoint's bound partition manifest no longer matches durable catalog state"
        )


def live_checkpoint_from_canonical_payload(payload: Mapping[str, Any]) -> LiveCheckpointV1:
    """Reconstruct a :class:`LiveCheckpointV1` from its own durably persisted
    ``canonical_payload``. Every field is re-derived and re-validated by the
    same ``__post_init__`` a fresh checkpoint uses -- nothing is trusted as
    an opaque blob (mirrors ``recovery_set_from_canonical_payload``)."""
    if not isinstance(payload, Mapping):
        raise CheckpointCorruptError("checkpoint canonical payload must be a mapping")
    if payload.get("identity_domain") != LIVE_CHECKPOINT_IDENTITY_DOMAIN:
        raise CheckpointCorruptError("checkpoint canonical payload has an unsupported identity_domain")
    try:
        return LiveCheckpointV1(
            dataset_identity=DatasetIdentity(**payload["dataset_identity"]),
            source_semantics_id=payload["source_semantics_id"],
            last_canonical_exchange_ts=Instant.parse(payload["last_canonical_exchange_ts"]),
            last_canonical_trade_id=payload["last_canonical_trade_id"],
            last_observed_sequence=payload.get("last_observed_sequence"),
            catalog_dataset_id=payload["catalog_dataset_id"],
            partition_key=payload["partition_key"],
            revision=payload["revision"],
            partition_manifest_sha256=payload["partition_manifest_sha256"],
            coverage_segment_id=payload["coverage_segment_id"],
            generation=payload["generation"],
            created_at=Instant.parse(payload["created_at"]),
        )
    except CheckpointError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise CheckpointCorruptError("checkpoint canonical payload is malformed") from exc


class CheckpointStore:
    """Atomic local-file persistence for one :class:`LiveCheckpointV1`.

    "Prefer a small local store/file/record mechanism" (issue #109): a
    single JSON file, written via write-temp-then-``os.replace`` so a
    crash mid-write can never leave a partially-written checkpoint (POSIX
    and Windows both guarantee ``os.replace`` is atomic within one
    filesystem). No generic checkpoint provider, database or event store.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> LiveCheckpointV1 | None:
        """Returns ``None`` only when no checkpoint file exists yet (a fresh
        domain). Any existing-but-unreadable file fails closed."""
        if not self._path.exists():
            return None
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CheckpointCorruptError(f"checkpoint file could not be read: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CheckpointCorruptError("checkpoint file is not valid JSON") from exc
        return live_checkpoint_from_canonical_payload(payload)

    def save(self, checkpoint: LiveCheckpointV1) -> None:
        if not isinstance(checkpoint, LiveCheckpointV1):
            raise CheckpointError("save() requires a LiveCheckpointV1")
        document = dict(checkpoint.canonical_payload())
        document["checkpoint_identity"] = checkpoint.checkpoint_identity
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(f"{self._path.name}.tmp-{os.getpid()}")
        tmp_path.write_text(_canonical_json(document), encoding="utf-8")
        os.replace(tmp_path, self._path)


def _positive_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise CheckpointError(f"{field_name} must be a positive integer")
    return value


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CheckpointError(f"{field_name} must be a non-empty string")
    return value.strip()


def _sha256_hex(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise CheckpointError(f"{field_name} must be 64 lowercase hex characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise CheckpointError(f"{field_name} must be 64 lowercase hex characters") from exc
    if value != value.lower():
        raise CheckpointError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _canonical_json(payload: Any) -> str:
    return canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")


def _canonical_fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "LIVE_CHECKPOINT_IDENTITY_DOMAIN",
    "CheckpointBindingError",
    "CheckpointCorruptError",
    "CheckpointDomainMismatch",
    "CheckpointError",
    "CheckpointRegressionError",
    "CheckpointStore",
    "LiveCheckpointV1",
    "advance_checkpoint",
    "live_checkpoint_from_canonical_payload",
    "validate_publication_binding",
]

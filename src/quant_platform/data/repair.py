"""A10 backfill/repair v1: deterministic repair intent, isolated candidates,
and the dedicated atomic replacement cutover.

Repair is reconciliation over explicit defects, never a heuristic. This
module owns only the bounded seam described by the frozen A10 contract:

- deterministic, evidence-bound repair-intent identity (no event-spacing,
  file-age, row-count, wall-clock or "latest source" heuristic ever creates
  a repair target);
- immutable, content-addressed candidate-attempt identity, whose derived
  staging ``partition_key`` gives distinct attempts physically isolated
  storage/catalog rows before convergence, purely by being a different
  natural-key value -- no new schema, table or column is introduced;
- one dedicated transactional compare-and-cutover seam that supersedes the
  predecessor and promotes the candidate atomically, only after the
  candidate is already proven through the existing S13/A16/S14 machinery
  under its own isolated staging partition-key family.

Candidate sealing, quality assessment and publication eligibility are
deliberately NOT reimplemented here: a caller certifies a candidate by
calling the existing ``CatalogPublicationWriter.seal_partition``,
``QualityLifecycleCatalog.apply_partition_lifecycle`` and
``PublicationEligibilityCatalog.publish`` with the candidate's own
``staging_partition_key`` as the partition manifest's ``partition_key``.
Because that staging key is never the natural partition_key, none of those
existing writers ever touches -- let alone supersedes -- the live
predecessor while the candidate is still being proven.  Only
:class:`RepairCutoverCatalog.cutover` ever changes the natural partition's
topology, and it does so in one transaction, never before candidate proof
is already durable.

This module must not import ``quant_platform.access``: callers pass already
computed B04 gap evidence and A16 assessment evidence in as canonical
``CoverageInterval``/string values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping

from .models import CoverageInterval, DatasetIdentity, Instant


REPAIR_SEMANTICS_VERSION = "a10-repair-v1"
REPAIR_INTENT_IDENTITY_DOMAIN = "a10-repair-intent-v1"
CANDIDATE_ATTEMPT_IDENTITY_DOMAIN = "a10-candidate-attempt-v1"

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_ELIGIBLE_LIVE_STATES = frozenset({"closed", "valid", "degraded", "invalid"})
_COVERING_STATES = frozenset({"valid", "degraded"})


class RepairError(RuntimeError):
    """Base for A10 repair refusals."""


class RepairIneligible(RepairError):
    """Raised when supplied evidence does not establish genuine repair eligibility."""


class RepairCutoverRefusal(RepairError):
    """Raised when the atomic compare-and-cutover transaction cannot proceed."""


class RepairOutcome(StrEnum):
    """The frozen A10 v1 outcome vocabulary."""

    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    CANDIDATE_PENDING = "CANDIDATE_PENDING"
    CONVERGED = "CONVERGED"
    ALREADY_SATISFIED = "ALREADY_SATISFIED"
    FAILED = "FAILED"
    STALE_CONFLICT = "STALE_CONFLICT"


@dataclass(frozen=True, slots=True)
class PredecessorReference:
    """The exact durable identity of a live revision captured at intent build time.

    Trigger evidence is target-bound: this is the immutable snapshot the
    cutover later compares against current topology to detect staleness.
    """

    partition_id: str
    revision: int
    state: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "partition_id", _non_empty_text(self.partition_id, "partition_id"))
        if type(self.revision) is not int or isinstance(self.revision, bool) or self.revision < 1:
            raise RepairError("predecessor revision must be a positive integer")
        if self.state not in _ELIGIBLE_LIVE_STATES:
            raise RepairError(f"predecessor state {self.state!r} is not a live catalog state")

    def stable_dict(self) -> dict[str, Any]:
        return {"partition_id": self.partition_id, "revision": self.revision, "state": self.state}


@dataclass(frozen=True, slots=True)
class CoverageGapTrigger:
    """Immutable B04-style gap evidence: one or more exact non-empty gaps.

    Also represents "explicitly unavailable required support" when the sole
    gap equals the entire required support and no predecessor exists -- B04's
    own ``gaps_for`` naturally returns exactly that when nothing is covered,
    so no distinct trigger type is needed.
    """

    gaps: tuple[CoverageInterval, ...]

    def __post_init__(self) -> None:
        gaps = tuple(self.gaps)
        if not gaps:
            raise RepairIneligible("missing-support trigger requires at least one non-empty gap")
        for index, gap in enumerate(gaps):
            if not isinstance(gap, CoverageInterval):
                raise RepairError(f"gaps[{index}] must be a CoverageInterval")
            if not gap.start < gap.end:
                raise RepairIneligible(f"gaps[{index}] must be a non-degenerate half-open interval")
        ordered = tuple(sorted(gaps, key=lambda item: (item.start.epoch_ns, item.end.epoch_ns)))
        for left, right in zip(ordered, ordered[1:]):
            if left.end > right.start:
                raise RepairError("gaps must not overlap")
        object.__setattr__(self, "gaps", ordered)

    def stable_dict(self) -> dict[str, Any]:
        return {"kind": "coverage_gap", "gaps": [gap.stable_dict() for gap in self.gaps]}


@dataclass(frozen=True, slots=True)
class InvalidLiveRevisionTrigger:
    """Immutable A16 evidence that the current live revision is ``fail``/``invalid``."""

    predecessor: PredecessorReference
    assessment_signature: str
    assessment_status: str

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, PredecessorReference):
            raise RepairError("predecessor must be a PredecessorReference")
        if self.predecessor.state != "invalid":
            raise RepairIneligible(
                "invalid-live-revision trigger requires the captured predecessor state to be 'invalid'"
            )
        object.__setattr__(
            self, "assessment_signature", _non_empty_text(self.assessment_signature, "assessment_signature")
        )
        if self.assessment_status != "fail":
            raise RepairIneligible(
                "invalid-live-revision trigger requires an A16 'fail' assessment status"
            )
        object.__setattr__(self, "assessment_status", self.assessment_status)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": "invalid_live_revision",
            "predecessor": self.predecessor.stable_dict(),
            "assessment_signature": self.assessment_signature,
            "assessment_status": self.assessment_status,
        }


RepairTrigger = CoverageGapTrigger | InvalidLiveRevisionTrigger


@dataclass(frozen=True, slots=True)
class RepairIntent:
    """One deterministic, partition-bounded, evidence-driven repair target.

    ``warn``/``degraded`` current state and a merely newer/different
    candidate source are never sufficient: construction only succeeds when
    ``trigger`` already establishes genuine eligibility under the frozen A10
    rules (enforced by :class:`CoverageGapTrigger` /
    :class:`InvalidLiveRevisionTrigger` themselves), and every field
    participates in ``intent_identity`` so a changed predecessor/trigger can
    never silently retarget an existing intent.
    """

    dataset_identity: DatasetIdentity
    partition_key: str
    required_support: CoverageInterval
    predecessor: PredecessorReference | None
    trigger: RepairTrigger
    semantics_version: str = REPAIR_SEMANTICS_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RepairError("dataset_identity must be a DatasetIdentity")
        object.__setattr__(self, "partition_key", _non_empty_text(self.partition_key, "partition_key"))
        if not isinstance(self.required_support, CoverageInterval):
            raise RepairError("required_support must be a CoverageInterval")
        if not self.required_support.start < self.required_support.end:
            raise RepairError("required_support must be a non-degenerate half-open interval")
        if self.predecessor is not None and not isinstance(self.predecessor, PredecessorReference):
            raise RepairError("predecessor must be a PredecessorReference or None")
        if self.predecessor is not None and self.predecessor.state == "superseded":
            raise RepairError("a superseded row is history, not a repair predecessor")
        if not isinstance(self.trigger, (CoverageGapTrigger, InvalidLiveRevisionTrigger)):
            raise RepairError("trigger must be CoverageGapTrigger or InvalidLiveRevisionTrigger")
        if isinstance(self.trigger, CoverageGapTrigger):
            for index, gap in enumerate(self.trigger.gaps):
                if gap.start < self.required_support.start or gap.end > self.required_support.end:
                    raise RepairError(f"gaps[{index}] is not contained in required_support")
        else:
            if self.predecessor is None or self.predecessor != self.trigger.predecessor:
                raise RepairError(
                    "invalid-live-revision trigger predecessor must equal the intent's own predecessor"
                )
        if self.semantics_version != REPAIR_SEMANTICS_VERSION:
            raise RepairError("unsupported repair semantics_version")

    @property
    def outcome(self) -> RepairOutcome:
        return RepairOutcome.REPAIR_REQUIRED

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": REPAIR_INTENT_IDENTITY_DOMAIN,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "required_support": self.required_support.stable_dict(),
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
            "trigger": self.trigger.stable_dict(),
            "semantics_version": self.semantics_version,
        }

    @property
    def intent_identity(self) -> str:
        return f"{REPAIR_INTENT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    def stable_dict(self) -> dict[str, Any]:
        return {"intent_identity": self.intent_identity, "canonical_payload": self.canonical_payload()}


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """One immutable, content-addressed non-authoritative repair candidate.

    ``candidate_identity`` is a deterministic fingerprint of every field:
    semantically identical retries (same intent, same source/transform/
    durable evidence) compute the exact same identity and the exact same
    ``staging_partition_key``, so they naturally reuse/credit one candidate
    row (idempotent retry).  Different content/evidence computes a distinct
    identity and a distinct staging key, so it is a distinct, independently
    proven candidate that can never collide with -- or clobber -- another
    attempt's staging row.
    """

    intent_identity: str
    dataset_identity: DatasetIdentity
    natural_partition_key: str
    source_semantics_id: str
    mapping_id: str
    dataset_sha256: str
    partition_sha256: str
    content_sha256: str
    code_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_identity", _non_empty_text(self.intent_identity, "intent_identity"))
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RepairError("dataset_identity must be a DatasetIdentity")
        object.__setattr__(
            self, "natural_partition_key", _non_empty_text(self.natural_partition_key, "natural_partition_key")
        )
        object.__setattr__(
            self, "source_semantics_id", _non_empty_text(self.source_semantics_id, "source_semantics_id")
        )
        object.__setattr__(self, "mapping_id", _non_empty_text(self.mapping_id, "mapping_id"))
        object.__setattr__(self, "dataset_sha256", _sha256_hex(self.dataset_sha256, "dataset_sha256"))
        object.__setattr__(self, "partition_sha256", _sha256_hex(self.partition_sha256, "partition_sha256"))
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "code_ref", _non_empty_text(self.code_ref, "code_ref"))

    @property
    def outcome(self) -> RepairOutcome:
        return RepairOutcome.CANDIDATE_PENDING

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": CANDIDATE_ATTEMPT_IDENTITY_DOMAIN,
            "intent_identity": self.intent_identity,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "natural_partition_key": self.natural_partition_key,
            "source_semantics_id": self.source_semantics_id,
            "mapping_id": self.mapping_id,
            "dataset_sha256": self.dataset_sha256,
            "partition_sha256": self.partition_sha256,
            "content_sha256": self.content_sha256,
            "code_ref": self.code_ref,
        }

    @property
    def candidate_identity(self) -> str:
        return f"{CANDIDATE_ATTEMPT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def staging_partition_key(self) -> str:
        """The physically/logically isolated natural-key value for this attempt.

        A different ``partition_key`` value is a different row family under
        ``partitions_one_live`` and a different ``rel_path`` prefix under
        ``rel_path_inside_partition`` -- both existing, unmodified DB/manifest
        constraints -- so distinct attempts can never share mutable physical
        or catalog state before convergence.
        """

        digest = hashlib.sha256(self.candidate_identity.encode("utf-8")).hexdigest()
        return f"{self.natural_partition_key}/repair={digest}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "candidate_identity": self.candidate_identity,
            "staging_partition_key": self.staging_partition_key,
            "canonical_payload": self.canonical_payload(),
        }


@dataclass(frozen=True, slots=True)
class RepairCutoverResult:
    """Immutable convergence provenance for one cutover attempt.

    ``status`` is restricted to the three outcomes a cutover transaction can
    itself decide: :attr:`RepairOutcome.CONVERGED`,
    :attr:`RepairOutcome.ALREADY_SATISFIED` or
    :attr:`RepairOutcome.STALE_CONFLICT`.
    """

    status: RepairOutcome
    intent_identity: str
    dataset_id: str
    natural_partition_key: str
    predecessor: PredecessorReference | None
    candidate_identity: str | None
    live_partition_id: str | None
    live_revision: int | None
    live_state: str | None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in (
            RepairOutcome.CONVERGED,
            RepairOutcome.ALREADY_SATISFIED,
            RepairOutcome.STALE_CONFLICT,
        ):
            raise RepairError("RepairCutoverResult.status must be CONVERGED, ALREADY_SATISFIED or STALE_CONFLICT")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "intent_identity": self.intent_identity,
            "dataset_id": self.dataset_id,
            "natural_partition_key": self.natural_partition_key,
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
            "candidate_identity": self.candidate_identity,
            "live_partition_id": self.live_partition_id,
            "live_revision": self.live_revision,
            "live_state": self.live_state,
            "detail": self.detail,
        }


class RepairCutoverCatalog:
    """The one dedicated A10 atomic compare-and-cutover seam.

    Deliberately does not seal, certify or assess the candidate: by the time
    ``cutover`` is called, the candidate must already be a ``valid``/
    ``degraded`` row under its own isolated ``staging_partition_key``,
    proven through the ordinary, unmodified S13/A16/S14 machinery.  This
    method performs only the one transactional compare-and-swap the ordinary
    forward-ingest S13 admission path is not permitted to perform for
    replacement repair: verifying predecessor/trigger authority still
    matches the repair intent, then -- in one commit -- superseding the
    predecessor and promoting the candidate to be the sole live revision.
    """

    def __init__(self, connection: Any):
        self.connection = connection

    def cutover(
        self,
        *,
        intent: RepairIntent,
        candidate: CandidateAttempt,
    ) -> RepairCutoverResult:
        if not isinstance(intent, RepairIntent):
            raise RepairError("intent must be a RepairIntent")
        if not isinstance(candidate, CandidateAttempt):
            raise RepairError("candidate must be a CandidateAttempt")
        if candidate.intent_identity != intent.intent_identity:
            raise RepairError("candidate does not target this repair intent")

        identity = intent.dataset_identity
        try:
            with self.connection.cursor() as cursor:
                dataset_id = self._resolve_dataset(cursor, identity)
                natural = self._lock_topology(cursor, dataset_id, intent.partition_key)
                staging = self._lock_topology(cursor, dataset_id, candidate.staging_partition_key)

                current_live = _live_row(natural)
                verdict, detail = _compare_authority(intent, current_live)
                if verdict is RepairOutcome.ALREADY_SATISFIED:
                    self.connection.commit()
                    return _result(
                        RepairOutcome.ALREADY_SATISFIED, intent, dataset_id, candidate_identity=None,
                        live=current_live, detail=detail,
                    )
                if verdict is RepairOutcome.STALE_CONFLICT:
                    self.connection.commit()
                    return _result(
                        RepairOutcome.STALE_CONFLICT, intent, dataset_id, candidate_identity=None,
                        live=current_live, detail=detail,
                    )

                staged = _single_row(staging, "candidate staging partition")
                if staged[4] not in _COVERING_STATES:
                    raise RepairCutoverRefusal(
                        f"candidate is not proven: staging state is {staged[4]!r}, expected valid/degraded"
                    )
                if not _covers(staged, intent.required_support):
                    raise RepairCutoverRefusal(
                        "candidate does not cover the full required support; a target gap remains"
                    )

                next_revision = 1 if intent.predecessor is None else intent.predecessor.revision + 1
                if intent.predecessor is not None:
                    cursor.execute(
                        """
                        UPDATE catalog.partitions
                           SET state = 'superseded'
                         WHERE partition_id = %s AND state = %s
                        RETURNING partition_id::text
                        """,
                        (intent.predecessor.partition_id, intent.predecessor.state),
                    )
                    if cursor.fetchone() is None:
                        raise RepairCutoverRefusal("predecessor could not be superseded atomically")

                cursor.execute(
                    """
                    UPDATE catalog.partitions
                       SET partition_key = %s, revision = %s
                     WHERE partition_id = %s AND state = %s
                    RETURNING partition_id::text
                    """,
                    (intent.partition_key, next_revision, staged[0], staged[4]),
                )
                if cursor.fetchone() is None:
                    raise RepairCutoverRefusal("candidate could not be promoted atomically")

                final = self._lock_topology(cursor, dataset_id, intent.partition_key)
                promoted = _live_row(final)
                if (
                    promoted is None
                    or str(promoted[0]) != str(staged[0])
                    or int(promoted[3]) != next_revision
                    or promoted[4] != staged[4]
                ):
                    raise RepairCutoverRefusal("post-cutover topology verification failed")
                if intent.predecessor is not None:
                    predecessor_row = next(
                        (row for row in final if str(row[0]) == intent.predecessor.partition_id), None
                    )
                    if predecessor_row is None or predecessor_row[4] != "superseded":
                        raise RepairCutoverRefusal("predecessor did not verify as superseded post-cutover")

            self.connection.commit()
            return _result(
                RepairOutcome.CONVERGED, intent, dataset_id, candidate_identity=candidate.candidate_identity,
                live=promoted, detail="",
            )
        except Exception:
            self.connection.rollback()
            raise

    @staticmethod
    def _resolve_dataset(cursor: Any, identity: DatasetIdentity) -> str:
        cursor.execute(
            """
            SELECT dataset_id::text
              FROM catalog.datasets
             WHERE layer=%s AND kind=%s AND venue=%s
               AND instrument=%s AND schema_id=%s
               AND feature_set_def_id IS NULL
             FOR UPDATE
            """,
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise RepairCutoverRefusal("dataset natural identity is missing or duplicated")
        return str(rows[0][0])

    @staticmethod
    def _lock_topology(cursor: Any, dataset_id: str, partition_key: str):
        cursor.execute(
            """
            SELECT partition_id::text, dataset_id::text, partition_key,
                   revision, state, ts_start, ts_end
              FROM catalog.partitions
             WHERE dataset_id=%s AND partition_key=%s
             ORDER BY revision
             FOR UPDATE
            """,
            (dataset_id, partition_key),
        )
        return cursor.fetchall()


def _compare_authority(intent: RepairIntent, current_live: Any) -> tuple[RepairOutcome | None, str]:
    """Decide ALREADY_SATISFIED / STALE_CONFLICT / proceed (``None``)."""

    predecessor = intent.predecessor
    if predecessor is None:
        if current_live is None:
            return None, ""
        if _covers(current_live, intent.required_support):
            return RepairOutcome.ALREADY_SATISFIED, "a live revision now covers the required support"
        return RepairOutcome.STALE_CONFLICT, "a live revision now exists where the intent captured none"

    if current_live is None:
        return RepairOutcome.STALE_CONFLICT, "captured predecessor no longer exists"
    if (
        str(current_live[0]) == predecessor.partition_id
        and int(current_live[3]) == predecessor.revision
        and current_live[4] == predecessor.state
    ):
        return None, ""
    if _covers(current_live, intent.required_support):
        return RepairOutcome.ALREADY_SATISFIED, "current live revision already covers the required support"
    return RepairOutcome.STALE_CONFLICT, "current live revision no longer matches the captured predecessor"


def _covers(row: Any, required_support: CoverageInterval) -> bool:
    if row[4] not in _COVERING_STATES:
        return False
    start, end = row[5], row[6]
    if start is None or end is None:
        return False
    return Instant.parse(start) <= required_support.start and Instant.parse(end) >= required_support.end


def _live_row(rows: Any) -> Any | None:
    live = [row for row in rows if row[4] != "superseded"]
    if len(live) > 1:
        raise RepairCutoverRefusal("partition topology has more than one live revision")
    return live[0] if live else None


def _single_row(rows: Any, what: str) -> Any:
    if len(rows) != 1:
        raise RepairCutoverRefusal(f"{what} must resolve to exactly one row")
    return rows[0]


def _result(
    status: RepairOutcome,
    intent: RepairIntent,
    dataset_id: str,
    *,
    candidate_identity: str | None,
    live: Any,
    detail: str,
) -> RepairCutoverResult:
    return RepairCutoverResult(
        status=status,
        intent_identity=intent.intent_identity,
        dataset_id=dataset_id,
        natural_partition_key=intent.partition_key,
        predecessor=intent.predecessor,
        candidate_identity=candidate_identity,
        live_partition_id=None if live is None else str(live[0]),
        live_revision=None if live is None else int(live[3]),
        live_state=None if live is None else live[4],
        detail=detail,
    )


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise RepairError(f"{field_name} must not contain control characters")
    return text


def _sha256_hex(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise RepairError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "CANDIDATE_ATTEMPT_IDENTITY_DOMAIN",
    "REPAIR_INTENT_IDENTITY_DOMAIN",
    "REPAIR_SEMANTICS_VERSION",
    "CandidateAttempt",
    "CoverageGapTrigger",
    "InvalidLiveRevisionTrigger",
    "PredecessorReference",
    "RepairCutoverCatalog",
    "RepairCutoverRefusal",
    "RepairCutoverResult",
    "RepairError",
    "RepairIneligible",
    "RepairIntent",
    "RepairOutcome",
    "RepairTrigger",
]

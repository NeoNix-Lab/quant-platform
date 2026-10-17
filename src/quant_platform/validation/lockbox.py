"""Terminal lockbox v1 semantics and contamination guards (F07).

This module implements the canonical lockbox semantic isolation governed by
ADR-0036 under the Validation owner (``quant_platform.validation``).

A lockbox declares one terminal holdout interval ``[lockbox_start, lockbox_end)``.
Visibility is strictly monotonic: ``UNREVEALED -> REVEALED``.
While unrevealed, development samples and their dependency/target support must not
intersect the lockbox holdout interval.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from types import MappingProxyType
from typing import Any

from quant_platform.canonical import canonical_bytes
from quant_platform.data.models import CoverageInterval, Instant
from .availability import ValidationCandidate


LOCKBOX_IDENTITY_DOMAIN = "lockbox-v1"

_GOVERNED_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")


class LockboxError(ValueError):
    """A lockbox constraint, contamination rule or reveal transition was violated."""


class LockboxVisibility(StrEnum):
    UNREVEALED = "unrevealed"
    REVEALED = "revealed"


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LockboxError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(c) < 32 for c in text):
        raise LockboxError(f"{field_name} must not contain control characters")
    return text


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
    return hashlib.sha256(encoded).hexdigest()


def _intervals_intersect(first: CoverageInterval, second: CoverageInterval) -> bool:
    """Check whether two half-open intervals [s1, e1) and [s2, e2) intersect."""
    return first.start < second.end and second.start < first.end


@dataclass(frozen=True, slots=True)
class Lockbox:
    """Canonical terminal holdout lockbox with semantic isolation and monotonic reveal."""

    lockbox_id: str
    holdout: CoverageInterval
    visibility: LockboxVisibility = LockboxVisibility.UNREVEALED
    reveal_evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "lockbox_id", _non_empty_text(self.lockbox_id, "lockbox_id"))
        if not isinstance(self.holdout, CoverageInterval):
            raise LockboxError("holdout must be a CoverageInterval")

        if self.holdout.start >= self.holdout.end:
            raise LockboxError("holdout interval must have start < end")

        try:
            vis = LockboxVisibility(self.visibility)
        except ValueError as exc:
            raise LockboxError(f"unsupported visibility: {self.visibility!r}") from exc
        object.__setattr__(self, "visibility", vis)

        if self.visibility is LockboxVisibility.UNREVEALED:
            if self.reveal_evidence is not None:
                raise LockboxError("UNREVEALED lockbox must not carry reveal_evidence")
        else:
            if self.reveal_evidence is None or not isinstance(self.reveal_evidence, Mapping):
                raise LockboxError("REVEALED lockbox must carry reveal_evidence mapping")
            object.__setattr__(self, "reveal_evidence", MappingProxyType(dict(self.reveal_evidence)))

    @property
    def lockbox_start(self) -> Instant:
        return self.holdout.start

    @property
    def lockbox_end(self) -> Instant:
        return self.holdout.end

    @property
    def is_untouched(self) -> bool:
        """True if the lockbox remains unrevealed and uncontaminated by revealed results."""
        return self.visibility is LockboxVisibility.UNREVEALED

    def is_member(self, instant: Instant | str) -> bool:
        """Check whether candidate/reference instant d belongs to this lockbox population."""
        parsed = Instant.parse(instant) if not isinstance(instant, Instant) else instant
        return self.holdout.start <= parsed < self.holdout.end

    def check_development_candidate(self, candidate: ValidationCandidate) -> None:
        """Ensure a development candidate does not violate lockbox semantic isolation.

        While unrevealed:
        - candidate reference instant d must not fall inside the lockbox holdout;
        - declared dependency support (features and targets) must not intersect
          the lockbox holdout.

        Raises LockboxError on contamination.
        """
        if self.is_member(candidate.d):
            raise LockboxError(
                f"development candidate instant {candidate.d.isoformat()} is inside "
                f"the lockbox holdout [{self.lockbox_start.isoformat()}, {self.lockbox_end.isoformat()})"
            )

        if self.visibility is LockboxVisibility.UNREVEALED:
            for dep in candidate.dependencies:
                if dep.support is not None and _intervals_intersect(dep.support, self.holdout):
                    raise LockboxError(
                        f"development candidate at {candidate.d.isoformat()} has dependency "
                        f"'{dep.identity}' whose support [{dep.support.start.isoformat()}, "
                        f"{dep.support.end.isoformat()}) intersects the unrevealed lockbox "
                        f"[{self.lockbox_start.isoformat()}, {self.lockbox_end.isoformat()})"
                    )

    def reveal(self, reason: str, *, evidence: Mapping[str, Any] | None = None) -> Lockbox:
        """Transition lockbox from UNREVEALED to REVEALED explicitly and irreversibly.

        Once revealed, the lockbox is no longer untouched for development/selection
        decisions influenced by observed results.
        """
        if self.visibility is LockboxVisibility.REVEALED:
            raise LockboxError("lockbox has already been revealed; reveal is irreversible and cannot be repeated")

        non_empty_reason = _non_empty_text(reason, "reveal reason")
        payload = {
            "reason": non_empty_reason,
            "evidence": dict(evidence) if evidence is not None else {},
        }

        return Lockbox(
            lockbox_id=self.lockbox_id,
            holdout=self.holdout,
            visibility=LockboxVisibility.REVEALED,
            reveal_evidence=payload,
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": LOCKBOX_IDENTITY_DOMAIN,
            "lockbox_id": self.lockbox_id,
            "holdout_start": self.lockbox_start.isoformat(),
            "holdout_end": self.lockbox_end.isoformat(),
            "visibility": self.visibility.value,
            "reveal_evidence": dict(self.reveal_evidence) if self.reveal_evidence is not None else None,
        }

    @property
    def identity(self) -> str:
        return f"{LOCKBOX_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"


__all__ = [
    "LOCKBOX_IDENTITY_DOMAIN",
    "Lockbox",
    "LockboxError",
    "LockboxVisibility",
]

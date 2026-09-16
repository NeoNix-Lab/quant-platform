"""HypothesisSpec v1 semantic identity and value model.

This module owns the bounded F01 semantic foundation only: an immutable,
reproducible declaration that a conditional relationship is being
investigated across one or more canonical E02 observables.  It does not:

- compute or detect events (F02 EventSpec/detection);
- define forward outcomes/labels/censoring (F03 OutcomeSpec/Outcome, F07);
- perform validation/purge/embargo (F06);
- simulate strategy/execution (G/H spines) -- ADR-0007 keeps event research
  distinct from strategy backtesting;
- resolve concrete FeatureArtifact storage or datasets (E04);
- introduce a generic research DSL, registry or plugin framework.

A hypothesis references canonical observables by their E02
``FeatureDefinitionId`` -- an opaque typed identity -- rather than importing
or redefining concrete FeatureDefinition/FeatureArtifact runtime details.
Temporal availability of each referenced observable is already pinned by
that E02 identity; F01 declares no additional temporal window, decision
time, horizon or embargo of its own -- those remain owned by F02/F03/F06.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, ClassVar

from ..features import FeatureDefinitionId


HYPOTHESIS_SPEC_IDENTITY_DOMAIN = "hypothesis-spec-v1"
HYPOTHESIS_SPEC_MODEL_VERSION = "1"

_GOVERNED_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[1-9][0-9]*$")


class HypothesisSpecError(ValueError):
    """A HypothesisSpec v1 semantic value violates the frozen contract."""


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HypothesisSpecError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise HypothesisSpecError(f"{field} must not contain control characters")
    return text


def _governed_key(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if not _GOVERNED_KEY_RE.fullmatch(text):
        raise HypothesisSpecError(f"{field} must be a governed canonical key spelling")
    return text


def _semantic_version(value: Any, field: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field)
    if not _VERSION_RE.fullmatch(text):
        raise HypothesisSpecError(f"{field} must be an explicit positive version")
    return text


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ObservableReference:
    """An opaque typed reference to one canonical E02 observable.

    HypothesisSpec does not redefine FeatureDefinition semantics; it only
    carries the stable E02 identity of an observable the hypothesis is
    about.
    """

    feature_definition_id: FeatureDefinitionId | str

    def __post_init__(self) -> None:
        value = self.feature_definition_id
        if isinstance(value, str):
            value = FeatureDefinitionId(value)
        elif not isinstance(value, FeatureDefinitionId):
            raise HypothesisSpecError(
                "feature_definition_id must be a FeatureDefinitionId"
            )
        object.__setattr__(self, "feature_definition_id", value)

    def stable_dict(self) -> dict[str, str]:
        return {"feature_definition_id": str(self.feature_definition_id)}


@dataclass(frozen=True, slots=True)
class HypothesisSpecId:
    """Deterministic content-derived HypothesisSpec identity."""

    value: str

    def __post_init__(self) -> None:
        text = _non_empty_text(self.value, "HypothesisSpecId")
        prefix = f"{HYPOTHESIS_SPEC_IDENTITY_DOMAIN}:sha256:"
        if not text.startswith(prefix) or len(text.removeprefix(prefix)) != 64:
            raise HypothesisSpecError("HypothesisSpecId must be a v1 sha256 identity")
        object.__setattr__(self, "value", text)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HypothesisSpecId":
        return cls(
            f"{HYPOTHESIS_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"
        )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class HypothesisSpec:
    """Immutable, reproducible declaration of a conditional relationship
    hypothesized to hold across one or more canonical E02 observables.

    A HypothesisSpec makes no claim about how the relationship would be
    detected (F02 EventSpec), what forward outcome would be measured (F03
    OutcomeSpec/Outcome), how it would be validated (F06), or how it would
    be traded (Strategy/backtesting).  ``statement`` is the sole vehicle for
    the hypothesis's claim: F01 does not parse it into a formal predicate,
    since that would require a research DSL this issue does not authorize.
    ``notes`` is administrative annotation only and is explicitly excluded
    from semantic identity, unlike ``statement``.
    """

    hypothesis_key: str
    semantic_version: str | int
    statement: str
    observable_references: Iterable[ObservableReference]
    notes: str | None = None

    identity_type: ClassVar[str] = "hypothesis-spec"
    identity_version: ClassVar[str] = HYPOTHESIS_SPEC_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "hypothesis_key", _governed_key(self.hypothesis_key, "hypothesis_key")
        )
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        object.__setattr__(self, "statement", _non_empty_text(self.statement, "statement"))

        if isinstance(self.observable_references, (str, bytes, bytearray)):
            raise HypothesisSpecError(
                "observable_references must be an iterable of ObservableReference"
            )
        references = tuple(self.observable_references)
        if not references:
            raise HypothesisSpecError(
                "HypothesisSpec requires at least one observable reference"
            )
        for index, reference in enumerate(references):
            if not isinstance(reference, ObservableReference):
                raise HypothesisSpecError(
                    f"observable_references[{index}] must be ObservableReference"
                )
        deduped = {
            str(reference.feature_definition_id): reference for reference in references
        }
        canonical_references = tuple(deduped[key] for key in sorted(deduped))
        object.__setattr__(self, "observable_references", canonical_references)

        if self.notes is not None:
            object.__setattr__(self, "notes", _non_empty_text(self.notes, "notes"))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": self.identity_type,
            "identity_version": self.identity_version,
            "hypothesis_key": self.hypothesis_key,
            "semantic_version": self.semantic_version,
            "statement": self.statement,
            "observable_references": [
                reference.stable_dict() for reference in self.observable_references
            ],
        }

    @property
    def canonical_utf8_serialization(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )

    @property
    def spec_id(self) -> HypothesisSpecId:
        return HypothesisSpecId.from_payload(self.canonical_payload())

    @property
    def identity(self) -> str:
        return str(self.spec_id)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.identity,
            "canonical_payload": self.canonical_payload(),
            "notes": self.notes,
        }


__all__ = [
    "HYPOTHESIS_SPEC_IDENTITY_DOMAIN",
    "HYPOTHESIS_SPEC_MODEL_VERSION",
    "HypothesisSpec",
    "HypothesisSpecError",
    "HypothesisSpecId",
    "ObservableReference",
]

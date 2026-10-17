"""``feature-provider-v1``: the Git identity of the provider a deck replay ran with (issue #342).

ADR-0069 section 7. A deck replay result that is submitted through K13 carries
the code identity of its ``feature_provider``, so that the result is
reproducible from Git:

.. code-block:: json

    {"identity_domain": "feature-provider-v1", "repository": "...",
     "commit": "<full commit>", "qualified_name": "<module>:<attribute>",
     "source_sha256": "<sha256 of the defining module file at that commit>",
     "parameters": {}}

and its identity is ``feature-provider-v1:sha256:<hex>`` over this payload in the
ADR-0068 ``sorted-compact-ascii-v1`` profile. This module only *computes and
checks* that identity. It is attribution evidence: nothing here, or anywhere on
the server, resolves, imports or executes ``qualified_name`` (the identity of a
provider is not a server-resolved provider reference; ADR-0065 section 5's
deferral of ``J11`` is unaffected).

The output contract of every replay-produced role is :class:`ReplayOutputContractV1`:
the bundle's ``configuration`` must carry ``replay_feature_provider`` as
``{"identity": ..., "payload": ...}`` and the identity must be the one
recomputed from the payload. ``GovernedResultBundleV1``'s fields and digest
rules are unchanged: the value is ordinary ``configuration`` content.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re
from typing import Any, ClassVar

from quant_platform.canonical import canonical_bytes

from .governed_result_import import ResultOutputContractV1

FEATURE_PROVIDER_DOMAIN = "feature-provider-v1"
FEATURE_PROVIDER_IDENTITY_PREFIX = FEATURE_PROVIDER_DOMAIN + ":sha256:"
REPLAY_FEATURE_PROVIDER_KEY = "replay_feature_provider"

# A repository *name*: path-like words only, never a URL or anything that can
# carry a credential (``@``, ``:``, a scheme, a query).
_REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,99}){0,3}")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DOTTED = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
_QUALIFIED_NAME = re.compile(rf"{_DOTTED}:{_DOTTED}")
_IDENTITY = re.compile(r"feature-provider-v1:sha256:[0-9a-f]{64}")
_PAYLOAD_FIELDS = frozenset({
    "identity_domain", "repository", "commit", "qualified_name", "source_sha256", "parameters",
})
MAX_PARAMETERS_BYTES = 16 * 1024


def _canonical(value: Any) -> bytes:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False)


class FeatureProviderIdentityError(ValueError):
    """A provider payload, identity or bundle configuration is not admissible.

    ``reason`` is a stable, wire-safe token.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


def feature_provider_payload(
    *,
    repository: str,
    commit: str,
    qualified_name: str,
    source_sha256: str,
    parameters: Mapping[str, Any] | None = None,
    tree_is_clean: bool,
) -> dict[str, Any]:
    """Build the ADR-0069 section 7 payload for a *clean* tree.

    ``tree_is_clean`` is the caller's statement about the working tree the
    provider was read from. A tree with uncommitted changes cannot produce a
    valid identity, so anything but ``True`` is refused here rather than
    producing a payload whose commit does not describe the code that ran.
    """

    if tree_is_clean is not True:
        raise FeatureProviderIdentityError(
            "dirty_tree", "a tree with uncommitted changes has no feature-provider identity",
        )
    payload = {
        "identity_domain": FEATURE_PROVIDER_DOMAIN,
        "repository": repository,
        "commit": commit,
        "qualified_name": qualified_name,
        "source_sha256": source_sha256,
        "parameters": {} if parameters is None else dict(parameters),
    }
    validate_feature_provider_payload(payload)
    return payload


def validate_feature_provider_payload(payload: Any) -> None:
    """Refuse a payload that is not exactly the ADR-0069 section 7 shape."""

    if type(payload) is not dict or set(payload) != _PAYLOAD_FIELDS:
        raise FeatureProviderIdentityError(
            "payload_shape", "a feature-provider payload has exactly its six fields",
        )
    if payload["identity_domain"] != FEATURE_PROVIDER_DOMAIN:
        raise FeatureProviderIdentityError("payload_domain", "unexpected identity domain")
    for name, pattern in (
        ("repository", _REPOSITORY),
        ("commit", _COMMIT),
        ("qualified_name", _QUALIFIED_NAME),
        ("source_sha256", _SHA256),
    ):
        value = payload[name]
        if type(value) is not str or not pattern.fullmatch(value):
            raise FeatureProviderIdentityError(f"payload_{name}", f"{name} is not admissible")
    parameters = payload["parameters"]
    if type(parameters) is not dict:
        raise FeatureProviderIdentityError("payload_parameters", "parameters must be an object")
    try:
        encoded = _canonical(parameters)
    except (TypeError, ValueError, RecursionError) as error:
        raise FeatureProviderIdentityError(
            "payload_parameters", "parameters must be canonical JSON",
        ) from error
    if len(encoded) > MAX_PARAMETERS_BYTES:
        raise FeatureProviderIdentityError("payload_parameters", "parameters are too large")


def feature_provider_identity(payload: Mapping[str, Any]) -> str:
    """``feature-provider-v1:sha256:<hex>`` over the payload, in ``sorted-compact-ascii-v1``."""

    document = dict(payload) if isinstance(payload, Mapping) else payload
    validate_feature_provider_payload(document)
    digest = hashlib.sha256(_canonical(document)).hexdigest()
    return FEATURE_PROVIDER_IDENTITY_PREFIX + digest


def replay_feature_provider_entry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The value a deck places under ``configuration["replay_feature_provider"]``."""

    return {"identity": feature_provider_identity(payload), "payload": dict(payload)}


def check_replay_feature_provider(configuration: Mapping[str, Any]) -> str:
    """The recomputed identity of a bundle's provider, or a refusal.

    Pure: the payload is hashed, never resolved. Missing key, a malformed entry,
    and an identity that is not the one recomputed from the payload are three
    distinct stable reasons.
    """

    entry = configuration.get(REPLAY_FEATURE_PROVIDER_KEY) if isinstance(configuration, Mapping) else None
    if entry is None:
        raise FeatureProviderIdentityError(
            "provider_missing", "a replay output requires configuration.replay_feature_provider",
        )
    if (
        not isinstance(entry, Mapping) or set(entry) != {"identity", "payload"}
        or not isinstance(entry["identity"], str) or not _IDENTITY.fullmatch(entry["identity"])
        or not isinstance(entry["payload"], Mapping)
    ):
        raise FeatureProviderIdentityError(
            "provider_malformed", "replay_feature_provider is not an identity and a payload",
        )
    recomputed = feature_provider_identity(entry["payload"])
    if recomputed != entry["identity"]:
        raise FeatureProviderIdentityError(
            "provider_identity_mismatch", "the identity is not the one of the payload",
        )
    return recomputed


@dataclass(frozen=True, slots=True)
class ReplayOutputContractV1(ResultOutputContractV1):
    """A replay-produced output role: the provider identity is part of its contract.

    It is a :class:`ResultOutputContractV1` for every K13 check; what it adds is
    the section 7 requirement, applied before registration by the composition
    that holds it (``j02-result-import-v1``).
    """

    configuration_key: ClassVar[str] = REPLAY_FEATURE_PROVIDER_KEY

    def check(self, configuration: Mapping[str, Any]) -> str:
        return check_replay_feature_provider(configuration)


__all__ = [
    "FEATURE_PROVIDER_DOMAIN",
    "FEATURE_PROVIDER_IDENTITY_PREFIX",
    "FeatureProviderIdentityError",
    "MAX_PARAMETERS_BYTES",
    "REPLAY_FEATURE_PROVIDER_KEY",
    "ReplayOutputContractV1",
    "check_replay_feature_provider",
    "feature_provider_identity",
    "feature_provider_payload",
    "replay_feature_provider_entry",
    "validate_feature_provider_payload",
]

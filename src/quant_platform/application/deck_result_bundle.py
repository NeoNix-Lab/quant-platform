"""The deck workload's producer of the K13 result bundle (issue #394, ADR-0072 section 5).

A deck workload (Omega, a notebook) runs the platform library in process over the bytes the
protocol client delivered (ADR-0069 section 8). This module turns what it produced into what the
client submits: the upload bytes of each output and the ``GovernedResultBundleV1`` wire document.

The protocol client (``clients/deck/``) imports nothing of ``quant_platform``, so this module takes
the verified admission as plain data and imports no client, opens no connection and sends nothing:
the upload and submit calls stay the client's.

v1 pins the deck to the server's commit (ADR-0072 section 5): the manifest's ``git_identity`` is the
commit the API recorded at startup, and the workload must run exactly that commit from a clean tree.
The ``RunIdentity`` carries it. It covers the manifest's logical inputs itself, and requires the
workload to *declare* the manifest's definitions (ADR-0073 section 6): a definition identity the
admission carries and the run does not name is ``definition_not_declared``, because the workload, not
the builder, states which definitions it evaluated. Both are the K13 compatibility checks
(``GovernedResultImportService``); everything else a run declares (study, trial, extra identities,
configuration) is the workload's own and is not decided here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
import re
import subprocess
from types import MappingProxyType
from typing import Any

from quant_platform.experiments.identities import (
    ArtifactContentIdentity,
    ArtifactIdentity,
    IdentityReference,
    RunIdentity,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)
from quant_platform.experiments.persistence import ArtifactRegistration

from .feature_provider_identity import (
    REPLAY_FEATURE_PROVIDER_KEY,
    feature_provider_payload,
    replay_feature_provider_entry,
)
from .governed_result_import import GovernedResultBundleV1, ResultOutputEvidenceV1
from .result_import_transport import _manifest_from_wire, bundle_to_wire

# The closed registry of ADR-0072 section 2: role -> (artifact kind, artifact schema version). These
# are the ADR's constants; the server's copy lives in ``result_import_composition`` and a test holds
# the two equal. This module does not import that factory.
DECK_OUTPUT_ROLES_V1: Mapping[str, tuple[str, str]] = MappingProxyType({
    "normalizer": ("supervised-normalizer", "supervised-normalizer-v1"),
    "model": ("supervised-model", "supervised-model-v1"),
    "predictions": ("supervised-predictions", "supervised-predictions-v1"),
    "metrics": ("supervised-metrics", "supervised-metrics-v1"),
    "replay-result": ("replay-result", "replay-result-v1"),
    "replay-summary": ("replay-summary", "replay-summary-v1"),
})
REPLAY_ROLES_V1: frozenset[str] = frozenset({"replay-result", "replay-summary"})
LOGICAL_INPUT_REFERENCE_KIND = "admitted-input"
# ADR-0073 section 6. K13 compares identity strings, not kinds: the kind is for readers and tooling.
FEATURE_DEFINITION_REFERENCE_KIND = "feature-definition"
REPRESENTATION_DEFINITION_REFERENCE_KIND = "representation-definition"
_DEFINITION_REFERENCE_KINDS = (
    ("feature-definition-v1:", FEATURE_DEFINITION_REFERENCE_KIND),
    ("bar-flow-definition-v1:", REPRESENTATION_DEFINITION_REFERENCE_KIND),
)
_GIT_TIMEOUT_SECONDS = 30
_COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class DeckResultError(ValueError):
    """A deck result cannot be built; ``reason`` is a stable token."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class WorkloadTree:
    """What the workload runs from: its commit and whether the tree is clean."""

    commit: str
    is_clean: bool


def inspect_workload_tree(root: str | Path) -> WorkloadTree:
    """The commit and cleanliness of the Git work tree at ``root``; any doubt is a refusal.

    Clean means ``git status --porcelain`` prints nothing at all: a modified or untracked file is
    code the commit does not describe. The answer is about ``root`` itself and one moment: Git runs
    without the ``GIT_*`` selectors of the environment (which could point it at another repository),
    ``root`` must be the work tree's top level, and ``HEAD`` must be the same commit before, during
    and after the status read, so a checkout in between is a refusal, not a mixed answer.
    """

    environment = {name: value for name, value in os.environ.items() if not name.upper().startswith("GIT_")}

    def git(*arguments: str) -> str:
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", *arguments], cwd=root, capture_output=True, text=True,
                check=False, timeout=_GIT_TIMEOUT_SECONDS, env=environment,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DeckResultError("tree_unavailable", "the workload tree could not be inspected") from error
        if result.returncode != 0:
            raise DeckResultError("tree_unavailable", "the workload tree is not a readable Git work tree")
        return result.stdout

    try:
        top_level, before = git("rev-parse", "--show-toplevel", "HEAD").split("\n")[:2]
        same_root = os.path.samefile(top_level, root)
    except (ValueError, OSError) as error:
        raise DeckResultError("tree_unavailable", "the workload tree could not be inspected") from error
    if not same_root:
        raise DeckResultError("tree_unavailable", "the workload root is not the top level of a Git work tree")
    status = git("status", "--porcelain=v2", "--branch", "--untracked-files=normal", "--ignore-submodules=none")
    after = git("rev-parse", "HEAD").strip()
    during = next((line[len("# branch.oid "):] for line in status.splitlines() if line.startswith("# branch.oid ")), "")
    if not (_COMMIT.fullmatch(before) and before == during == after):
        raise DeckResultError("tree_changed", "the workload tree moved while it was inspected, or has no commit")
    is_clean = all(line.startswith("# ") for line in status.splitlines())
    return WorkloadTree(commit=before, is_clean=is_clean)


@dataclass(frozen=True, slots=True)
class DeckAdmission:
    """The verified admission, as the client holds it: identity, digest and the sealed manifest."""

    admission_id: str
    manifest_digest: str
    manifest: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class DeckOutput:
    """One output of the workload: a role of the registry and the bytes to upload."""

    role: str
    payload: bytes
    locator: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FeatureProviderSource:
    """The provider a replay ran with (ADR-0069 section 7); the commit is the workload's, not declared."""

    repository: str
    qualified_name: str
    source_sha256: str
    parameters: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DeckRunDeclaration:
    """What the workload itself declares about its run; the manifest's coverage is added to it."""

    study: StudyIdentity
    execution_id: str
    trial_assignments: Mapping[str, Any] = field(default_factory=dict)
    data_identities: Sequence[IdentityReference] = ()
    feature_identities: Sequence[IdentityReference] = ()
    research_label_identities: Sequence[IdentityReference] = ()
    strategy_policy_execution_identities: Sequence[IdentityReference] = ()
    validation_identity: IdentityReference | None = None
    environment_identity: IdentityReference | None = None
    run_configuration: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class DeckUpload:
    """The bytes the client uploads for one output, with the checksum the bundle declares for them."""

    role: str
    content_checksum: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class DeckResult:
    """The bundle, the document the client submits, and the uploads that precede it."""

    bundle: GovernedResultBundleV1
    wire: dict[str, Any]
    uploads: tuple[DeckUpload, ...]


def definition_reference(identity: str) -> IdentityReference:
    """The reference a workload declares for a definition identity the server admitted.

    A ``feature-definition-v1`` identity is a ``feature-definition`` and a ``bar-flow-definition-v1``
    identity a ``representation-definition`` (ADR-0073 section 6); any other identity has no kind
    this module knows.
    """

    for prefix, kind in _DEFINITION_REFERENCE_KINDS:
        if type(identity) is str and identity.startswith(prefix):
            return IdentityReference(kind, identity)
    raise DeckResultError("definition_kind_unknown", "the identity is not a feature or representation definition")


def content_checksum(payload: bytes) -> str:
    """The K13 checksum of uploaded bytes: ``sha256:<hex>``."""

    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_deck_result(
    admission: DeckAdmission,
    tree: WorkloadTree,
    run: DeckRunDeclaration,
    outputs: Sequence[DeckOutput],
    *,
    runtime_environment: Mapping[str, Any],
    configuration: Mapping[str, Any] | None = None,
    seeds: Mapping[str, Any] | None = None,
    replay_provider: FeatureProviderSource | None = None,
    metrics_evidence: Mapping[str, Any] | None = None,
    failure_evidence: Mapping[str, Any] | None = None,
) -> DeckResult:
    """Build the bundle and its wire document, or refuse with a :class:`DeckResultError`.

    Nothing is built for a tree that is dirty or not at the manifest's commit.
    """

    manifest = _admitted_manifest(admission)
    code_identity = manifest.git_identity
    if not code_identity:
        raise DeckResultError("manifest_without_code", "the admission carries no code identity to pin to")
    if not isinstance(tree, WorkloadTree) or tree.is_clean is not True:
        raise DeckResultError("dirty_tree", "the workload tree has uncommitted changes")
    if tree.commit != code_identity:
        raise DeckResultError("commit_mismatch", "the workload does not run the commit the server runs")

    declared = _declared_outputs(outputs)
    replay = any(role in REPLAY_ROLES_V1 for role in declared)
    if replay and replay_provider is None:
        raise DeckResultError("provider_missing", "a replay output needs the feature provider it ran with")
    if not replay and replay_provider is not None:
        raise DeckResultError("provider_without_replay", "a feature provider is only declared for replay outputs")

    bundle_configuration = dict(configuration) if configuration is not None else {}
    if REPLAY_FEATURE_PROVIDER_KEY in bundle_configuration:
        raise DeckResultError("provider_preset", f"{REPLAY_FEATURE_PROVIDER_KEY} is set by the builder only")
    if replay_provider is not None:
        bundle_configuration[REPLAY_FEATURE_PROVIDER_KEY] = replay_feature_provider_entry(feature_provider_payload(
            repository=replay_provider.repository, commit=tree.commit,
            qualified_name=replay_provider.qualified_name, source_sha256=replay_provider.source_sha256,
            parameters=replay_provider.parameters, tree_is_clean=tree.is_clean,
        ))

    identity = _run_identity(run, manifest.logical_input_identities, manifest.definition_identities, code_identity)
    evidence: list[ResultOutputEvidenceV1] = []
    uploads: list[DeckUpload] = []
    for role, output in declared.items():
        kind, schema = DECK_OUTPUT_ROLES_V1[role]
        checksum = content_checksum(output.payload)
        evidence.append(ResultOutputEvidenceV1(
            ArtifactRegistration(
                ArtifactIdentity(identity, role, ArtifactContentIdentity(kind, schema, checksum)),
                None if output.locator is None else dict(output.locator),
            ),
            checksum,
        ))
        uploads.append(DeckUpload(role, checksum, output.payload))

    bundle = GovernedResultBundleV1(
        admission_id=manifest.admission_id,
        manifest_digest=manifest.manifest_digest,
        consumed_manifest=manifest,
        deck_code_identity=code_identity,
        runtime_environment=runtime_environment,
        configuration=bundle_configuration,
        seeds=seeds,
        run_identity=identity,
        outputs=tuple(evidence),
        metrics_evidence=metrics_evidence,
        failure_evidence=failure_evidence,
    )
    wire = bundle_to_wire(bundle)
    _require_transportable(wire, "wire")
    return DeckResult(bundle, wire, tuple(uploads))


def _require_transportable(value: Any, path: str) -> None:
    """Refuse what the ``submit`` surface cannot carry as lossless JSON, naming the field.

    The J02 listener rejects a request whose text holds a number with a fractional part or an exponent,
    NaN or Infinity, before any decoder runs. A float is not turned into text here: that would change the
    value an identity or fingerprint is computed over, so the workload must declare it another way.
    """

    kind = type(value)
    if value is None or kind in (str, bool, int):
        return
    if kind is list:
        for index, item in enumerate(value):
            _require_transportable(item, f"{path}[{index}]")
        return
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise DeckResultError("not_transportable", f"{path}: a key is not text")
            _require_transportable(item, f"{path}.{key}")
        return
    if kind is float:
        raise DeckResultError("not_transportable", f"{path}: a float has no lossless wire form")
    raise DeckResultError("not_transportable", f"{path}: a {kind.__name__} has no wire form")


def _admitted_manifest(admission: DeckAdmission):
    if not isinstance(admission, DeckAdmission):
        raise TypeError("admission must be a DeckAdmission")
    try:
        manifest = _manifest_from_wire(admission.manifest)
    except (TypeError, ValueError) as error:
        raise DeckResultError("manifest_malformed", "the admission's manifest is not a sealed v1 manifest") from error
    if manifest.manifest_digest != admission.manifest_digest or manifest.admission_id != admission.admission_id:
        raise DeckResultError("manifest_digest_mismatch", "the manifest is not the one the admission names")
    return manifest


def _declared_outputs(outputs: Sequence[DeckOutput]) -> dict[str, DeckOutput]:
    declared: dict[str, DeckOutput] = {}
    for output in outputs:
        if not isinstance(output, DeckOutput) or not isinstance(output.payload, bytes) or not output.payload:
            raise DeckResultError("output_malformed", "an output is a role and non-empty bytes")
        if output.role not in DECK_OUTPUT_ROLES_V1:
            raise DeckResultError("role_unknown", "an output role is outside the closed registry")
        if output.role in declared:
            raise DeckResultError("role_duplicate", "an output role is declared once")
        declared[output.role] = output
    if not declared:
        raise DeckResultError("outputs_empty", "a result has at least one output")
    return declared


def _run_identity(
    run: DeckRunDeclaration,
    logical_inputs: Sequence[str],
    definitions: Sequence[str],
    code_identity: str,
) -> RunIdentity:
    if not isinstance(run, DeckRunDeclaration):
        raise TypeError("run must be a DeckRunDeclaration")
    return RunIdentity(
        RunSpecIdentity(
            TrialIdentity(run.study, dict(run.trial_assignments)),
            IdentityReference("code", code_identity),
            data_identities=_covering(run.data_identities, logical_inputs, LOGICAL_INPUT_REFERENCE_KIND),
            feature_identities=_declared(run.feature_identities, definitions),
            research_label_identities=tuple(run.research_label_identities),
            validation_identity=run.validation_identity,
            strategy_policy_execution_identities=tuple(run.strategy_policy_execution_identities),
            environment_identity=run.environment_identity,
            run_configuration={} if run.run_configuration is None else dict(run.run_configuration),
        ),
        run.execution_id,
    )


def _declared(
    declared: Sequence[IdentityReference], required: Sequence[str],
) -> tuple[IdentityReference, ...]:
    """The workload's own references, which must name every definition the admission carries.

    Nothing is added: a definition the workload did not declare is a refusal, so the run says which
    definitions it evaluated. A run may declare more than the admission carried (K13 verifies
    coverage, not exclusivity); only the admitted ones are server-attested.
    """

    references = tuple(declared)
    present = {reference.identity for reference in references}
    missing = sorted(identity for identity in required if identity not in present)
    if missing:
        raise DeckResultError(
            "definition_not_declared",
            "the workload did not declare the admitted definition(s): " + ", ".join(missing),
        )
    return references


def _covering(
    declared: Sequence[IdentityReference], required: Sequence[str], kind: str,
) -> tuple[IdentityReference, ...]:
    """The workload's own references, plus one for every required identity it did not already name."""

    references = tuple(declared)
    present = {reference.identity for reference in references}
    return references + tuple(IdentityReference(kind, identity) for identity in required if identity not in present)


__all__ = [
    "DECK_OUTPUT_ROLES_V1",
    "FEATURE_DEFINITION_REFERENCE_KIND",
    "DeckAdmission",
    "DeckOutput",
    "DeckResult",
    "DeckResultError",
    "DeckRunDeclaration",
    "DeckUpload",
    "FeatureProviderSource",
    "LOGICAL_INPUT_REFERENCE_KIND",
    "REPLAY_ROLES_V1",
    "REPRESENTATION_DEFINITION_REFERENCE_KIND",
    "WorkloadTree",
    "build_deck_result",
    "content_checksum",
    "definition_reference",
    "inspect_workload_tree",
]

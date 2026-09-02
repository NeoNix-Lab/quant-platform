"""Generic declared-coverage reconstruction for publication certification.

The reconstruction is deliberately document-only.  It never infers coverage
from records, filenames, partition keys, or Parquet statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .models import Instant


@dataclass(frozen=True, slots=True)
class CoverageViolation:
    code: str
    message: str
    where: str = ""


_ELIGIBLE_STATES = frozenset({"closed", "valid", "degraded"})
_CONTRADICTORY_EVIDENCE = frozenset({"transport_interruption", "sequence_discontinuity"})


def reconstruct_catalog_coverage(
    coverage_manifests: Iterable[Mapping[str, Any]],
    partition_manifests: Iterable[Mapping[str, Any]],
) -> tuple[dict[tuple[str, int], tuple[Instant, Instant]], tuple[CoverageViolation, ...]]:
    """Fold complete live coverage to one interval per natural partition.

    The algorithm is order-independent and fail-closed for mixed identities,
    invalid supersession graphs, duplicate partition references, live revision
    conflicts, contradictions, gaps, and observed bounds outside the final
    half-open interval.
    """

    coverage = tuple(coverage_manifests)
    partitions = tuple(partition_manifests)
    violations: list[CoverageViolation] = []
    identities = {_natural_identity(item) for item in coverage + partitions}
    if len(identities) > 1:
        return {}, (CoverageViolation(
            "RECONSTRUCTION_DATASET_MIXED",
            "coverage and partition manifests must share one DatasetIdentity",
        ),)

    by_ref: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for item in partitions:
        ref = (item.get("partition_key"), item.get("revision"))
        by_ref.setdefault(ref, []).append(item)
    unpublishable_keys: set[str] = set()
    for ref, matches in sorted(by_ref.items(), key=lambda item: str(item[0])):
        if len(matches) > 1:
            violations.append(CoverageViolation(
                "PARTITION_MANIFEST_DUPLICATE",
                f"multiple partition manifests resolve to {ref!r}",
                f"{ref[0]}@rev{ref[1]}",
            ))
            unpublishable_keys.add(ref[0])
    by_key: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for (key, revision), matches in by_ref.items():
        if matches:
            by_key.setdefault(key, []).append((revision, matches[0]))
    for key, revisions in sorted(by_key.items(), key=lambda item: str(item[0])):
        live = sorted(revision for revision, item in revisions if item.get("state") != "superseded")
        if len(live) > 1:
            violations.append(CoverageViolation(
                "PARTITION_LIVE_REVISION_CONFLICT",
                f"multiple non-superseded revisions for {key!r}: {live}",
                key,
            ))
            unpublishable_keys.add(key)

    live, graph_violations = _live_coverage_documents(coverage)
    violations.extend(graph_violations)
    if live is None:
        return {}, tuple(violations)

    attributed: dict[tuple[str, int], list[tuple[Instant, Instant]]] = {}
    non_complete: list[tuple[Instant, Instant, str | None]] = []
    for document in live:
        for assertion in document.get("assertions") or ():
            start, end = _interval(assertion)
            if start is None or end is None or start >= end:
                continue
            if assertion.get("status") != "complete":
                non_complete.append((start, end, document.get("coverage_id")))
                continue
            for ref in assertion.get("partitions") or ():
                key = (ref.get("partition_key"), ref.get("revision"))
                attributed.setdefault(key, []).append((start, end))

    result: dict[tuple[str, int], tuple[Instant, Instant]] = {}
    unpublishable_refs: set[tuple[str, int]] = set()
    for key, intervals in sorted(attributed.items(), key=lambda item: str(item[0])):
        where = f"{key[0]}@rev{key[1]}"
        if key[0] in unpublishable_keys:
            violations.append(CoverageViolation(
                "PARTITION_NOT_PUBLISHABLE",
                f"partition family {key[0]!r} is ambiguous because its manifests contain duplicate or live-conflicting revisions",
                where,
            ))
            unpublishable_refs.add(key)
            continue
        matches = by_ref.get(key, ())
        if len(matches) != 1:
            violations.append(CoverageViolation(
                "PARTITION_UNKNOWN",
                f"complete coverage does not resolve to exactly one partition manifest: {key!r}",
                where,
            ))
            unpublishable_refs.add(key)
            continue
        partition = matches[0]
        if partition.get("state") not in _ELIGIBLE_STATES:
            violations.append(CoverageViolation(
                "PARTITION_NOT_ELIGIBLE",
                f"partition state {partition.get('state')!r} cannot receive complete coverage",
                where,
            ))
            unpublishable_refs.add(key)
            continue
        merged = _merge(intervals)
        if len(merged) != 1:
            violations.append(CoverageViolation(
                "COVERAGE_NOT_CONTIGUOUS",
                f"complete coverage for {where} is not one contiguous interval",
                where,
            ))
            unpublishable_refs.add(key)
            continue
        start, end = merged[0]
        contradiction = any(_overlaps(start, end, a, b) for a, b, _ in non_complete)
        if contradiction:
            violations.append(CoverageViolation(
                "COVERAGE_CONTRADICTION",
                f"complete coverage for {where} contradicts a non-complete assertion",
                where,
            ))
            unpublishable_refs.add(key)
            continue
        result[key] = (start, end)

    for key, matches in sorted(by_ref.items(), key=lambda item: str(item[0])):
        if key in unpublishable_refs or key[0] in unpublishable_keys:
            continue
        if key not in result:
            if matches[0].get("state") in _ELIGIBLE_STATES:
                violations.append(CoverageViolation(
                    "COVERAGE_MISSING_FOR_PARTITION",
                    f"no complete coverage assertion resolves to {key!r}",
                    f"{key[0]}@rev{key[1]}",
                ))
            continue
        partition = matches[0]
        if not partition.get("row_count"):
            continue
        start, end = result[key]
        first = _parse_timestamp(partition.get("first_exchange_ts"))
        last = _parse_timestamp(partition.get("last_exchange_ts"))
        if first is not None and first < start:
            violations.append(CoverageViolation("OBSERVED_OUTSIDE_DECLARED", "first observed record is outside declared coverage", f"{key[0]}@rev{key[1]}"))
        if last is not None and last >= end:
            violations.append(CoverageViolation("OBSERVED_OUTSIDE_DECLARED", "last observed record is outside half-open declared coverage", f"{key[0]}@rev{key[1]}"))
    return result, tuple(violations)


def _live_coverage_documents(
    documents: tuple[Mapping[str, Any], ...],
) -> tuple[list[Mapping[str, Any]] | None, list[CoverageViolation]]:
    violations: list[CoverageViolation] = []
    by_id: dict[Any, Mapping[str, Any]] = {}
    duplicates: set[Any] = set()
    for document in documents:
        identifier = document.get("coverage_id")
        if identifier in by_id:
            duplicates.add(identifier)
        else:
            by_id[identifier] = document
    for identifier in sorted(duplicates, key=str):
        violations.append(CoverageViolation("COVERAGE_ID_DUPLICATE", f"duplicate coverage_id {identifier!r}", str(identifier)))
        by_id.pop(identifier, None)

    for identifier, document in sorted(by_id.items(), key=lambda item: str(item[0])):
        spans: list[tuple[Instant, Instant]] = []
        for assertion in document.get("assertions") or ():
            start, end = _interval(assertion)
            if start is None or end is None:
                continue
            if any(_overlaps(start, end, old_start, old_end) for old_start, old_end in spans):
                violations.append(CoverageViolation(
                    "COVERAGE_ASSERTION_OVERLAP",
                    f"coverage assertions overlap in {identifier!r}",
                    str(identifier),
                ))
            spans.append((start, end))

    edges: dict[Any, Any] = {}
    reverse: dict[Any, list[Any]] = {}
    for identifier, document in sorted(by_id.items(), key=lambda item: str(item[0])):
        target = document.get("supersedes")
        if target == identifier:
            violations.append(CoverageViolation("COVERAGE_SUPERSESSION_SELF", f"coverage {identifier!r} supersedes itself", str(identifier)))
        elif target is not None:
            if target not in by_id:
                violations.append(CoverageViolation("COVERAGE_SUPERSEDES_UNKNOWN", f"coverage {identifier!r} supersedes an unavailable document", str(identifier)))
            else:
                edges[identifier] = target
                reverse.setdefault(target, []).append(identifier)
    for target, supersessors in sorted(reverse.items(), key=lambda item: str(item[0])):
        if len(supersessors) > 1:
            violations.append(CoverageViolation("COVERAGE_SUPERSESSION_CONFLICT", f"multiple documents supersede {target!r}", str(target)))

    cycle_members: set[Any] = set()
    for start in sorted(edges, key=str):
        path: list[Any] = []
        current = start
        while current in edges:
            if current in path:
                cycle_members.update(path[path.index(current):])
                break
            path.append(current)
            current = edges[current]
    if cycle_members:
        violations.append(CoverageViolation("COVERAGE_SUPERSESSION_CYCLE", "coverage supersession graph contains a cycle"))

    structural = {"COVERAGE_ID_DUPLICATE", "COVERAGE_ASSERTION_OVERLAP", "COVERAGE_SUPERSESSION_SELF", "COVERAGE_SUPERSEDES_UNKNOWN", "COVERAGE_SUPERSESSION_CONFLICT", "COVERAGE_SUPERSESSION_CYCLE"}
    if any(item.code in structural for item in violations):
        return None, violations

    narrowing: set[Any] = set()
    for identifier, target in edges.items():
        old = _interval(by_id[target].get("acquisition") or {}, "intent_start", "intent_end")
        new = _interval(by_id[identifier].get("acquisition") or {}, "intent_start", "intent_end")
        if None not in old + new and (new[0] > old[0] or new[1] < old[1]):
            violations.append(CoverageViolation("COVERAGE_SUPERSESSION_NARROWS", "supersession narrows the replaced acquisition domain", str(identifier)))
            narrowing.update({identifier, target})
    superseded = set(edges.values())
    live_ids = sorted((set(by_id) - superseded) - narrowing, key=str)
    return [by_id[item] for item in live_ids], violations


def _natural_identity(document: Mapping[str, Any]) -> tuple[Any, ...]:
    keys = ("layer", "dataset_kind", "venue", "instrument", "record_schema_id")
    value = tuple(document.get(key) for key in keys)
    if document.get("layer") == "features":
        value += (document.get("feature_set_slug"), document.get("feature_set_version"))
    return value


def _parse_timestamp(value: Any) -> Instant | None:
    return None if value is None else Instant.parse(value)


def _interval(document: Mapping[str, Any], start_key: str = "start", end_key: str = "end") -> tuple[Instant | None, Instant | None]:
    return _parse_timestamp(document.get(start_key)), _parse_timestamp(document.get(end_key))


def _overlaps(a_start: Instant, a_end: Instant, b_start: Instant, b_end: Instant) -> bool:
    return a_start < b_end and b_start < a_end


def _merge(intervals: Iterable[tuple[Instant, Instant]]) -> list[tuple[Instant, Instant]]:
    ordered = sorted(intervals, key=lambda item: (item[0].epoch_ns, item[1].epoch_ns))
    merged: list[tuple[Instant, Instant]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


__all__ = ["CoverageViolation", "reconstruct_catalog_coverage"]

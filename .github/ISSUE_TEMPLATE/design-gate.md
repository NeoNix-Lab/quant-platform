---
name: "[agent] Design gate"
about: Decision-gate design issue. Produces an ADR or design note only — no code.
title: "[agent] <DG-ID> — Design <capability> v1 (<ADR-00XX>, <atom>)"
labels: []
assignees: ''
---

## Agent-ready mandate

Derived from `SCOPE.md` / **<macro-scope name>**.

Active Path step: **<N> — <step name>**.

This is a design/governance issue, not an implementation issue.

Blocked by: <#issue, or "None — no prerequisite step">.

<If not blocked, state explicitly whether this design gate may run
concurrently with another open design gate, or whether the repository's
one-bounded-mutation-slice-at-a-time rule requires picking one first.>

## Objective

<One paragraph: what decision must be resolved, in what direction it is
allowed to go (e.g. "either a proven source/path, or an explicit retained
default — both are valid closes"), and why it blocks a dependent atom.>

## Authority

Start from:

- `SCOPE.md` — <macro-scope name>, relevant P-findings/section
- `docs/product/CAPABILITY_DAG.md` — <relevant atom rows / derived governance map, if one exists>
- <ADR-00XX, ADR-00YY, ...>
- <any prior disposed issue/decision this must not reopen, e.g. "issue #110's NO_AUTHORITATIVE_REPAIR_PATH_PROVEN disposition">

## Required output

A committed design note or ADR, per repository convention, that defines:

- <bullet: the first concrete sub-decision>
- <bullet: the second concrete sub-decision>
- <bullet: ...>
- why this does not require <the scope's specific generic-framework exclusions>

## Acceptance

- The design is specific enough that the implementation issue can proceed
  without further semantic decisions.
- <capability-specific criterion>
- <capability-specific criterion>
- Markdown/link checks pass (`python tools/check_markdown_links.py`).

## Stop conditions

Stop and report — do not implement — if <the scope-specific conditions that
require escalation instead of a decision, e.g. "no source can prove exact
interval completeness" or "the design would require weakening an accepted
contract">. A negative/deferred result under these conditions is a valid
close of this gate, not a failure.

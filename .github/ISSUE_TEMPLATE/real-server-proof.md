---
name: "[agent] Real-server / E2E proof"
about: Bounded real-environment, golden E2E, or supervised-deployment proof issue. Produces evidence, not new semantics.
title: "[agent] <ID> — Execute <proof name>"
labels: []
assignees: ''
---

## Agent-ready mandate

Derived from `SCOPE.md` / **<macro-scope name>**.

Active Path step: **<N> — <step name>**.

Blocked by: #<implementation issue(s)>.

Do not start until the blocking implementation slice(s) are merged or
explicitly accepted as sufficient for proof.

## Setup checklist

- [ ] Add this issue to the **<scope> Milestone** — the same one as the epic
      and its siblings. That is how this repo tracks "belongs to this scope,"
      not a label.
- [ ] Link this issue as a native GitHub **sub-issue** of epic #<epic issue>
      (Development panel → Sub-issues), in addition to the `Blocked by`
      field above — the two are complementary, neither replaces the other.

## Objective

Execute and record <the proof> on <target environment — real server, homelab
target, golden historical dataset, etc.>. This issue proves existing
behavior; it does not introduce new ingest/execution semantics, and any bug
found during the proof gets its own separate fix issue/PR rather than being
patched silently inside the evidence record.

## Authority

- #<implementation issue> output
- `SCOPE.md` — Active Path step <N> and Acceptance items <list the exact item numbers this proof closes>
- <ADRs / identity constraints this proof must respect, e.g. K02 least-privilege identity>

## Required proof

Run on <target>:

- <step 1, e.g. "deploy/run bounded server">
- <step 2, e.g. "stop/restart">
- <step 3, e.g. "bounded reconcile">
- <step 4, e.g. "forced/simulated failure scenario">
- Record material evidence paths, identities, commands and outputs.

## Acceptance

- <criterion 1 — reference the exact SCOPE.md acceptance item(s) this satisfies>
- <criterion 2>
- Evidence is committed or linked according to repository convention
  **without** adding secrets or bulk market data.

## Stop conditions

Stop and report — do not silently patch around it — if the real environment
requires unauthorized ACL/role changes, secrets exposure, broader
authority than the governing ADR permits, or reveals a genuine code defect
(file it separately rather than working around it here).

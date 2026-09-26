---
name: "[agent] Implementation slice"
about: Bounded implementation issue for one design-gated slice of work.
title: "[agent] <ID> — Implement <capability> v1"
labels: []
assignees: ''
---

## Agent-ready mandate

Derived from `SCOPE.md` / **<macro-scope name>**.

Active Path step: **<N> — <step name>**.

Blocked by: #<design-gate issue number(s)>.

Do not start until #<N> is closed or explicitly accepted as sufficient
authority. If this issue is not actually blocked by anything, say so
explicitly ("Blocked by: none") rather than leaving it implicit.

## Setup checklist

- [ ] Add this issue to the **<scope> Milestone** — the same one as the epic
      and its siblings. That is how this repo tracks "belongs to this scope,"
      not a label.
- [ ] Link this issue as a native GitHub **sub-issue** of epic #<epic issue>
      (Development panel → Sub-issues), in addition to the `Blocked by`
      field above — the two are complementary, neither replaces the other.

## Objective

<One paragraph: what this slice implements and the exact bounded output it
must produce — reuse the design gate's own required-output list rather than
restating the whole scope's objective.>

## Authority

Start from:

- #<design-gate issue> design output (ADR-00XX)
- `SCOPE.md` — relevant P-findings/section and Active Path step <N>
- <ADR-00XX, ...>
- <existing implementation/proof entrypoints this slice must reuse rather than reimplement>

## Expected artifact

Code and tests for <capability>, plus any runbook/operator documentation
named by #<design-gate issue>. <State explicitly what must NOT be
reimplemented — the functions/modules already credited complete that this
slice composes instead.>

## Acceptance

- <criterion 1, tailored to this slice specifically, not the whole scope's global acceptance list>
- <criterion 2>
- <criterion 3>
- Targeted tests pass.
- Repository-required verification passes on the stable candidate
  (`python -m compileall -q src tests tools`, `python tools/check_markdown_links.py`, `git diff --check`).

## Stop conditions

Stop and report if implementation requires <the scope's specific excluded
capabilities/generic frameworks — copy verbatim from SCOPE.md's Out of Scope
section so this issue does not silently narrow or widen it>.

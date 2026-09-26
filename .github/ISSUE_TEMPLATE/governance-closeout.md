---
name: "[governance] Closeout / reconciliation"
about: Governance-only reconciliation or scope closeout. Touches only governance/planning files — no code.
title: "[governance] <Scope name> closeout and governance reconciliation"
labels: []
assignees: ''
---

## Governance-only mandate

This issue is explicitly designated as governance-only / governance-reconciliation
work per `AGENTS.md`'s governance boundary. It — and only it, or another issue
carrying this same explicit designation — may update `SCOPE.md`,
`docs/product/ROADMAP.md`, `docs/product/CAPABILITY_MAP.md`,
`docs/product/CAPABILITY_DAG.md`, and `docs/architecture/OPEN_DECISIONS.md`.
Ordinary implementation issues must leave those files untouched and report
staleness findings here instead of editing them directly.

Blocked by: #<final proof/implementation issue that this closeout credits>.

## Setup checklist

- [ ] Add this issue to the **<scope> Milestone** — the same one as the epic
      and its siblings.
- [ ] Link this issue as a native GitHub **sub-issue** of epic #<epic issue>
      (Development panel → Sub-issues), in addition to the `Blocked by`
      field above.
- [ ] Closing this issue is also when the **Milestone itself gets closed** —
      this is the last issue in the scope, not just another sibling.

## Objective

Reconcile governance to the strongest propositions actually proven by
**<scope name>**, and archive the scope per the normal versioned-scope
convention.

## Required output

- Update `CAPABILITY_MAP.md` / `CAPABILITY_DAG.md` / `ROADMAP.md` /
  `OPEN_DECISIONS.md` to record <atom(s)/milestone> as COMPLETE, citing the
  merged evidence (PR #s) that proves it.
- Flip `SCOPE.md`'s Status to CLOSED (or archive it per convention) with a
  closeout-result note pointing at the evidence.
- Do **not** declare complete — by implication or omission — anything this
  scope's Out of Scope section explicitly excluded.

## Acceptance

- Governance files reflect only propositions actually proven by merged
  evidence, not aspirational/target state.
- No excluded capability is marked complete by implication.
- Markdown/link checks pass (`python tools/check_markdown_links.py`).

## Stop conditions

Stop and report rather than reconcile if closing out would require claiming
an unproven capability complete, or if governance documents conflict with
accepted ADRs/contracts in a way this issue cannot resolve without a new
decision.

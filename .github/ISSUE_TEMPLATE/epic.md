---
name: "[Epic] Macro-scope tracking"
about: Parent tracking issue for a macro-scope (Wave/vertical). Links its child issues; does not itself carry an Active Path.
title: "[Epic] <Wave/scope name>"
labels: []
assignees: ''
---

## Scope

Parent tracking epic for the **<macro-scope name>** (Vertical Milestone <VN>).

<One paragraph: what this scope builds, why, and the one-line pipeline it establishes (see the scope's own SCOPE.md Objective for the exact wording).>

Governing authority:

- `<branch>:SCOPE.md`
- <ADR-00XX, ...>
- `docs/product/CAPABILITY_DAG.md` (Atoms <list>)
- Decision Gate <DG-X> (if any)

## Setup checklist

Classic issue templates can't preset these — GitHub fields to set by hand
right after filing, mirroring how Milestone #4 (Live Ingest Server Production
Readiness v1) and #5 (Wave 4) were actually run:

- [ ] Create (or reuse) a **Milestone** named after this scope, e.g. exactly
      this issue's title minus the `[Epic] ` prefix, and add this epic to it.
      Every child issue below must be added to the *same* Milestone — that is
      the mechanism this repo actually uses to scope "which issues belong to
      this Wave," not a label.
- [ ] Link each child issue as a native GitHub **sub-issue** of this epic
      (Development panel → Sub-issues → Add sub-issue), not only as a text
      mention. The list below is for Active-Path *order* and *blocking*
      semantics GitHub's sub-issue list doesn't encode — it complements the
      native link, it doesn't replace it.
- [ ] Issue Type (Task/Bug/Feature): not used consistently on any existing
      issue in this repo yet (checked: all `null`). Leave unset unless you are
      deliberately adopting it for this scope.

## Child issues (Active Path order)

List child issues in the same order as `SCOPE.md`'s Active Path. This list is
for human/epic navigation and blocking context only — it is **not** a
substitute for the native sub-issue link above, nor for each child issue's
own `Blocked by` field, which is what actually gates when an agent may start
work. Keep all three in sync as issues are filed.

1. #<issue> — <step 1 name>
2. #<issue> — <step 2 name>
3. ...

## Notes

- This epic tracks the macro-scope; it is closed when the scope's own
  governance-closeout issue (see the governance-closeout template) is merged
  and `SCOPE.md`'s Status flips to CLOSED. Close the Milestone at the same
  time.
- Do not use this issue to authorize concurrent work on multiple child issues
  — the repository rule of **one bounded mutation slice at a time** still
  applies regardless of how many child issues are filed up front.

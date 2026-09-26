---
name: "[Epic] Macro-scope tracking"
about: Parent tracking issue for a macro-scope (Wave/vertical). Links its child issues; does not itself carry an Active Path.
title: "[Epic] <Wave/scope name>"
labels: []
assignees: ''
---

## Scope

Parent tracking epic for the **<macro-scope name>** (Milestone #<N>, Vertical Milestone <VN>).

<One paragraph: what this scope builds, why, and the one-line pipeline it establishes (see the scope's own SCOPE.md Objective for the exact wording).>

Governing authority:

- `<branch>:SCOPE.md`
- <ADR-00XX, ...>
- `docs/product/CAPABILITY_DAG.md` (Atoms <list>)
- Decision Gate <DG-X> (if any)

## Child issues (Active Path order)

List child issues in the same order as `SCOPE.md`'s Active Path. This list is
for human/epic navigation only — it is **not** a substitute for each child
issue's own `Blocked by` field, which is what actually gates when an agent may
start work. Keep both in sync as issues are filed.

1. #<issue> — <step 1 name>
2. #<issue> — <step 2 name>
3. ...

## Notes

- This epic tracks the macro-scope; it is closed when the scope's own
  governance-closeout issue (see the governance-closeout template) is merged
  and `SCOPE.md`'s Status flips to CLOSED.
- Do not use this issue to authorize concurrent work on multiple child issues
  — the repository rule of **one bounded mutation slice at a time** still
  applies regardless of how many child issues are filed up front.

# ADR-0053 — `translate_intent` stays stateless; repeated-entry gating is the caller's responsibility

**Status:** ACCEPTED
**Date:** 2026-10-02

## Context

Design gate issue #251 (materialized from #232 D3; originally raised
externally via `NeoNix-Lab/omega#15` U13). Confirmed directly in
`execution/__init__.py`: `translate_intent` has no position/ledger state —
its own docstring already says so ("this package has no position/ledger
state (that is H04, issue #144)") — and for an entry (`LONG`/`SHORT`)
`DecisionIntent`, it always sizes and admits a brand-new order from
`spec.sizing_policy`, with no check for whether the caller is already at the
intent's `target_position`. If an upstream entry signal stays true across
many ticks (e.g. a momentum condition that doesn't flip for several bars),
calling `translate_intent` once per tick admits a brand-new full-size order
every time — pyramiding the position, not re-affirming it.

The Omega consumer already found this directly and gates repeated entries at
its own signal layer. This is not a hypothetical: Omega verified both
directions experimentally (`omega` PR #18) — with the gate, a momentum
condition that stays true for 6 ticks produces exactly 1 entry; a mutation
test that removed the gate reproduced repeated entries (pyramiding) on every
one of those 6 ticks.

The module boundary this sits on predates this ADR: `execution/__init__.py`'s
own top docstring states the package owns H01 (order/fill lifecycle), H02
(cost models) and H03 (conflict resolution), and explicitly does *not*
implement "double-entry portfolio/ledger accounting (H04, owned by
`quant_platform.portfolio`)". Target-position-delta semantics ("am I already
at this target?") requires knowing the current position — that is precisely
H04's job, not H01-H03's.

Issue #251 posed this as a binary choice: (a) teach `translate_intent` (or
its caller) target-position-delta semantics, which requires H04
position/ledger awareness inside or ahead of this seam, or (b) keep today's
stateless-per-intent behavior and make the caller's gating responsibility
explicit and unambiguous. This ADR decides (b).

## Decision

**`translate_intent` remains stateless per call.** It does not gain
position/ledger awareness, and it does not become a no-op when a target
position is already held. **A caller that replays a persistent entry signal
across multiple ticks is responsible for gating repeated entries itself**
(e.g. tracking "have I already acted on this entry condition" at its own
signal or orchestration layer, as the Omega consumer already does and has
verified).

This is a decision to leave an existing, correctly-scoped boundary exactly
where it already was — H01-H03 (this package) stops at "translate one
`DecisionIntent` into one `Order`"; H04 (`quant_platform.portfolio`, a
separate, not-yet-integrated-here package) owns knowing what is currently
held. Moving position-awareness into `translate_intent` or inserting it as a
mandatory pre-check ahead of every call would mean either:

- giving H01-H03 a dependency on H04 state it was deliberately scoped not to
  need (every `translate_intent` caller would need to supply a full,
  current, correctly-reconciled position snapshot just to admit a single
  order), or
- building a narrower, parallel "have I already entered" tracking mechanism
  inside this package that duplicates part of what H04 exists to own anyway.

Both are real, proven-necessary-in-production (H04, issue #144) scope that
this design gate cannot responsibly pre-empt in passing. The caller-side gate
is not a workaround to tolerate — it is architecturally the correct place for
this responsibility until H04 is integrated with this seam, at which point
revisiting this decision would be its own design gate.

### Why not (a)

Doing target-position-delta semantics properly means "no-op re-entry at an
already-held target," which is meaningless without knowing the actual held
position — not an approximation `translate_intent` can safely guess at
(guessing wrong in either direction is worse than refusing to guess: a false
no-op silently drops a legitimate re-entry after a stop-out; a false
admission pyramids exactly as today). There is no partial version of this
that is safe to ship without H04, and H04 integration is explicitly out of
scope for this issue and this gate.

## Consequences

- No code or contract change to `translate_intent`'s behavior.
- `translate_intent`'s own docstring is amended to state the pyramiding risk
  and the caller's gating responsibility explicitly and prominently, not just
  the pre-existing, narrower exit-side note about `close_side`/`close_quantity`.
- `CORE_CONTRACTS.md` section 23 (ExecutionSpec) gains the same explicit
  statement, so the next consumer does not have to discover this by
  experiment the way Omega did.
- A new test (`tests/test_execution_v1.py`) proves the documented behavior
  concretely: replaying the same entry `DecisionIntent` across repeated
  calls admits a new full-size order every time, with no built-in gate —
  this locks in the decision as intentional, not an untested accident.
- When H04 (issue #144) is integrated with this seam, revisiting this
  decision is a new, separate design gate — this ADR does not pre-decide
  that outcome either way.
- `H01`-`H03` (`CAPABILITY_DAG.md`) remain unchanged by this ADR.

## Related

Parent tracking: #232. Closes issue #251 (D3). Found via `NeoNix-Lab/omega#15` (U13).

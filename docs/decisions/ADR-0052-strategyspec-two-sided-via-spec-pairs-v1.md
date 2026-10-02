# ADR-0052 — Two-sided strategies are expressed as a pair of single-direction StrategySpecs

**Status:** ACCEPTED
**Date:** 2026-10-02

## Context

Design gate issue #250 (materialized from #232 D2; originally raised
externally via `NeoNix-Lab/omega#15` U12). `EntryPolicy.direction` is a
single `Direction` value (`LONG` or `SHORT`; `FLAT` is explicitly rejected in
`__post_init__`) — confirmed by reading `strategy/__init__.py`. A
`StrategySpec` therefore always enters in exactly one pre-configured
direction whenever its entry signal fires; there is no way for one spec to
express "go long or short depending on which condition is true" or to
reverse directly from long to short (or vice versa) in a single decision.

`compose_decision` makes the shape of this concretely: an exit signal always
targets `Direction.FLAT` (`spec.position_policy.target_for(Direction.FLAT)`);
an entry signal always targets `spec.entry_policy.direction`, the one fixed
direction baked into that spec. A direct reversal is structurally two
decisions (exit to flat, then a separate entry), never one.

The Omega consumer already works around this today with `_long`/`_short`
`StrategySpec` pairs run independently, and explicitly rejects a
`direction: both` configuration at its own layer with an explanatory
message — i.e. it already treats single-direction-per-spec as a hard
constraint to design around, not a bug to route around silently.

Issue #250 posed this as a binary choice: (a) add single-spec two-sided
(reverse-in-one-step) semantics to the frozen G01-G04 contract, or (b)
formally document the two-spec-pair pattern as the accepted, intended v1
answer. This ADR decides (b).

## Decision

**A two-sided strategy is expressed as two single-direction `StrategySpec`s
(one `LONG`, one `SHORT`), run side by side.** No single-spec
reverse-in-one-step mechanism is added to `EntryPolicy`/`compose_decision`.

This composes cleanly with what already exists, without new semantics:

- Each spec in the pair has its own distinct `strategy_identity` (they differ
  in at least `entry_policy.direction`), so each gets independent G04
  session/cooldown state and independent provenance — nothing needs to be
  taught to share cooldown state across "the same logical strategy" because,
  by this decision, there is no single logical strategy spanning both
  directions at the platform's contract level; there are two.
- If both specs' entry conditions were ever true in the same decision
  instant (a signal-design error upstream, not an expected case), the
  resulting conflicting `DecisionIntent`s are exactly what H03's frozen
  execution conflict model (`ADR-0046`, "same-bar/OCO/partial-fill single
  result") already exists to resolve. No new conflict-resolution semantics
  are needed at the entry-direction layer.
- A direct long-to-short flip still executes as two decisions under this
  pattern (the long spec's exit fires, the short spec's entry fires) rather
  than one atomic reversal — this ADR does not claim the pair pattern makes
  reversal atomic, only that it is the accepted v1 mechanism for expressing
  two-sidedness at all. Atomic reversal is explicitly not decided here (see
  Consequences).

### Why not (a)

Single-spec reverse-in-one-step would require `EntryPolicy` to carry two
independent signal/confidence/urgency triples (one per direction) plus a
decision rule for what happens when both are true simultaneously --
duplicating exactly what two separate specs plus H03's existing conflict
model already provide, while adding new surface to the frozen G01-G04
contract for a capability the two-spec-pair pattern already delivers today,
proven by a real external consumer. There is no concrete need observed that
the pair pattern cannot satisfy.

## Consequences

- No code or contract change: `StrategySpec`, `EntryPolicy`, `Direction`,
  `compose_decision` are unchanged by this ADR.
- `CORE_CONTRACTS.md` section 21 (StrategySpec) is amended to state the
  two-spec-pair pattern explicitly, so the next consumer does not have to
  rediscover it independently (as Omega did).
- This ADR does **not** decide whether a future atomic reversal primitive is
  ever worth building; it only declines to build one now, for the reason
  above. A future design gate could reopen this if a concrete need for
  single-decision atomicity (e.g. a fill-latency or slippage argument that
  the two-decision pattern cannot satisfy) is observed.
- `G01`/`G04` (`CAPABILITY_DAG.md`) remain `RESOLVED`/`FROZEN` per their
  existing status; this decision changes neither.

## Related

Parent tracking: #232. Closes issue #250 (D2). Found via `NeoNix-Lab/omega#15` (U12).

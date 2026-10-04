# ADR-0051 — StrategySpec signal rules: the identity-backed `execution_policy` slot is the intended extension point

**Status:** ACCEPTED
**Date:** 2026-10-02

## Context

Design gate issue #249 (materialized from #232 D1; itself raised externally
while building declarative strategies on `NeoNix-Lab/omega`, tracked there as
`omega#15` U11). `StrategySpec`'s `EntryPolicy`/`ExitPolicy` reference signals
only by `signal_key: str` (`strategy/__init__.py`) — a bare lookup key into
whatever external computation produced a boolean condition. The actual rule
logic (thresholds, comparison operators, stop-loss/take-profit levels) that
decides what a signal key *means* is not represented anywhere in
`StrategySpec` itself.

A consumer building real strategies on top of the platform needs two
strategies with different thresholds (e.g. two momentum strategies differing
only in a lookback window or a stop-loss percentage) to produce two different
`strategy_identity` values — otherwise they would silently collide on one
identity despite being functionally different strategies, corrupting every
downstream provenance/reproducibility guarantee `strategy_identity` exists to
provide. The Omega consumer's existing workaround carries its full rule set
(thresholds, stop-loss, take-profit) inside the `execution_policy` slot
specifically so it participates in the identity fingerprint.

`StrategySpec.execution_policy` is typed `ExecutionPolicy = IdentityBackedPolicy`
(`strategy/__init__.py`), a `Protocol` requiring only `.identity: str` and
`.stable_dict() -> Mapping[str, Any]`. Its own docstring already states it is
"the typed policy interface for slots whose semantics are outside [the
frozen G01-G04 contract]" — i.e. this slot was already designed as the
escape hatch for exactly this kind of consumer-owned semantics; it was simply
never promoted from an implicit code-level pattern into an explicit,
discoverable statement in `CORE_CONTRACTS.md` or an ADR, so each new consumer
has had to rediscover it independently.

Issue #249 posed this as a binary choice: (a) add a first-class
rule/threshold type to the frozen G01-G04 `StrategySpec` contract, or (b)
formally document the existing `execution_policy` pattern as the intended,
supported mechanism. This ADR decides (b).

## Decision

**`StrategySpec.execution_policy` is the intended, supported extension point
for any signal rule, threshold, or exit-level logic (stop-loss, take-profit,
or otherwise) that a consumer needs to make part of a strategy's identity.**
No new first-class type is added to `StrategySpec`.

A consumer that owns such logic:

1. Defines its own type implementing `IdentityBackedPolicy` (`identity: str`,
   `stable_dict() -> Mapping[str, Any]`) carrying whatever rule/threshold
   payload it needs — the platform places no constraint on that payload's
   internal shape beyond determinism (the same configuration must always
   produce the same `stable_dict()`/`identity`).
2. Passes an instance of that type as `StrategySpec.execution_policy`. Because
   `canonical_payload()` includes `_policy_payload(self.execution_policy, ...)`,
   any change to the consumer's rule/threshold content changes
   `strategy_identity` — two strategies with different thresholds are
   guaranteed to get different identities, with no platform-side validation
   or awareness of what the thresholds mean required.
3. Keeps `EntryPolicy.signal_key`/`ExitPolicy.signal_key` as bare references:
   the platform's frozen G01-G04 contract continues to know only *that* a
   named boolean signal exists, never *why* it is true — exactly the
   learner-agnostic boundary ADR-0009 already establishes for `DecisionIntent`,
   extended here to the rule layer that feeds a signal.

### Why not (a)

Adding a first-class rule/threshold type to the frozen contract would mean
the platform taking ownership of an open-ended family of signal
logic (arbitrary comparison operators, arbitrary indicator combinations,
stop-loss/take-profit shapes that vary per strategy family) that G01-G04
explicitly chose not to own in the first place (`strategy/__init__.py`'s own
`IdentityBackedPolicy` docstring predates this ADR and already drew that
boundary). The existing `execution_policy` slot already solves the actual
problem stated in #249 — identity uniqueness across differing thresholds —
without widening a frozen contract for a capability it was deliberately
scoped to exclude.

## Consequences

- No code or contract change: `StrategySpec`, `EntryPolicy`, `ExitPolicy`,
  `ExecutionPolicy`/`IdentityBackedPolicy` are unchanged by this ADR.
- `CORE_CONTRACTS.md` section 21 (StrategySpec) is amended to state this
  pattern explicitly, so the next consumer does not have to rediscover it by
  reading source.
- Future consumers remain free to define richer `IdentityBackedPolicy`
  implementations (e.g. a typed rule-set dataclass) entirely in their own
  codebase; the platform never needs to know their shape.
- If a future need arises that this pattern genuinely cannot satisfy (e.g.
  the platform itself needing to interpret rule semantics, not just carry
  their identity), that would be a new, separately-justified design gate —
  this ADR does not foreclose it, it only declines to open it speculatively
  now.
- `G01` (StrategySpec/DecisionIntent, `CAPABILITY_DAG.md`) remains `RESOLVED`/
  `COMPLETE` and `G04` remains `FROZEN` per `ADR-0045`; this decision changes
  neither.

## Related

Parent tracking: #232. Closes issue #249 (D1). Found via `NeoNix-Lab/omega#15` (U11).

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

**H04 is not a future capability** — `quant_platform.portfolio` is a real,
complete package (`CAPABILITY_DAG.md`: `RESOLVED`/`COMPLETE`, "integrated in
Wave 4"), and `quant_platform.replay` (H05, the platform's own reference
historical-replay runtime) already holds a live `PortfolioLedger` and already
consults it for risk snapshots and FLAT-side close instructions. Caught
during review of this ADR's own PR: **H05's runtime has the identical gap**,
not just external callers like Omega. Reproduced directly: a
`feature_provider` that keeps `signal.entry` true across three ticks (rather
than true only on the tick the condition first becomes true) produces three
full-size `BUY` orders and a final position of 3, not 1, through the
unmodified, already-shipped `HistoricalReplayRuntime` — its ledger is in
scope for other purposes but is never consulted to recognise an
already-held entry target. This means the "caller" this ADR holds
responsible is not a hypothetical external one; it already includes the
platform's own shipped backtest engine, which had zero documentation of this
responsibility anywhere before this ADR.

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
`DecisionIntent` into one `Order`"; H04 (`quant_platform.portfolio`, a real,
complete, separate package) owns knowing what is currently held, but is not
consulted by `translate_intent` itself. Moving position-awareness into
`translate_intent` or inserting it as a mandatory pre-check ahead of every
call would mean either:

- giving H01-H03 a hard dependency on H04 state for every caller — including
  ones with no ledger in scope, like bare unit tests of admission logic
  (`tests/test_execution_v1.py` exercises `translate_intent` with no
  `PortfolioLedger` anywhere; that must keep working), or
- building a narrower, parallel "have I already entered" tracking mechanism
  inside this package that duplicates part of what H04 already owns.

The caller-side gate is not a workaround to tolerate — it is architecturally
the correct place for this responsibility, because only a caller that already
has a reason to hold ledger state (like H05) can decide to consult it without
forcing every other caller to carry one too. Whether and how a specific
caller (starting with H05, which already has the ledger in scope for other
purposes) should use it for entry-gating is a separate, narrower design
question this gate does not resolve — see Consequences.

### Why not (a)

Doing target-position-delta semantics properly means "no-op re-entry at an
already-held target," which is meaningless without knowing the actual held
position — not an approximation `translate_intent` can safely guess at
(guessing wrong in either direction is worse than refusing to guess: a false
no-op silently drops a legitimate re-entry after a stop-out; a false
admission pyramids exactly as today). H04 exists and is reachable (H05
already holds a `PortfolioLedger`), so the blocker is not that the needed
state is unavailable anywhere -- it is that wiring it into `translate_intent`
specifically would mean giving H01-H03 a hard dependency on H04 state for
every caller, including ones (like bare unit tests of admission logic) that
have no ledger and should not need one. Deciding exactly where and how to
wire existing ledger state into entry-gating (at H05, generically, or
elsewhere) is its own design question this gate does not resolve; see
Consequences.

## Consequences

- No code or contract change to `translate_intent`'s behavior, nor to H05
  `HistoricalReplayRuntime`'s behavior: both remain exactly as unprotected as
  they were, by decision rather than by oversight now that it is written
  down.
- `translate_intent`'s own docstring is amended to state the pyramiding risk
  and the caller's gating responsibility explicitly and prominently, not just
  the pre-existing, narrower exit-side note about `close_side`/`close_quantity`.
- `CORE_CONTRACTS.md` section 23 (ExecutionSpec) gains the same explicit
  statement, so the next consumer does not have to discover this by
  experiment the way Omega did.
- `quant_platform.replay`'s `HistoricalReplayRuntime` and `FeatureProvider`
  gain the same warning, naming concretely that its own `PortfolioLedger` is
  in scope but not consulted for this purpose -- this was the one gap this
  ADR's own review caught: documenting "the caller" in the abstract without
  checking whether the platform's own reference caller actually follows the
  policy.
- Two new tests (`tests/test_execution_v1.py`, `tests/test_replay_v1.py`)
  prove the documented behavior concretely at both layers: replaying the same
  entry condition across repeated ticks admits a new full-size order every
  time, with no built-in gate at either `translate_intent` or H05 replay —
  this locks in the decision as intentional, not an untested accident at
  either layer.
- A future design gate could decide H05 specifically (not H01-H03) should use
  its already-in-scope `PortfolioLedger` to no-op a repeated entry at an
  already-held target, without touching the frozen `translate_intent`
  contract at all -- this ADR does not pre-decide that outcome, it only
  documents that no such gate exists today.
- `H01`-`H04` (`CAPABILITY_DAG.md`) remain unchanged by this ADR.

## Related

Parent tracking: #232. Closes issue #251 (D3). Found via `NeoNix-Lab/omega#15` (U13).

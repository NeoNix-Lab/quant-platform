# ADR-0046 — Execution conflict and intra-bar fill model v1

**Status:** ACCEPTED

**Date:** 2026-09-26

## Context

Decision Gate **DG-F.2** (`SCOPE.md`, atom `H03`) must resolve execution
conflict semantics — same-bar conflict resolution, intra-bar ambiguity, order
cancellation/OCO, and partial fills — before `H03` can unblock `H04`/`H05`
per `CAPABILITY_DAG.md`. `H03` requires `H01` (Order/Fill lifecycle) and
`G04` (resolved by ADR-0045).

ADR-0012 establishes one canonical execution engine shared across rules,
supervised and RL policy families; it does not itself define conflict
resolution. `CORE_CONTRACTS.md` §23 (`ExecutionSpec`) already names
"cancel/replace rules" and "partial-fill model" as expected concepts without
defining their shape; §26 (`Position`) names "quantity" and "average basis"
without defining whether an instrument may carry more than one concurrent
position.

Resolved collaboratively, this design turned out to reach further than
DG-F.2's original four bullets: it also fixes parts of `H01` (order replace
as a state transition) and `H04` (the position/ledger shape itself, since
hedge-mode requires more than one concurrent position per instrument). This
ADR is therefore authority for those parts of `H01`/`H04` as well as `H03` --
issues #143 (`H01`/`H02`) and #144 (`H03`/`H04`) must both follow it, not
only #144. No new `SCOPE.md` Active Path step or Milestone is introduced by
this broadening; it deepens the existing DG-F.2 design gate rather than
adding a new one.

This ADR does not touch `I06`/`I07` (Strategic/Execution RL, `OPEN_DEFERABLE`,
Wave 11/12): per ADR-0011, execution RL "decides how to implement a desired
trade/order objective" and belongs within the execution-policy boundary this
ADR defines -- it will operate through these mechanics later, not redefine
them now. Per ADR-0009, `DecisionIntent` is learner-agnostic; the decision
logic that chooses prices, quantities, and when to move a level (trailing)
remains a `G01`/`G02` strategy-policy concern this ADR does not decide --
`H01`/`H03` only execute whatever is injected, deterministically.

## Decision

### 1. Intra-bar reconstruction

The canonical source of truth is `trade-v1` (`A01`); a Candle is an accepted
first-class **derived** representation (ADR-0005), not a replacement for the
underlying trade stream. Accordingly, resolving what happens "within a bar"
uses the **real reconstructed trade path** inside that bar's declared
temporal bounds -- never a conservative bar-level convention (e.g. "assume
the stop was hit before the target because both are within the bar's
high-low range"). Events are ordered using the existing canonical order
`(exchange_ts, trade_id)` (ADR-0040) -- no new ordering primitive is
introduced.

**Tie-break:** if a single trade event's price crosses more than one
conflicting trigger level simultaneously (there is no earlier/later order to
resolve it), the **more conservative outcome wins** (e.g. a stop-loss trigger
takes precedence over a take-profit trigger tied at the same trade).

**Forward compatibility (not implemented now):** this mechanism must be
described in terms of "the finest-grained ordered event stream available
within this bar's bounds," not hardcoded to always mean "query `trade-v1`
directly." A future capability to build a higher-timeframe bar from other,
lower-timeframe bars (rather than only from raw trades) must be able to
supply an analogous ordered stream without requiring `H03`/`H05` to be
redesigned. Designing that multi-grain source-resolution mechanism itself is
explicitly deferred (`OPEN_DEFERABLE`, evidence-triggered, matching this
platform's existing treatment of `A12`/`L1`-`L3`) -- this ADR only requires
that today's implementation not preclude it.

### 2. Position model: hedge-mode (touches H04)

An instrument may carry **two independent concurrent positions**, one per
side (`LONG`, `SHORT`), each with its own quantity, average basis, and
separately realized PnL -- not one net position with a flag. This is
"hedge-mode" as implemented natively by major crypto perpetual venues
(Binance, Bybit), not a generic multi-asset concept: it is scoped to one
instrument's two sides, and is unrelated to and does not activate `H06`
(multi-asset execution, `OPEN_DEFERABLE`, out of this Wave's scope).

A future aggregated cross-side view (e.g. one combined "net exposure" report
spanning both sides) is not designed now and is not precluded by this shape.

### 3. Opening (entry) logics

Three mutually exclusive patterns, selected and injected by the strategy
(`G01`/`G02`) per trading decision -- `H01`/`H03` process whichever is
injected generically, without hardcoding which pattern is "normal":

1. **Single**: one entry logic, one side.
2. **Hedge**: two independent entry logics, opposite sides, **both intended
   to remain live simultaneously and independently** (per §2's position
   model).
3. **OCO** (One-Cancels-Other): two or more competing, mutually exclusive
   entry logics -- exactly one is meant to actually execute. Legs may target
   the same side (e.g. "enter long on breakout above X, OR enter long on
   pullback to Y") or opposite sides; direction is just a parameter of each
   competing leg, not a constraint the OCO mechanism itself imposes.
   **Any fill of any member leg, including a partial fill, cancels every
   other member of the group** -- OCO legs compete for the same underlying
   intent, so a partial commitment to one already resolves the competition.

OCO groups are **dynamic**: legs may be added to or removed from a live
group as explicit, individually recorded operations, not only at group
creation. OCO groups do not support sub-grouping (a group's cancellation
cascade always reaches every other current member; a caller wanting a
narrower relationship must use independent legs, per §4, instead of a
group).

### 4. Exit (position-management) logic: independent multileg (touches H01, H04)

Once a position(-side) exists, its exit management uses a **different**
mechanism from §3, regardless of which opening pattern created it: an
open-ended set of **independent legs** (dynamic stop-loss and take-profit
orders at up to N target levels), injected and updated by the strategy.

Each leg carries its **own allocated quantity**. Filling one leg, in whole or
in part, **never cancels or reduces any other leg** -- this is the opposite
cancellation behavior from §3's OCO groups, because multileg exit orders are
not competing alternatives, they are a scaled/laddered plan where each leg is
meant to execute independently as the position is worked down.

**Invariant:** the sum of quantities allocated across all currently active
legs for one position(-side) must never exceed that position's current open
quantity. This is a quantity-conservation constraint enforced deterministically
by `H01`/`H04`, not a cancellation rule -- an attempt to allocate more than
the remaining open quantity is refused explicitly, never silently clamped.

A "reduce-only" order (an exit order with no OCO peer) is the degenerate
`N=1` case of this same mechanism, not a separate construct: an independent
leg with no siblings behaves identically to what "reduce-only, does not
cancel with anything" already means.

An order belongs to **exactly one** of: an OCO group (§3), or the
independent-legs set (§4). §3 and §4 do not overlap on the same order --
they apply to different, non-overlapping situations (competing entries
before a position exists, versus exit management of an already-open
position), and a strategy that wants OCO-style linking among exit orders can
already express purely competing exits as an OCO group of exit orders, which
is a valid but distinct use of §3, not a blending of §3 and §4 on one order.

### 5. Order replace / trailing mechanics (touches H01)

`H01`'s Order state machine supports **replacing a resting (unfilled) order's
trigger/limit price in place**, preserving its `order_id` and its OCO-group
or independent-leg membership unchanged. This is the mechanic a strategy's
trailing-stop or dynamic-target `ExitPolicy` decision (a `G01`/`G02` concern,
not decided by this ADR) is executed through.

For v1, only the **price** is replaceable. Replacing a leg's allocated
**quantity** is not designed now and is not precluded by this shape (it would
still need to respect §4's quantity-conservation invariant whenever it is
designed).

**Replace-vs-fill ordering:** a replace instruction is itself decision-time
evidence, causally anchored (ADR-0006) to the point in the §1 reconstructed
chronological trade stream at which the strategy's decision was made. If the
order's prior trigger price was already touched by a real trade earlier in
that same chronological stream, the fill has already happened and takes
precedence -- the replace cannot retroactively un-happen it. No new rule is
required for this: it is a direct consequence of processing the reconstructed
stream in canonical chronological order (§1).

## Excluded

This ADR does not define or authorize:

- the multi-grain (bar-from-bars) intra-bar source-resolution mechanism
  itself (§1) -- deferred, `OPEN_DEFERABLE`;
- an aggregated cross-side portfolio view for hedge-mode positions (§2) --
  not designed now, not precluded;
- leg quantity replace (§5) -- not designed now, not precluded;
- `H06` multi-asset execution, `K07`/`K09`, `J02`/`J04`-`J08` -- all remain
  out of `SCOPE.md`'s Wave 4 scope, untouched by hedge-mode being
  single-instrument;
- `I06`/`I07` Strategic/Execution RL state, action or reward contracts --
  those remain `OPEN_DEFERABLE`; this ADR only fixes the execution
  primitives they will later operate through, per ADR-0009/ADR-0011;
- `H02`'s cost/synthetic-fill model itself (fees/slippage) -- `H02` applies
  its own existing, separately-resolved model to whatever fills this ADR's
  mechanics produce, regardless of OCO/multileg structure.

## Consequences

- Issues #143 (`H01`/`H02`) and #144 (`H03`/`H04`) must both implement per
  this ADR, not only #144 -- #143 owns the order-replace state transition
  (§5) and the reduce-only/independent-leg order shape (§4); #144 owns the
  hedge-mode position/ledger shape (§2), OCO group mechanics (§3), the
  quantity-conservation invariant (§4), and the intra-bar reconstruction/
  conflict resolution (§1) composed with `H01`'s state machine.
- `H03` can be credited `RESOLVED` in `docs/architecture/OPEN_DECISIONS.md`
  and `docs/product/CAPABILITY_DAG.md` once this ADR is accepted, per the
  governance-boundary rule that only an explicitly designated governance
  issue may make that edit (`#147`, not this one).
- `H04` (which requires `H01`, `H02`, `H03`) remains blocked until #143 and
  #144 are both credited complete, not merely once this ADR is accepted.
- A future Execution RL design (`I07`, deferred) inherits a substrate that
  already supports hedge-mode, competing entries, and independent scaled
  exits -- it will select/parametrize within these mechanics rather than
  needing new execution primitives invented for it later.

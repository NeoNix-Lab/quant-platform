# ADR-0045 — Session calendar and cooldown semantics v1

**Status:** ACCEPTED

**Date:** 2026-09-26

## Context

Wave 4's `StrategySpec` (`CORE_CONTRACTS.md` §21) already names `SessionPolicy`
as one of its possible components, without defining its semantics. Decision
Gate **DG-F.1** (`SCOPE.md`, atom `G04`) must resolve that semantics — session
calendars, cooldown policy, and timezone/DST integrity — before `G04` can
unblock `H01`/`H03` per `CAPABILITY_DAG.md`.

`G04` sits downstream of `G02` (policy composition) and `F06` (availability),
and must respect the platform's existing temporal authority:

- ADR-0006: no decision may use information unavailable at `decision_time`.
- `CORE_CONTRACTS.md` §33: `feature_available_time <= decision_time <=
  order_submit_time <= fill_time`, and every derived value must be able to
  explain which source events it consumed and when they became available.

Bybit BTCUSDT itself is a 24/7 perpetual market: there is no Bybit trading
calendar, no weekend closure, no exchange holiday. Any "session" semantics
here are therefore not Bybit's own calendar — they are a strategy-facing
temporal reference to the trading sessions of major traditional markets
(New York, London, Tokyo), whose liquidity/volatility rhythms are commonly
used as a signal even for instruments that never close.

`PressurePolicyDefinition v1` (ADR-0028) already establishes an accepted,
frozen shape for "deterministic policy over explicit evidence, evaluated at
one explicit instant, with restrictions as upper bounds only, never a silent
permissive default." Cooldown policy adopts that same shape rather than
inventing a new idiom.

## Decision

### 1. Session reference markets

Define three reference markets for v1: **New York** (NYSE/NASDAQ), **London**
(LSE), **Tokyo** (TSE). These are not Bybit's own calendar (Bybit/BTCUSDT has
none); they are named, independently evaluable temporal references a strategy
may consult. A future second venue/instrument does not require touching this
ADR; a future second reference market is additive in the same shape.

Each reference market has:

- a **market-open calendar**: the set of UTC calendar dates on which it is
  open at all, derived from that market's real, documented holiday/closure
  calendar (not a bare Monday–Friday approximation);
- zero or more named **phases** per open day, each an intraday time-of-day
  window converted to UTC. A market with no established phase structure
  simply has one phase ("regular") or the phases that are real for it —
  phase structure is not forced to be symmetric across reference markets.

Verified real, standard-time (non-DST) hours, converted to UTC (verified
2026-09-26; sources below):

```text
New York (NYSE/NASDAQ), standard EST = UTC-5
  pre-market   09:00–14:30 UTC
  regular      14:30–21:00 UTC
  post-market  21:00–01:00 UTC (next day)

London (LSE), standard GMT = UTC+0
  pre-trading      05:05–07:50 UTC
  pre-open auction 07:50–08:00 UTC
  regular (core)   08:00–16:30 UTC
  closing auction  16:30–16:35 UTC
  post-trading     16:40–17:15 UTC

Tokyo (TSE), JST = UTC+9 fixed, no DST ever
  morning    00:00–02:30 UTC
  (lunch break 02:30–03:30 UTC: no phase, market closed for the break)
  afternoon  03:30–06:30 UTC (current hours, effective since the
                              November 2024 30-minute extension)
```

Tokyo has no established pre-market/post-market retail trading convention
(unlike New York); London's extended-hours structure is auction-based
(pre-open/closing auctions), not continuous trading like New York's
pre/post-market. This asymmetry is a real fact about these markets, not an
omission to fix.

Sources: NYSE/NASDAQ hours (stockmarkethours.org, TD Direct Investing);
London Stock Exchange hours (globalexchangeclock.com, LSE public trading
schedule); Tokyo Stock Exchange hours (Japan Exchange Group, official trading
rules, jpx.co.jp — including the November 2024 afternoon-session extension).

### 2. Historical accuracy scope (v1)

- **DST transitions** (New York, London — Tokyo has none) and **holiday /
  closure calendars** (all three markets, including one-off special closures
  that cannot be derived from any recurring rule) are both modeled the same
  way: an explicit, versioned **static date table** — never computed from a
  live host timezone database, and never derived from a calendar rule at
  evaluation time. This keeps evaluation deterministic and independent of the
  host system clock/tz data, which `H05`'s bitwise-reproducible replay
  requires.
- The table must cover at least **2020-01-01 through 2035-12-31** (Bybit
  BTCUSDT historical data begins 2020-03-25); extending it further is a data
  change, not a design change, and does not require reopening this ADR.
- **Early closes** (e.g. a market closing at a modified time on a specific
  date) are entries in this same table — an exception overriding that date's
  normal phase end-time — not a separate mechanism from full-day closures.
- Exchange **session-hour rule changes over history** (e.g. TSE's November
  2024 30-minute afternoon-session extension) are explicitly **not** modeled
  retroactively for v1: the current rule applies uniformly across all
  historical replay. This is a deliberate, accepted v1 simplification, and it
  is intentionally asymmetric with DST/holiday handling above — DST and
  holiday closures recur predictably and indefinitely into the future, so a
  table costs little and stays exact; a market changing its own session-hour
  rule is rare and arbitrary, and modeling its full history is not required
  for this vertical.
- The literal table contents (exact per-year DST transition instants, exact
  holiday/closure/early-close dates for 2020–2035, per market) are **not**
  fixed by this ADR — see Excluded.

### 3. Session data model and evaluation

Evaluating one named reference session at an explicit UTC instant answers
exactly two questions: is this market open at all on this UTC calendar date
(per its market-open calendar), and if so, which phase (if any) contains this
instant. A caller receives an explicit "not open" result on a closed date —
never a silently assumed phase.

No pre-named "derived" or overlap sessions (e.g. a hypothetical
`london_ny_overlap`) are defined. A strategy computes overlaps itself by
evaluating multiple named sessions and composing the per-session results —
this keeps the primitive minimal and lets a strategy define whatever
combination of reference markets it actually needs.

At any instant, zero, one, or multiple reference sessions may be
simultaneously open. This is expected, not an error state: New York
pre-market materially overlaps London's regular session, and a roughly 2.5
hour window exists between Tokyo's afternoon close (06:30 UTC standard time)
and New York's pre-market open (09:00 UTC standard time) during which no
reference market is open at all.

### 4. Trading-day boundary

Since BTCUSDT itself never closes, "trading day rollover" is a reporting/
accounting convention, not an exchange event. The trading-day boundary is
**UTC midnight**, consistently with the frequency-limit day boundary in §6.

### 5. Excluded from session-calendar scope

- Bybit's own trading calendar (it has none for BTCUSDT perpetual; this ADR
  does not invent one).
- A generic multi-venue/multi-exchange calendar framework; only the three
  named v1 reference markets are in scope. A second venue's real trading
  hours (A12) are deferred until a concrete need exists, per `SCOPE.md`.
- The literal DST/holiday/early-close table row data for 2020–2035 (verified
  data-compilation work — see Consequences).

### 6. Cooldown policy

`CooldownPolicyDefinition v1` follows `PressurePolicyDefinition`'s accepted
shape: an immutable, versioned policy, evaluated at one explicit UTC `as_of`
decision instant against explicit, caller-supplied realized-trade-history
evidence. The evaluator never reads a host clock and never queries trade
history itself.

Three named triggers combine into **one** evaluation (mirroring
`PressureDecision`'s single combined state, not three independent state
machines a caller must reconcile):

- **Post-loss cooldown**: any single realized loss — a position closed (per
  `H04`) with negative realized PnL, settled at or before `as_of` — starts a
  cooldown lasting a configured duration.
- **Consecutive-loss cooldown**: `N` consecutive realized losses start a
  (typically longer) cooldown lasting a separately configured duration. Any
  realized win resets the consecutive-loss streak counter to zero
  unconditionally.
- **Intra-day frequency limit**: no more than a configured number of new
  position entries — **actually opened positions**, not merely emitted
  `DecisionIntent`s that were never executed — may start within one UTC
  calendar day (§4's boundary).

**Composition rule:** when the post-loss and consecutive-loss cooldowns are
both active, the effective cooldown-until instant is the **maximum** (later)
of the two. They extend each other; the later trigger never silently replaces
or shortens the earlier one.

Invariants:

- Only realized, settled, closed-position outcomes may trigger the post-loss
  or consecutive-loss cooldown. An open position's unrealized PnL never does
  — per ADR-0006/§33, an unrealized mark is not a causally-settled economic
  fact available for a decision to consume as trade history.
- A cooldown restricts **new entries only**. It never blocks exit/reduction
  of an existing position: Risk/Exit policies remain free to close a position
  at any time regardless of cooldown state.
- Like `PressureRestrictions`, a `CooldownDecision`'s restrictions are upper
  bounds only: an `ELIGIBLE` result never itself authorizes a trade, it only
  means the cooldown gate does not forbid one.
- Missing, stale, future-dated or malformed required trade-history evidence
  produces an explicit unavailable decision (mirroring
  `PressureDecisionUnavailable`) — never a silent default to `ELIGIBLE`.

## Excluded

This ADR does not define or authorize:

- the literal DST transition / holiday / early-close table contents for
  2020–2035 (verified data-compilation work, not a semantic decision — see
  Consequences);
- any second venue's or second instrument's session calendar (`A12` remains
  deferred per `SCOPE.md`);
- `H01`/`H02`/`H04` Order/Fill/Portfolio semantics themselves, beyond the
  read-only realized-loss evidence `CooldownPolicy` consumes from them;
- `DG-F.2`'s execution conflict / intra-bar / OCO / partial-fill semantics
  (`H03`), a separate design gate;
- a generic multi-market calendar or policy-engine framework.

## Consequences

- Issue #142 (`G03`/`G04` implementation slice) can implement
  `SessionPolicy` and `CooldownPolicyDefinition`/`CooldownDecision` per this
  design without further semantic decisions. It must additionally compile the
  actual DST/holiday/early-close table for New York, London and Tokyo
  covering 2020–2035, citing authoritative per-exchange sources, as verified
  data — not as a new design question.
- `G04` can be credited `RESOLVED` in `docs/architecture/OPEN_DECISIONS.md`
  and `docs/product/CAPABILITY_DAG.md` once this ADR is accepted, per the
  governance-boundary rule that only an explicitly designated governance
  issue may make that edit (`#147`, not this one).
- `H01` (which requires `G04` per `CAPABILITY_DAG.md`) remains blocked until
  issue #142's implementation is credited complete, not merely once this ADR
  is accepted.
- `DG-F.2` (`H03`, issue #140) is independent of this ADR's content and may
  proceed in parallel or in sequence per the repository's one-slice-at-a-time
  discipline, not per any dependency this ADR introduces.

# ADR-0044 — Live-ingest long-gap remediation and explicit-gap state v1

**Status:** ACCEPTED
**Date:** 2026-09-25

## Context

`SCOPE.md` ("Live Ingest Server Production Readiness v1") Active Path step 3
requires a design gate closing findings **P2** (long-gap remediation path),
**P3** (source authority for missing data) and **P4** (gap state machine and
repair queue) before any implementation slice may touch long-gap handling
(issue #125, blocking issue #127).

Issue #110 (2026-09-24) disposed the only known candidate repair source --
the public Bybit historical archive at
`https://public.bybit.com/trading/BTCUSDT/BTCUSDTYYYY-MM-DD.csv.gz` -- as
`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`: at observation time the archive
stopped at `2026-09-22` (`2026-09-23` returned `404`), one day short of the
live/recent-trade window, so no archive/live overlap could be compared, and
no manifest/checksum/per-interval completeness attestation existed for any
requested interval.

Per this ADR's own governing issue, that disposition may only be revisited
with newly observed, attributable evidence -- so this ADR re-audits the
source before designing the state machine around it, rather than assuming
#110's evidence is still current.

### Re-audit performed 2026-09-25 (new observed evidence)

Direct `HEAD` requests (not directory-listing summarization, which proved
unreliable against this bucket's size) against the archive:

```text
BTCUSDT2026-09-22.csv.gz  200 OK  Last-Modified: 2026-09-23 01:12:08 GMT
BTCUSDT2026-09-23.csv.gz  200 OK  Last-Modified: 2026-09-24 01:13:58 GMT
BTCUSDT2026-09-24.csv.gz  200 OK  Last-Modified: 2026-09-25 01:13:18 GMT
BTCUSDT2026-09-25.csv.gz  404 Not Found (NoSuchKey)
```

This shows the archive is not stalled: it publishes each day's file at
approximately `01:1x UTC` the *next* day, and by 2026-09-25 covers through
`2026-09-24`. No manifest, checksum or per-interval completeness attestation
file was found alongside the daily objects (only the `.csv.gz` object itself,
consistent with #110's finding).

The live recent-trades endpoint (`GET /v5/market/recent-trade`, ADR-0040 §7's
bounded reconciliation source) was also re-checked: it returns only the
most-recent handful of trades (by `time`/`seq`), i.e. a window of seconds at
BTCUSDT's trade rate, not a window reaching back a full day.

**New finding beyond #110:** the gap between the two sources is not an
observation-time accident that might close on its own -- it is structural.
The archive's freshest complete day is always approximately 24 hours behind
"now" (publication lag), while the bounded recent-trades endpoint ADR-0040 §7
authorizes for reconciliation only reaches back seconds to low minutes. Under
current, observed provider behavior, **the archive's coverage and the
recent-trades bounded reconciliation window structurally never overlap** --
there is always an unobserved band between "yesterday's finalized archive"
and "the last few minutes of live trades" that neither source covers. This is
a stronger, more precise finding than #110's "one day short at observation
time," and it does not depend on exactly which day is checked.

**Disposition:** `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` is reconfirmed, not
reopened, for two independent reasons: (1) no completeness attestation exists
at the archive source, and (2) the archive and the live bounded-reconciliation
window do not structurally overlap under currently observed provider
behavior. Per issue #125's own stop condition, this is "a valid design-gate
result, not a failure" -- it does not block this ADR from defining the
accepted state machine and source-authority criteria below, which apply
whether or not a source is proven today.

## Decision

### 1. Long-gap is a derived classification, not a new store

The gap/repair state machine below is a **read model derived from existing
durable records** -- Declared Coverage (`B04`/ADR-0029) segments, A10
repair-intent/candidate/cutover records (ADR-0033), and the K10 checkpoint
(ADR-0042) -- not a new persisted table or a second source of truth for
coverage. This follows the same rule the rest of the platform already
applies (no second live-only storage/catalog/identity system).

### 2. Accepted state machine

Two levels are distinguished, because they answer different operator
questions and were conflated in `SCOPE.md`'s original flat P4 list.

**Session-level state** (one value at a time, surfaced in ADR-0043's
`HEALTH_SNAPSHOT` signal):

```text
CONTINUOUS              no open (unrepaired) gap interval exists anywhere
                        in this dataset's history
RESTART_RECONCILING     ADR-0042 S5 bounded reconciliation is in progress
                        (transient, entered on start/reconnect)
RESUMED_WITH_EXPLICIT_GAP
                        the live segment is running normally, but at least
                        one interval is in GAP_RECORDED_NON_COMPLETE or a
                        REPAIR_* sub-state below
```

`RESTART_RECONCILING` exits to `CONTINUOUS` when ADR-0042 S5 proves
continuity, or to `RESUMED_WITH_EXPLICIT_GAP` when it cannot (a new
`GAP_RECORDED_NON_COMPLETE` interval is recorded per below). This is not a
new invariant: it is the observable classification of the exact
`RestartOutcome` values `resume_live_ingest` already returns
(`RESUMED` -> `CONTINUOUS`, `GAP_DETECTED` -> `RESUMED_WITH_EXPLICIT_GAP` once
the new segment resumes, `NO_CHECKPOINT` -> `CONTINUOUS` for a first
acquisition).

**Per-interval state** (one value per recorded non-complete interval; an
installation may have zero, one or many over its lifetime):

```text
GAP_DETECTED               transient: interruption observed, exact
                            [last_durable_key, new_segment_start_key) bound
                            being computed
GAP_RECORDED_NON_COMPLETE  durable: explicit non-complete coverage persisted
                            via the existing Declared Coverage mechanism; the
                            entry state before any source evaluation has been
                            attempted or completed for this interval
REPAIR_SOURCE_EVALUATION_INCONCLUSIVE
                            durable but retryable: an evaluation against S3
                            was attempted for this exact interval but could
                            not be completed (query failure, ambiguous
                            evidence, evaluator crash); this is not a
                            judgement about the source and must not be
                            treated as a rejection
REPAIR_SOURCE_UNPROVEN     durable, terminal absent new evidence: an
                            evaluation against the source-authority criteria
                            (S3 below) *completed* for this exact interval
                            and the source failed one or more criteria --
                            a genuine negative result, not a missing one
REPAIR_CANDIDATE_PENDING   the source met S3's criteria for this exact
                            interval; an A10 repair candidate has been
                            produced and awaits cutover validation
REPAIR_CUTOVER_COMPLETE    A10's existing atomic cutover has run and
                            re-verified the interval; coverage now reads
                            complete under existing coverage/publication
                            contracts
```

Fail-closed transition rules:

- the only transition into `REPAIR_CUTOVER_COMPLETE` is from
  `REPAIR_CANDIDATE_PENDING` after A10 cutover re-verification succeeds; no
  other state may be reclassified as complete;
- an interval whose repair-source evaluation cannot be *completed* (query
  failure, ambiguous evidence, evaluator crash) classifies as
  `REPAIR_SOURCE_EVALUATION_INCONCLUSIVE`, never as proven and never
  collapsed into `REPAIR_SOURCE_UNPROVEN`'s durable-negative meaning -- the
  same fail-closed direction K03's `HealthState` already uses for
  unavailable observations (`UNKNOWN`, never `HEALTHY`), but kept
  observably distinct from an actual negative result so it remains
  retryable rather than durably rejected;
- only an evaluation that *completes* against S3 for the exact interval and
  fails one or more criteria may move the interval to `REPAIR_SOURCE_UNPROVEN`;
- `REPAIR_SOURCE_EVALUATION_INCONCLUSIVE` is retried on the next scheduled
  or operator-triggered evaluation attempt without any new evidence
  requirement, since nothing about the source was actually decided;
  `REPAIR_SOURCE_UNPROVEN`, by contrast, requires new observed evidence
  before re-evaluation is attempted again (the same discipline issue #110's
  own closing comment already established: "a future issue could reopen
  this once ... evidence exists");
- `GAP_RECORDED_NON_COMPLETE`, `REPAIR_SOURCE_EVALUATION_INCONCLUSIVE` and
  `REPAIR_SOURCE_UNPROVEN` are all stable and may persist indefinitely; the
  session-level state remains `RESUMED_WITH_EXPLICIT_GAP` for as long as any
  interval is not `REPAIR_CUTOVER_COMPLETE`, and normal live operation is
  unaffected. Today's re-audit (§ above) is a *completed* evaluation with a
  definitive negative result, so the archive candidate for any interval
  discovered under current provider behavior lands in `REPAIR_SOURCE_UNPROVEN`,
  not `REPAIR_SOURCE_EVALUATION_INCONCLUSIVE` -- this distinction is exactly
  why the two states must not be collapsed.

### 3. Source-authority criteria (S3)

Before any candidate source may move an interval into `REPAIR_CANDIDATE_PENDING`,
an evaluation must *complete* against all of the following, as observed and
attributable evidence -- never inferred from missing `seq`, elapsed wall
time, absence of trades, local buffer contents or operator convenience:

- exact interval support for the missing interval (not merely file
  existence -- a completeness attestation, checksum, provider manifest, or
  equally strong proof the requested `[start, end)` is fully represented);
- source identity and provenance (who publishes it, and why it is
  attributable to the same economic venue/instrument);
- overlap, or other equally strong evidence, that the candidate source's
  coverage and the existing canonical history's boundary actually converge
  (today's re-audit shows this fails structurally for the archive +
  recent-trades pairing -- see Context);
- a mapping to canonical semantics per S4 below without inventing sequence
  continuity.

Two distinct failure outcomes follow from this, and must not be conflated:

- the evaluation *completes* and one or more criteria are actually
  unmet for the exact requested interval -> `REPAIR_SOURCE_UNPROVEN`
  (durable negative result for that interval);
- the evaluation *cannot complete* at all (the check itself failed, timed
  out, or returned ambiguous evidence) -> `REPAIR_SOURCE_EVALUATION_INCONCLUSIVE`
  (no judgement was made; must be retried, not treated as a rejection).

### 4. Canonical binding (S4) -- defined now for whenever a source is proven

Even though no source is proven today, the binding rule is decided now so
that proof of a future source requires no further semantic decision:

- a repair-candidate row maps to `TradeKeyV1`/`trade-v1` through the exact
  same canonical construction ADR-0040 §2 already defines for live records
  (`venue`, `instrument` fixed to the selected vertical; `exchange_ts` and
  `trade_id` taken directly from the source's equivalent fields --
  `timestamp`/`trdMatchID` for the Bybit archive's observed schema);
  historical/repair rows never get a separate mapping function from live
  rows;
- absent `seq` in a candidate source (as observed in the Bybit archive
  schema) is handled exactly as ADR-0040 already handles it for live
  records: diagnostic evidence only, never required, never synthesized;
- canonical total order remains `(exchange_ts, trade_id)` uniformly; repair
  rows must sort into existing canonical history without reordering already
  -published records.

### 5. A10 composition (S5)

- A repair candidate for one `GAP_RECORDED_NON_COMPLETE` interval is
  represented through A10's existing repair-intent mechanism (ADR-0033),
  scoped exactly to that interval's `[start_key, end_key)` -- never
  open-ended or applied to un-recorded intervals;
- A10's existing isolated-candidate-attempt and atomic compare-and-cutover
  transaction (reusing the A16/S14 seams) is reused unchanged; this ADR does
  not add a second cutover mechanism;
- ADR-0033's own accepted limitation ("coverage-trigger re-verification is
  architecturally unsatisfiable inside A10's own package boundary") means
  A10 cannot self-verify S3/S4 compliance. The long-gap orchestration
  introduced by this ADR is the caller that performs S3/S4 verification
  *before* invoking A10's candidate/cutover step, discharging the
  attributable-evidence caller-discipline obligation `OPEN_DECISIONS.md`
  already assigns to E06/F02/I04-style callers, applied here to this new
  caller.

### 6. Behavior when no source is proven (the actual current path)

This is not a fallback for an edge case -- per today's re-audit, it is the
expected steady-state outcome for any interval discovered under current
provider behavior:

- the interval remains `GAP_RECORDED_NON_COMPLETE` (or
  `REPAIR_SOURCE_UNPROVEN` once a candidate has actually been evaluated and
  rejected) indefinitely;
- A11/K10 continue with a new governed live segment per ADR-0040 §7/§9,
  unaffected;
- the session-level state stays `RESUMED_WITH_EXPLICIT_GAP`, and every open
  interval must remain enumerable through the operator evidence ADR-0043 §4
  wires (P6) -- an operator must never have to infer an open gap's existence
  from absence of alerting.

## Excluded

This ADR does not define or authorize:

- production long-gap repair implementation (that is Active Path step 5,
  issue #127, blocked on this ADR);
- a second acquisition path/endpoint beyond the recent-trades bounded window
  and the archive already investigated (adding one, e.g. a deeper historical
  REST endpoint, would itself need source-authority proof under S3 and is
  explicitly not pre-authorized here);
- reopening or weakening issue #110's disposition -- this ADR reconfirms it
  with additional, more precise evidence;
- a new coverage/catalog/identity system (per §1, the state machine is a
  derived classification over existing B04/A10/K10 records);
- any change to ADR-0033's A10 semantics beyond specifying, as its required
  caller, who performs the re-verification A10 itself cannot perform.

## Consequences

- Active Path step 5 (issue #127) can implement gap detection/recording and
  the derived-state classification described here without further semantic
  decisions. For the one candidate evaluated so far (the public archive),
  the reachable durable state is `REPAIR_SOURCE_UNPROVEN` -- a completed
  evaluation with a negative result, per today's re-audit. Issue #127 must
  still implement `REPAIR_SOURCE_EVALUATION_INCONCLUSIVE` as a distinct,
  retryable outcome for any future evaluation attempt that cannot complete
  (its own or a different candidate's); it must not treat every non-proven
  interval as if it had received a completed negative evaluation.
- If a future re-audit finds a source that closes the structural overlap gap
  identified here (for example, a provider capability that narrows or
  removes the ~24h archive-publication lag, or a deeper bounded-history
  endpoint), S3/S4/S5 already state exactly how it would be evaluated and
  bound -- no new ADR is required solely to admit a proven source, only to
  authorize a genuinely new acquisition path if one becomes necessary.
- ADR-0043's `HEALTH_SNAPSHOT` signal must include the session-level state
  from §2 as one of its diagnostics dimensions, and must enumerate any
  interval not in `REPAIR_CUTOVER_COMPLETE`, closing P6 for this design gate.
- Issue #110 remains closed and its disposition stands; this ADR is the
  "future issue" its own closing comment anticipated ("a future issue could
  reopen this once the archive/live overlap gap closes and completeness
  evidence exists") -- the overlap gap has not closed, so the disposition is
  reconfirmed rather than reversed.

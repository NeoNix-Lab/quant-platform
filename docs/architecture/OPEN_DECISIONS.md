# Open Decisions

This document records only decisions that are still live or intentionally deferred. Accepted ADRs/contracts remain semantic authority; [`../product/CAPABILITY_DAG.md`](../product/CAPABILITY_DAG.md) records which atoms each decision blocks and when a branch of a gate family is activated.

## Inventory checkpoint — DG-M closeout (#475)

Recomputed from the union of the actual DAG rows and checked against expanded
Capability Map rows, preserving both parents' accepted credits. No new atom,
capability or follow-on authorization is created. DG-K/DG-K2 are already on main
(#434/#437); L2 remains on implement/l2 pending its separate final wave promotion.
The [scope verifier](../../SCOPE.md#executable-governance-verification) captures both OIDs.

```text
TOTAL_ATOMS                     = 123
CLASSIFIED_ATOMS                = 123
UNCLASSIFIED_GAPS               = 0
SEMANTIC_FROZEN_OR_RESOLVED     = 111 / 123 = 90.2%
OPEN_BLOCKING                   = 3
OPEN_DEFERABLE                  = 9
ROADMAP_DEFINED                 = 111 + 3 + 9 = 123
ROADMAP_PLANNING_COMPLETENESS   = 123 / 123 = 100%
IMPLEMENTATION_COMPLETE         = 103 / 123 = 83.7%
```

## Resolved foundation — reference only

The following are no longer open and must not be re-litigated without contradictory current authority:

```text
Contract Freeze Gate                                  PASSED
Conformity Implementation Gate                        PASSED
Human / Golden Bybit BTCUSDT E2E                     PASS
Package Boundary / Modular Monolith Foundation v1   COMPLETE
Legacy Capability Harvest Audit v1                   COMPLETE
ASS-01 Application ownership/enforcement             COMPLETE
ASS-02 in-process Application service                COMPLETE
DG-D Application configuration semantics             RESOLVED
DG-A FeatureDefinition v1 semantics                  FROZEN / COMPLETE
DG-A FootprintDefinition v1 semantics                FROZEN / COMPLETE
DG-A FeatureArtifact v1 (E04)                        FROZEN / COMPLETE
DG-A H01 canonical integration v1 (E06)              FROZEN / COMPLETE
DG-B B04 non-contiguous coverage reads v1            FROZEN / COMPLETE
DG-B A10 backfill / repair v1                        FROZEN / COMPLETE
DG-B A11 Bybit live acquisition semantics v1         FROZEN / COMPLETE
F03 Outcome v1 semantic authority                    FROZEN / COMPLETE
DG-E F07 labels/censoring/lockbox v1                 FROZEN / COMPLETE
DG-E F08 DSR/PBO robust comparison v1                FROZEN / COMPLETE
DG-H K02 live-ingest runtime identity v1             FROZEN / COMPLETE
DG-H K06 RAW / source protection v1                  FROZEN / COMPLETE
DG-H K08 backup / restore v1                         FROZEN / COMPLETE
DG-H K10 checkpoint / recovery v1                    FROZEN / COMPLETE
Live ingest server composition v1                    FROZEN / COMPLETE
Live ingest long-gap orchestration v1                FROZEN / COMPLETE
DG-B B06 live consumer cursor v1                     FROZEN / COMPLETE — ADR-0047
DG-H K07 storage tier relocation v1                  FROZEN / COMPLETE — ADR-0048
DG-H K09 retention / deletion authority v1           FROZEN / COMPLETE — ADR-0049
DG-F G04 session/cooldown semantics v1              FROZEN / COMPLETE — ADR-0045
DG-F H03 execution-conflict semantics v1            FROZEN / COMPLETE — ADR-0046
```

The completed two-stage Producer–Consumer Conformity Gate remains governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md). Its gate states above are historical accepted foundation, not live decisions.

Resolved architecture includes:

- canonical Data Access owner `quant_platform.access`;
- shared canonical primitives/Producer under `quant_platform.data`;
- deliberate shared physical seam `quant_platform.data.parquet`;
- source-specific ownership under `quant_platform.source_adapters`;
- application composition owner/package `application` / `quant_platform.application`;
- current runtime owner DAG and executable-orchestration enforcement;
- API-first direction: clients/API transport -> application services -> domain/DataGateway;
- Consumer API semantic selector/result/error boundary;
- completed ASS-02 in-process path: C02 semantic selector resolution + C03 result/error translation;
- `trade-v1`, Declared Coverage, CandleDefinition v1, FeatureDefinition v1, FootprintDefinition v1, B04 non-contiguous coverage reads v1, first-vertical conformity/publication semantics and source-acquired lineage v2;
- canonical F03 Outcome v1 semantics, including explicit end-of-data evidence, under [ADR-0038](../decisions/ADR-0038-outcome-v1-semantic-authority.md);
- K08 backup/restore semantics under [ADR-0039](../decisions/ADR-0039-backup-restore-v1.md);
- Bybit BTCUSDT live-trade acquisition/cutover/dedup/reconnect semantics under [ADR-0040](../decisions/ADR-0040-bybit-live-trades-v1.md);
- live-ingest runtime identity/least-privilege semantics under [ADR-0041](../decisions/ADR-0041-live-ingest-runtime-identity-v1.md);
- live-ingest checkpoint/recovery invariants under [ADR-0042](../decisions/ADR-0042-live-ingest-checkpoint-recovery-v1.md);
- live-ingest server composition under [ADR-0043](../decisions/ADR-0043-live-ingest-server-composition-v1.md);
- live-ingest long-gap orchestration under [ADR-0044](../decisions/ADR-0044-live-ingest-long-gap-remediation-v1.md);
- G04 session/calendar/cooldown semantics under [ADR-0045](../decisions/ADR-0045-session-calendar-and-cooldown-semantics-v1.md);
- H03 execution-conflict and intra-bar fill semantics under [ADR-0046](../decisions/ADR-0046-execution-conflict-and-intra-bar-fill-model-v1.md);
- B06 live-consumer cursor semantics under [ADR-0047](../decisions/ADR-0047-live-consumer-cursor-v1.md);
- K07 storage tier relocation semantics under [ADR-0048](../decisions/ADR-0048-storage-tier-relocation-v1.md);
- K09 retention/deletion authority semantics under [ADR-0049](../decisions/ADR-0049-retention-deletion-authority-v1.md).

### Resolved via the Omega stabilization line (`implement/omega`, tracking #232)

- A07's one-UTC-day/BTCUSDT-linear acquisition boundary is frozen, permanent and
  intentional (not a temporary v1 gap) under [ADR-0054](../decisions/ADR-0054-a07-historical-acquisition-day-boundary-v1.md); multi-day/date-range consumers loop day-by-day.
- F08's `k_eff` effective-trial-count evidence is permanently caller-supplied,
  never estimated by the platform, under ADR-0037 Amendment 1, which also
  records the recommended conservative fallback (`k_eff = N`).
- StrategySpec's `execution_policy` slot is the intended extension point for
  consumer-owned signal rule/threshold logic (ADR-0051); a two-sided strategy
  is a pair of single-direction StrategySpecs, not a new two-sided contract
  (ADR-0052); `translate_intent` stays stateless and caller-gated for repeated
  entries, both at the `translate_intent` seam and at H05 `HistoricalReplayRuntime`
  (ADR-0053).
- J02's result-size bound and wire `max_size` are frozen (ADR-0050 Amendment 1);
  its authentication/TLS gap remains explicitly open (see above and Wave 8
  design gate G1).
- D05's representation-artifact identity and replay-input-profile semantics are
  frozen (ADR-0059, see DG-A above); implementation remains missing.
- Server/deck runtime topology, artifact handoff rules and the networked
  server-to-deck boundary are frozen (ADR-0057); the authenticated/TLS
  transport this requires is the same open gate as J02's above.
- The Omega validation bridge's public import surface (ADR-0055), the Golden
  proof/test-double classification (ADR-0056), the replay sweep orchestration
  boundary (ADR-0058), the canonical replay I/O profile (ADR-0060), and the
  replay summary output mode (ADR-0061) are frozen design decisions; none
  authorizes new runtime implementation by itself.

Legacy repositories remain evidence/reference only and are never runtime dependencies.

### Resolved DG-D / C05 configuration semantics

The Application configuration decision is frozen as:

```text
CLI / environment
      ↓
executable boundary: acquire + resolve input only
      ↓
typed immutable capability-specific Application config
      ↓
quant_platform.application: concrete composition owner
      ↓
Catalog / DataGateway / source / producer capabilities
```

Rules:

- executable tooling owns CLI/environment acquisition and parsing;
- precedence is **explicit CLI > environment > declared default > explicit failure**;
- Application receives resolved values only and does not know their acquisition source;
- Application-facing config is typed, immutable and capability-specific;
- `quant_platform.application` owns concrete composition and does not read process arguments/environment directly;
- no generic DI container, service locator, provider registry or plugin/config framework is introduced.

This resolves the C05 decision state. Both C05 and C04/ASS-03 implementation are complete (C04 via issue #40/PR #44, 2026-09-14).

## Decision-gate policy

An `OPEN_BLOCKING` decision is not automatically a current project. It becomes active only when a selected dependent atom reaches that proposition on its transitive dependency path.

A gate-family name groups related decisions for navigation; it is **not** a requirement to resolve every sibling proposition in the family.

An `OPEN_DEFERABLE` decision remains deliberately unresolved until its stated real-world evidence trigger exists. No decision is frozen merely to improve a completeness percentage.

## DG-A — Representation / Feature integration

This family has independent branches.

### Candle materialization (`D05`) — FROZEN (semantics), implementation MISSING

ADR-0059 freezes `D05`'s semantic decision: a future `RepresentationArtifact`'s
identity (representation definition, source dataset/schema/ordering/partitions,
manifest/content digests, declared output support and required source support,
output partition identities/digests, implementation identity); candle support/
availability rules for materialized rows; and the replay-input-profile shape
(FINAL-only, causally-available rows; tick and representation replay are not
interchangeable without a future declared bar-close profile proving equivalence).

**This freezes semantics only.** ADR-0059 explicitly excludes D05 runtime,
two-resolution replay access, and any change to existing tick replay. No
persisted candle materialization, representation-artifact runtime, or
representation-replay profile is implemented. Do not read ADR-0059 as
implementing persisted candles.

`D05` is not a prerequisite of canonical H01 integration.

### FeatureArtifact (`E04`) — RESOLVED

Artifact identity/provenance and equivalence of cached vs recomputed output
are frozen and complete under [ADR-0034](../decisions/ADR-0034-feature-artifact-v1.md).
`FeatureArtifactIdentity`, portable `FeatureSetDefinitionIdentity`,
`SupportShape` non-contiguous support, FINAL-only sealing and the
attributable-evidence caller-discipline obligations on E06/F02/I04 are the
accepted authority; do not re-litigate them without contradictory current
authority.

### Footprint + canonical H01 (`D06`,`E06`) — RESOLVED

The canonical H01 path is:

```text
D06 footprint representation (FROZEN / COMPLETE by ADR-0027)
E02 FeatureDefinition (FROZEN / COMPLETE by ADR-0026)
E04 FeatureArtifact (FROZEN / COMPLETE by ADR-0034)
E05 pure H01 kernel (RESOLVED / COMPLETE)
E06 canonical H01 integration (FROZEN / COMPLETE by ADR-0035; issue #83/PR #88)
```

[ADR-0035](../decisions/ADR-0035-h01-canonical-integration-v1.md) freezes the first concrete H01 Feature vertical:

- `quant_platform.application` owns D06/Feature cross-owner composition while H01 meaning remains Feature-owned;
- Diagonal Imbalance and Stacked Imbalance are independent `FeatureDefinition`s, both consuming the canonical FINAL D06 Footprint directly;
- both use `footprint.price_level@1`, `current()` support and `FINAL_ONLY` maturity;
- Diagonal owns `imbalance_ratio`; Stacked owns `imbalance_ratio` plus `stacked_min_levels`;
- one eligible FINAL Footprint bucket yields one structured per-level observation for each constituent feature;
- covered-empty buckets yield FINAL empty observations; missing support remains non-observation;
- canonical Diagonal and Stacked RECORD outputs are fixed, including `NaN -> null` normalization for absent-neighbor diagonal ratios;
- deterministic D06 exact-decimal -> E05 float calculation semantics are accepted for H01 v1 without weakening D06 evidence authority;
- E06 derives the true expected H01 observation universe required by ADR-0034;
- E04 binds exact immutable D06 result/binding evidence rather than a duplicated trade-lineage model;
- one `h01_imbalance@1` FeatureSetDefinition contains the two independent observables.

E06 therefore has no remaining semantic decision blocker. Its implementation is `COMPLETE` (issue #83/PR #88).

Do not activate Candle materialization merely because it is in DG-A. Do not create a generic provider/plugin framework. FeatureDefinition v1, FootprintDefinition v1, FeatureArtifact v1 and H01 canonical integration semantics are accepted authority under ADR-0026, ADR-0027, ADR-0034 and ADR-0035.

## DG-B — Historical / Live data convergence

This family also has separate repair and live branches.

### Repair branch (`A16`,`A10`,`B04` resolved) — RESOLVED for the accepted first vertical

B04 explicit non-contiguous coverage reads are frozen and complete under
[ADR-0029](../decisions/ADR-0029-non-contiguous-coverage-reads-v1.md). B04
reports requested support, eligible support, exact gaps and returned rows; it
does not decide repair triggering, retry, replacement revision or live cursor
policy.

A10 backfill/repair -- repair triggering (coverage-gap and invalid-revision
triggers), isolated candidate attempts, and one atomic compare-and-cutover
transaction reusing the A16/S14 seams -- is frozen and complete under
[ADR-0033](../decisions/ADR-0033-backfill-repair-v1.md). **Accepted known
limitations** (see ADR-0033 Consequences): coverage-trigger
*re-verification* is architecturally unsatisfiable inside A10's own package
boundary (attributable-evidence principle, same as ADR-0032/ADR-0034); candidate
identity binding for `coverage_start`/`coverage_end` and full provenance
persistence remain partial, deferred to a future pass.

Historical repair does not activate live-cursor semantics by default. Any
repair proposition beyond the accepted A10/A16/B04 foundation -- general
quality-report -> lifecycle mapping beyond the accepted first vertical,
duplicate resolution beyond what A10 already resolves -- remains live and
should only be activated by the selected repair slice actually needing it.

### Live acquisition branch (`A11`) — RESOLVED / IMPLEMENTED

A11 semantics for the selected first vertical are frozen under [ADR-0040](../decisions/ADR-0040-bybit-live-trades-v1.md).

Accepted A11 v1 decisions include:

- Bybit public linear `publicTrade.BTCUSDT` is the selected source;
- canonical mapping preserves provider trade time/ID/price/size/taker-side/cross-sequence while `receive_ts` remains null in v1 rather than inventing semantics;
- `TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)` and canonical total order remains `(exchange_ts, trade_id)`;
- provider `seq` is source evidence, not a gap-free resume cursor;
- same key + equivalent payload is idempotent; same key + conflicting payload fails closed;
- historical authority owns keys through the explicit historical cutover key and live authority owns keys after it;
- disconnect/reconnect uses only bounded provider evidence capable of proving continuity; inability to recover the last durable key produces explicit non-complete coverage, never fabricated completeness;
- transport is at-least-once while the canonical economic effect is idempotent.

A11 implementation is `COMPLETE`: PR #112 implements the source/application/certification path and real-provider smoke proof; PR #113 proves the same path on the target server through canonical materialization, S13/S14 publication/catalog and historical DataGateway read-back.

### Live gap remediation beyond bounded reconciliation — **DISPOSED, `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`**

Investigated in issue #110 (2026-09-24; full evidence in the issue's closing comment). A real, attributable Bybit historical archive exists for BTCUSDT (`public.bybit.com/trading/BTCUSDT/...csv.gz`), but at investigation time the archive directory stopped one day short of the live recent-trade window, so no current archive/recent-trade overlap could be compared, and no provider completeness attestation was found for any requested interval. The disposition below remains the accepted rule going forward; this is not a new decision, it is confirmation that the trigger condition has not yet been proven satisfiable and the gap stays explicit. A future issue may reopen this once archive/live overlap exists and completeness evidence can actually be produced for a bounded interval.

ADR-0040 deliberately does not invent a provider capability for interruptions that exceed the bounded recent-public-trades reconciliation window.

Trigger: after an A11 disconnect/restart, the last durable canonical `TradeKeyV1` cannot be recovered from the provider evidence available to the bounded reconciliation path, leaving an explicit non-complete interval.

Before such an interval may be declared filled/complete, resolve and prove:

- the authoritative source capable of reconstructing the exact missing interval (for example a provider historical/archive source only if its semantics and availability are actually observed and attributable);
- how that source binds to the existing Bybit `TradeKeyV1`, ordering and `trade-v1` semantics without inventing sequence continuity;
- how the existing A10 repair-intent/candidate/cutover path consumes that evidence;
- how the repaired interval is re-verified strongly enough to replace the prior `transport_interruption` / non-complete coverage evidence;
- what happens when no authoritative source can prove the missing interval (the gap must remain explicit; no guessed completion).

This disposed proposition does **not** prevent A11/K10 or the ADR-0043 server from running, recording an explicit gap and continuing with a new governed live segment. It **does** prevent the project from claiming that such a gap has been colmato/completed, and prevents any live-ingest closeout from claiming lossless continuity across that interval, until the missing-evidence proposition is actually satisfied.

Issue #110's disposition (above) and ADR-0044 are the current record. A negative or temporarily unprovable archive result must not be reinterpreted as proof that a gap is complete.

Do not solve this by treating missing `seq` values, absence of trades, wall-clock time or a finite local buffer as proof of completeness.

### Live consumer cursor (`B06`) — RESOLVED / IMPLEMENTED

B06 deterministic consumer resume/replay semantics are frozen under
[ADR-0047](../decisions/ADR-0047-live-consumer-cursor-v1.md).
ADR-0040 remains the acquisition/reconciliation authority; ADR-0047 is the
consumer-cursor authority.

PR #204 implements `DataGateway.live_stream()` with deterministic replay,
disconnect and explicit gap notification semantics, and PR #212 proves the
bounded Wave 6 live-consumer path. The disposed issue #110 long-gap rule is
unchanged: an interval beyond bounded reconciliation remains explicit
non-complete evidence until a future attributable repair path proves it.

## DG-C — Market-data depth

**Status:** L2 batch v1 is **CLOSED / COMPLETE on `implement/l2`** (#442, 2026-10-18), under ADR-0070 (#367) and Amendments 1 (#402 / PR #403) and 2 (#416 / PR #417). `A14` and `A14a`-`A14e` are COMPLETE; `A14f`/`A14g` remain held, FROZEN/MISSING. Promotion to `main` is a separate wave closeout PR after DG-K. The L1 branch (`A13`) stays `OPEN_BLOCKING`.

### L1 branch (`A13`)

Activate when a concrete L1 feed is selected. Resolve versioned L1 schema, identity, ordering, coverage and provenance from observed source evidence.

Selecting L1 does **not** activate L2.

### L2 branch (`A14`)

Selected by the owner on 2026-10-14 (#364); it does **not** require `A13`. Resolve snapshot/increment semantics, deterministic reconstruction ordering, gap handling and duplicate rules.

Exact L3/MBO semantics remain separately deferable until a real L3 feed exists.

PR #317 / issue #316 (operator-requested emergency slice, merged during Wave 8)
added an `l2-book-event-v1` schema, an `L2BookEvent` identity and pure venue
adapters. ADR-0070 ratifies the schema unchanged. Governance #442 now credits
`A14a` (#374 / PR #375), `A14b` (#376 / PR #379), `A14c` (#399 / PR #401),
`A14d` (#410 / PR #411, conformed by #416 / PR #417) and `A14e` (#407 / PR #408).
The real-day golden (#412 / PR #413, accounting fix #429 / PR #430) is accepted,
11/11 claims at `1cdd78f`; this closes A14 v1 within [the recorded bounds](../../SCOPE.md#closeout-result-issue-442).

**Owner decisions taken (2026-10-14, #364) — fixed inputs of the design gate:**

1. L2 is aggregated price levels only; no order-level, queue-position or L3/MBO semantics; no
   synthetic book from trades.
2. `A13` (L1) is not a prerequisite of `A14`; "L1 does not activate L2" is unchanged.
3. Two batches: `v1` is historical ingest, `v2` is live ingest.
4. Consumer read through DataGateway/Consumer API is in scope of `v1`.
5. `implement/l2` stays cut from `implement/dgk`; promotion follows DG-K closeout. The resulting
   by-hand reconciliation of `SCOPE.md` toward `main` is accepted maintainer debt, paid at the L2 merge.
6. Venue: Bybit only; no second venue.
7. (2026-10-16, #402) A source day with a `u` discontinuity is cut into per-run partitions
   `dt=YYYY-MM-DD/seg=NNN` (one coverage document per run and per gap), not split inside one partition:
   the frozen coverage fold cannot publish an internal `known_gap`.
8. (2026-10-17, #416 / PR #417, ADR-0070 Amendment 2) The UTC day owns each event by
   `exchange_ts` (`cts`), strictly congruent with `trade-v1`; source filenames are provenance.
   Contiguous out-of-day head/tail blocks are retained and evidenced, not published into the
   adjacent day automatically. Head/tail `known_gap` assertions stay explicit; only `A14f` may
   close an adjacent-file seam via predecessor-bound continuation; its held gate is unchanged.
9. (2026-10-17, #423 / PR #424, ADR-0049 Amendment 1) One redundant physical source copy
   may be disposed only while an independent verified full copy remains and all policy
   guards pass. The logical source stays UNIQUE_SOURCE with permanent retention; the last
   copy is never removable. #425 / PR #426 is recorded as an operational tool, outside the
   A14 atom list and without new K09 credit; the second copy is supplied by the operator.

**Proposals of PR #360, now settled by ADR-0070:** the ordering key `(exchange_ts, native_sequence,
native_update_id)` and provider `bybit-l2-exchange-ts-seq-v1` are **rejected** (order is numeric `seq`
alone, provider `bybit-l2-book-event-v1-seq-v1`); the physical path and Zstandard are **accepted** with
the typed columns of ADR-0070 §4; topics `orderbook.50`/`orderbook.500` are **rejected** (`orderbook.500`
is not a valid live topic; the effective depth is 200); the certification profile and check-suite names
are fixed in ADR-0070 §5.

**Question set for the L2 design gate (answered by `ADR-0070` §1-§8; atoms `A14a`-`A14f` now `FROZEN`):**

1. Which total order does the venue's native sequencing support, given that a Bybit snapshot resets the
   update id (`u`)? Is `exchange_ts` an admissible primary key?
2. Which snapshot/delta reconciliation, gap detection and resync rule makes an interval certifiable, and
   what state is carried across a partition (day) boundary?
3. Does the canonical layer store events (deltas and snapshots) or a reconstructed book, and which replay
   semantic does that fix?
4. Which Parquet layout, partition manifest and coverage manifest carry L2?
5. Which certification profile and check suite make an L2 partition eligible for publication?
6. Which DataGateway/Consumer API surface serves `l2` without changing the frozen J02 v1 contract or the
   existing representation contracts, and does it need an amendment to an accepted ADR?
7. How does `l2-book-event-v1` (#317) relate to the final contract: ratified as is, amended or superseded?
8. (`v2`) Which supervision lifecycle, backpressure and resync rule does live ingest follow, and how does
   it fit the live-ingest host (ADR-0043)?

**Resolution (ADR-0070, accepted 2026-10-15):**

1. Q1: order by numeric `seq` alone within one stream; `exchange_ts` = `cts` is a certified non-decreasing
   property, `provider_ts` = `ts`; `u` is the continuity signal because a snapshot resets it (§1).
2. Q2 (as amended): `u + 1` runs, one eligible partition per anchored run; gaps are separate
   `known_gap` documents, never a published `degraded` L2 partition. A `seq` or `exchange_ts`
   regression makes it ineligible; no synthetic snapshot; cross-day continuity is evidence, not
   carried state. Amendment 2 assigns by UTC `exchange_ts` and evidences source-edge exclusions.
3. Q3: the canonical layer stores source events, not a book; one derived exception, the declared top-N
   projection of §9 (§3).
4. Q4: `canonical/l2/bybit/BTCUSDT/l2-book-event-v1`, existing manifests and `l2` kind, no manifest
   evolution; one effective depth (200); sibling L2 modules (§4).
5. Q5 (as amended): profile and checks 1-9; in-day anchored runs are certified `eligible` and
   published `valid`; `degraded` is only the producer-side signal locating cuts (Amendment 1).
6. Q6: additive `l2_book_events@1` representation, byte-based result bound, J02 v1 unchanged, no accepted-ADR
   amendment (§6).
7. Q7: `l2-book-event-v1` ratified unchanged; depth 200 is the only effective depth; `ob500` history enters
   only by the §9 projection (`A14g`) (§7, §9).
8. Q8 (`v2`): reuse the live-ingest host, one `orderbook.200` subscription, resync waits for the in-band
   snapshot, no repair path (§8).

**Measured evidence, bounded:** E1's original two 2 MiB prefixes remain historical evidence. The
accepted golden (#430) measured one full `2025-12-01` `ob200` file: 863,929 source
events = 863,916 stored + 13 excluded post-midnight events; one published valid partition,
5 byte-identical files, consumer read 863,916 and bounded window 600 = 600. The file head
declares `[2025-12-01T00:00:00Z, 2025-12-01T00:00:01.112Z)` a `known_gap`.
E2-E4: no provider restart, mid-day re-snapshot, absent-level delete or crossed state on this
one day; no restart-survival or population guarantee follows. One adjacent-file seam was
observed, using only the first 3 MiB of the next file. E5 ingest-cost observations are in
trigger 4 below; the original ADR-0070 E5 (`ob500` projection parity) remains open for A14g.
A `cts` regression along `seq` still fails closed; no threshold is relaxed.

**Amendment 1 (ADR-0070, #402, 2026-10-16):** supersedes the §2/§4/§5 in-partition `known_gap` split and the
`degraded` publication state. A run is a maximal sequence from an in-band snapshot to the delta that opens a
discontinuity; each run is one partition `dt=YYYY-MM-DD/seg=NNN` certified `eligible`; events in an unknown
interval or before the first snapshot are excluded from the canonical layer, kept in the retained source archive
and counted in the gap evidence; coverage is one document per run (`complete`) and per gap (`known_gap`), 1 µs
unit; refusals `discontinuity_not_representable`, `no_anchored_run`, `event_outside_day`,
`day_already_published_differently`. Read those original boundary/refusal descriptions through
Amendment 2 below. The live day-roll head mechanics remain deferred to the held `A14f` issue.

**Amendment 2 (ADR-0070, #416 / PR #417, 2026-10-17):** UTC-day ownership is exactly
`[D 00:00:00Z, D+1 00:00:00Z)` by `exchange_ts`, congruent with `trade-v1`. Only
contiguous file-head/tail out-of-day blocks are excluded and evidenced; regression or
interior out-of-day placement still refuses. The first anchored timestamp starts complete
coverage, with a leading `known_gap`; a clean final run reaches midnight only with valid
tail evidence, otherwise strict `ceil_us(last event)` ends it and a trailing gap remains.
Coverage resolution stays 1 µs; no in-day duplicate exception. The approximately 1.1 s
measured head gap closes only through A14f's separately scoped adjacent-tail continuation,
never by interpolation or a synthetic snapshot. A different second seam is a stop condition.

**ADR-0049 Amendment 1 (#423 / PR #424):** pure source-copy disposal is separate from
K09 retention evaluation. Fresh survivor/canonical-byte verification, K06 PROTECTED,
attributable authority/time, durable evidence and a persisted PERMITTED audit precede
removal. The last source copy is never removable; unknown provider retention is no
reacquisition guarantee. PR #426 is credited as an operational tool only (T6).

### L2 v1 reopening triggers (#442)

Each condition below is **NOT AUTHORIZED** by #442. A trigger identifies evidence or a
separate scoped decision; recording it does not open an issue, relax a gate or start work.

| ID | Proposition still bounded/open | Named reopening condition | Authorization |
|---|---|---|---|
| T1 — E2 provider restart | Survival of `seq` across a provider restart is unproven; `sequence_regression` stays fail-closed. | A measured day with a `u = 1` snapshot or a `seq` regression, or any population-level discontinuity-rate claim; return to the ADR-0070 decision owner. | NOT AUTHORIZED |
| T2 — More days/seams | One full day and one rolling-file seam (`2025-12-01` -> `2025-12-02`, first 3 MiB of the next file); closing/opening snapshots equal in `seq`, `u`, `cts`, `ts` and all levels. No all-seams claim. | A second seam with different closing/opening snapshots: stop and report to the ADR-0070 owner per #416, without merging the records. | NOT AUTHORIZED |
| T3 — File-head gap | The measured head gap is 1.112 s (next-file prefix 1.113 s), not a fixed tolerance or a promise about every day. Gap-free L2 across UTC midnight is unavailable. | A14f start gate, or a consumer needing a gap-free midnight read: only A14f's separately scoped predecessor-linked continuation using the adjacent file tail may close it. | NOT AUTHORIZED |
| T4 — E5 ingest cost | One run on one Windows host: 4,310.6 s wall (279.4 s measurement), peak working set 9,080 MB, peak pagefile 9,935 MB. Observations, no memory/throughput bound. Original E5 projection parity stays open under A14g. | A multi-day backfill plan or a host memory budget requires a separately approved measurement/capacity plan; projection parity requires A14g's own gate. | NOT AUTHORIZED |
| T5 — Provider retention | Bybit historical `ob200` retention is unknown; ADR-0049 Amendment 1 never permits removing the last full source copy. | Provider-retention evidence plus an accountable assertion and a superseding ADR before a reacquirable-source disposition. | NOT AUTHORIZED |
| T6 — Disposal tooling registration | #425 / PR #426 is credited here as an **operational tool**, not an A14 atom or new K09 credit. `operations.source_copy_disposal`, the application service and `tools/dispose_source_copy.py` implement ADR-0049 Amendment 1; its PostgreSQL proof is not wired into CI. | A request to add an atom, give additional K09 credit or wire that proof into CI needs its own governance/implementation mandate. | NOT AUTHORIZED |
| T7 — Trade-side disposal | `tools/sync_symbol.py`, named by closed #415, does not exist; no trade-archive disposal is specified or implemented. | A concrete trade-side disposal request requires its own scoped design/implementation issue; the L2 tool proves no trade-side capability. | NOT AUTHORIZED |
| T8 — Proof environment | Disposable PostgreSQL on Windows with a placeholder storage root resolved by an injected path resolver; the catalog requires POSIX absolute `abs_path`. No production-topology proof. | A production-like run on a POSIX host, under a separately scoped operator plan, before claiming that environment proven. | NOT AUTHORIZED |

**Not claimed:** no live L2 (A14f), no `ob500` or other depth (A14g), no population-level
continuity or provider-restart guarantee, no gap-free UTC-midnight L2. The independent
second archive copy is an operator action, not a platform guarantee. Disposal tooling is
outside the A14 closure. No new runtime or promotion is authorized by #442.

**Requirements carried by the completed v1 implementations (not new decisions):** `A14b` maps `uint64` columns to the
string-typed schema with an exact round-trip; `A14e` exposes per-partition origin and must not assume
`u + 1` on projected rows; `A14d` reads a streamed zip with a data descriptor.

## DG-E — Validation semantics — RESOLVED / IMPLEMENTED

DG-E has no remaining open semantic or implementation branch through F07/F08.

### Validation / labeling branch (`F06`,`F07`) — COMPLETE

F06 availability/purge/embargo is frozen and complete under
[ADR-0031](../decisions/ADR-0031-availability-purge-embargo-v1.md).

F07 label/censoring/lockbox semantics are frozen under
[ADR-0036](../decisions/ADR-0036-labels-censoring-lockbox-v1.md) (issue #95), and the bounded runtime is integrated via issue #96 / PR #99.

Accepted F07 v1 decisions include:

- outcome-derived labels only; policy-derived labels wait for explicit Strategy/Execution authority;
- only `COMPLETE` F03 Outcomes may produce a label value; source-censored and insufficient-coverage Outcomes remain explicit no-value states;
- exact identity-value and ordered exact-rational threshold transforms only;
- label causal availability equals source Outcome causal availability;
- target support includes the consumed F03 boundary at `horizon_end` and is projected into F06 without shrinking that endpoint;
- training targets are fold-completion dependencies; test/lockbox targets are evaluation outputs, not decision-time inputs;
- lockbox v1 is one terminal holdout interval with explicit hidden-evaluation isolation and irreversible reveal semantics;
- F07 freezes semantic isolation, not physical ACL/vault infrastructure;
- Validation consumes a narrow Validation-owned projection of F03 evidence rather than importing/redefining Research runtime semantics.

### DSR/PBO branch (`F08`) — COMPLETE

F08 robust-comparison semantics are frozen under
[ADR-0037](../decisions/ADR-0037-dsr-pbo-robust-comparison-v1.md) (issue #97), and the bounded Validation-owned runtime is integrated via issue #98 / PR #104.

Accepted F08 v1 decisions include:

- one complete same-frequency excess-return panel over a declared comparable trial population;
- non-annualized canonical Sharpe using sample standard deviation (`ddof=1`);
- deterministic raw standardized moment formulas for DSR;
- DSR v1 is the location-only `DSR-L` variant; DSR-LS/full-search variants are future extensions;
- effective trial count `K_eff` is explicit caller evidence, not estimated by F08;
- search adjustment uses the Bailey/López de Prado expected-maximum location approximation and PSR formula;
- sampling assumption is explicitly `IID_V1`; no serial-correlation correction is implied;
- PBO v1 is full CSCV with all symmetric half-block combinations, deterministic IS selection, OOS ranking, average exact-tie rank and strict `lambda < 0` overfit event;
- non-evaluable required Sharpe values fail the complete metric rather than silently dropping a trial/split;
- F05 walk-forward folds are not reinterpreted as CSCV partitions;
- binary64 numerical policy and pinned DSR/PBO reference vectors are part of the contract;
- Validation remains the runtime owner and consumes opaque cross-owner evidence without loosening the package DAG.

DG-E is therefore complete through F08. No future scope should reopen these semantics absent contradictory accepted authority.

## DG-G — Experiment / RL / Jobs

Independent branches:

### Supervised Experiment / ML branch (`I03`,`I04`,`I05`) — RESOLVED / IMPLEMENTED

Wave 5 closes the supervised branch of DG-G without resolving RL or job-runtime
semantics:

- `I04` Supervised input / selection is complete via PR #177 / issue #172:
  supervised projections reuse Validation-owned availability, purge, embargo,
  label and lockbox evidence and fail closed on leakage.
- `I03` Trial accounting / comparison is complete via PR #178 / issue #171:
  comparable trial population identity, resume/idempotency and metric
  comparison are expressed through the canonical Experiment System.
- `I05` Supervised training / evaluation is complete via PR #179 / issue #173:
  deterministic fold-safe baseline training emits model, prediction and metric
  artifact identities.
- PR #180 / issue #174 records the Golden supervised E2E proof in
  `../integration/WAVE5_GOLDEN_E2E_SUPERVISED_ML.md`, with stable projection,
  run, metric and artifact identities across independent runs.

This does **not** freeze Strategic RL (`I06`), Execution RL (`I07`), Job
runtime (`J03`), paper/shadow trading (`J07`) or live product mode (`J08`).

### Experiment persistence (`I02`)

Resolve one canonical Study/Trial/Run/Artifact persistence model. Restart/query/resume must preserve identity and idempotency; avoid competing persistence stores/models.

### Strategic RL (`I06`)

Freeze StrategicState/StrategicAction/StrategicReward only when the strategic-RL runtime is selected. It must not absorb execution-control variables/objectives.

### Execution RL (`I07`)

Freeze ExecutionState/ExecutionAction/ExecutionReward only when execution RL is selected. It must remain structurally separate from the strategic task.

### Job runtime (`J03`) — FROZEN (semantics), IMPLEMENTED (durable record)

ADR-0062 freezes `J03`'s decision: submission identity (deterministic `job_id`
fingerprint), durable lifecycle (`ADMITTED` through `RECOVERY_REQUIRED`),
attempt/retry-idempotency rules, result/artifact reference requirements, and
failure semantics for a server-local, Application-owned Job. It does not infer
transport/process topology or define a remote worker — see `DG-J` G2 below.
PR #307 (Wave 8) implements the durable record and lifecycle; handler
dispatch, a worker process and a PostgreSQL store are not implemented and are
carried to DG-K Process Topology v1 (#318).

## DG-H — Operational safety

This family is progressive and non-monolithic.

### Runtime identity (`K02`) — RESOLVED / IMPLEMENTED for Live Ingest v1

Live-ingest runtime identity semantics are frozen under [ADR-0041](../decisions/ADR-0041-live-ingest-runtime-identity-v1.md).

PR #113 proves the real target deployment under the existing non-root `mkt-transform` identity and minimum catalog role, and verifies physical storage topology plus backup-authority separation. The real-server proof reused the existing governed identity boundary and applied only the explicitly authorized ACL correction needed to remove ingest write authority from the independent K08 recovery copy.

For public Bybit trades v1 there is no provider secret. Filesystem/database authority remains restricted to ingest/publication/checkpoint responsibilities; normal ingest authority does not imply backup-destruction authority.

### Observability (`K03`)

The selected ADR-0043 live-ingest server uses the K03 operational signal seam for minimum lifecycle/health/provenance/failure evidence. Broader observability technology, SLOs and alerting remain local until a future runtime/product scope selects them.

### Pressure (`K05`)

ADR-0043 uses the accepted K05 pressure policy for the selected live-ingest server's write/admission decisions. Future pressure thresholds or safe actions beyond this path remain scoped to the selecting runtime. No silent deletion.

### Source protection (`K06`) — RESOLVED

Protection authority and reconstruction guarantees are frozen and complete
under [ADR-0032](../decisions/ADR-0032-raw-source-protection-v1.md): a pure
protection-identity/assessment seam (`ProtectionState`,
`ProtectionAssessment`, `assess_protection()`, `SafetyRelevanceAssertion`,
`ProtectionWriteAuthorization`) that never crawls a filesystem or authorizes
deletion itself. K07 and K08 may depend on its output. **Accepted known
limitation:** `ProtectionObligationEvidence`'s obligation kind is not yet
type-restricted to K06-owned concerns, tracked for a future narrowing pass.

### Tier relocation (`K07`) — RESOLVED / IMPLEMENTED

K07 crash-safe old-or-new-valid placement semantics are frozen under
[ADR-0048](../decisions/ADR-0048-storage-tier-relocation-v1.md). This is a
sibling downstream use of source protection, not a prerequisite of
backup/restore.

PR #209 implements storage tier relocation, and PR #212 proves the bounded Wave
6 storage-lifecycle path without consumer-visible disappearance.

### Backup/restore (`K08`) — RESOLVED / IMPLEMENTED

K08 semantics are frozen under [ADR-0039](../decisions/ADR-0039-backup-restore-v1.md): protect finalized published canonical state as an identity-bound recovery set; prove restore from storage independent of the tested primary boundary into an empty isolated target; reproduce canonical identities/coverage/catalog resolution and historical DataGateway-visible data. RPO v1 is the last finalized recovery set; no numeric RTO/HA/off-site claim is implied.

PR #111 implements the identity-bound recovery set, export/restore and isolated Postgres/DataGateway proof. PR #113 supplies the previously pending deployment-independence evidence on the real topology and removes ingest write authority over the independent recovery tier. K08 is therefore implementation `COMPLETE` for v1.

### Retention/deletion (`K09`) — RESOLVED / IMPLEMENTED

K09 retention/deletion authority is frozen under
[ADR-0049](../decisions/ADR-0049-retention-deletion-authority-v1.md). Restore
proof must precede deletion authority. No protected or sole recoverable evidence
may be deleted.

PR #211 implements governed deletion, and PR #212 proves the bounded Wave 6
storage-lifecycle path. DG-H has no currently identified open atom after Wave 6
closeout.

### Checkpoint/recovery (`K10`) — RESOLVED / IMPLEMENTED

K10 semantics are frozen under [ADR-0042](../decisions/ADR-0042-live-ingest-checkpoint-recovery-v1.md).

A checkpoint means the last canonical progress point already durably published; publication must become durable before checkpoint advance. Replay after crash is allowed and relies on ADR-0040 idempotent deduplication. Invalid checkpoints fail closed. Restart uses bounded provider reconciliation and must record an explicit gap when continuity cannot be proven; it never guesses a cursor/completeness state.

PR #114 implements persisted checkpoint state, binding/monotonicity/refusal semantics, A11 restart composition and the required hermetic proof matrix. PR #122 (issues #109, #121) closes the previously pending `REAL_RESTART_PROOF_PENDING` handoff: under the K02 identity, Process A publishes and persists a checkpoint, Process B restarts, runs bounded reconciliation, durably publishes the accepted reconciliation records under explicit `reconciliation` coverage, and only then advances the checkpoint to a new monotonic generation (`K10_REAL_RESTART_PROOF: PASS`, run `k10-restart-fixed-fast-20260924T193502Z`). K10 is therefore implementation `COMPLETE` for v1.

### Live ingest server composition — RESOLVED / IMPLEMENTED

[ADR-0043](../decisions/ADR-0043-live-ingest-server-composition-v1.md)
freezes the first production live-ingest server composition as a bounded
Application-owned loop over the accepted A11/K10/K03/K05 authorities, exposed
through `tools/live_ingest_server.py`. It is not a generic scheduler, job
runtime, live product mode or B06 consumer cursor.

The supervised operating path is documented in
`../engineering/LIVE_INGEST_SERVER_DEPLOYMENT.md` and proven on the target host
in `../integration/LIPR06A_SUPERVISED_LIVE_INGEST_DEPLOYMENT.md`: systemd
starts the server under `mkt-transform:marketdata`, the server publishes and
advances checkpoints, stops cleanly, restarts, reconciles from the durable
checkpoint and passes DataGateway readback.

### Live ingest long-gap orchestration — RESOLVED / IMPLEMENTED

[ADR-0044](../decisions/ADR-0044-live-ingest-long-gap-remediation-v1.md)
freezes the v1 path for gaps beyond bounded provider reconciliation: record an
explicit non-complete gap, retain source/restart evidence, continue only through
a governed segment when allowed, and refuse to mark the missing interval
complete without future attributable repair evidence. This closes the selected
production-readiness path while preserving issue #110's
`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` disposition.

## DG-J — Remote Service Topology (Wave 8 design gates) — RESOLVED / IMPLEMENTED

All six gates reached a disposition on 2026-10-04 (issues #282-#287, parent
#281, reconciled by issue #288). Their nine atoms were implemented in Wave 8
and promoted to `main` (PR #322, #326); the Wave 8 closeout (issue #327)
records the exact bound of each credit in `CAPABILITY_DAG.md` and `SCOPE.md`.
No Wave 8 Golden proof artifact is claimed.

### G1 — Authentication, TLS and authorization for non-loopback J02 — ACCEPTED (ADR-0063)

Resolved: non-loopback J02 is WSS with TLS 1.3+ and mandatory client-certificate
mTLS or it refuses to start; the authenticated credential is the exact,
versioned certificate fingerprint mapped to one `principal_id` and an explicit
scope set; the only currently defined scope is `j02.market_data.read`
(read-only, no storage/catalog/Job/admin access). Freezes `J09`. Anchors:
ADR-0050 Amendment 1 §3; ADR-0057 §4.

### G2 — `J03` job runtime — ACCEPTED (ADR-0062)

Resolved: a Job is a durable, Application-composed admission record
(deterministic `job_id` fingerprint over operation kind/handler/admitted
request/input identities); lifecycle `ADMITTED -> QUEUED -> RUNNING ->
SUCCEEDED|FAILED|CANCELLED`, with `RECOVERY_REQUIRED` on restart instead of
inferred liveness; automatic retry is prohibited, explicit retry requires
proven effect-safety. Activates the existing DG-G `J03` decision as a Wave 8
prerequisite; it is the same atom, not a new one. Dependencies `C03`/`I02` are
already complete.

### G3 — Placement contract: data-local vs. consumer-local, and the wire boundary — ACCEPTED (ADR-0064)

Resolved: the existing ADR-0057 deck is the sole Wave 8 consumer compute
surface — no separate third "consumer machine" exists or is introduced. `K12`
is a sealed, deterministic admitted-input manifest (`admission_id =
admitted-input-v1:sha256(canonical_payload_v1)`, excluding paths/catalog
UUIDs). `K13` resolves that admission and verifies every declared input/output
digest before registering a governed Experiment/Artifact result; a mismatch
refuses the entire bundle with no mutation. Freezes `K12`, `K13`. Anchors:
ADR-0057 §1-§3, §6.

### G4 — Consumer-API-equivalent seams for Strategy, Replay, Validation, Training — ACCEPTED, partial (ADR-0065)

Resolved for three of four domains: `J10` (`strategy-compose-v1` over
`compose_decision`), `J12` (finite Validation operations: fold building,
candidate classification, DSR, PBO), and `J13`
(`supervised-train-evaluate-v1` over `train_evaluate_supervised_baseline`) are
each a thin, versioned application seam carried over existing J02. **Replay
(`J11`) is explicitly deferred, not frozen**: `HistoricalReplayRuntime.run()`
requires an injected `feature_provider` callable with no accepted
identity-bearing, serializable, server-resolved definition; exposing it as a
path/module name/callable would invent a new semantic authority. Trigger for
revisiting: a future ADR that defines that semantic feature/provider reference
and whether the resulting operation is synchronous or J03-admitted. Anchors:
`ROADMAP.md` Wave 8; the existing `C02`/`C03` pattern.

Implementation note (Wave 8 closeout, #327): `J10`/`J12`/`J13` are implemented
as in-process application seams (PR #308, #312, #315). ADR-0065 defines no J02
wire message or remote scope for them, and the owner decided on 2026-10-08
that this carriage belongs to DG-K Process Topology v1 (atom P05, #318), not
to Wave 8. "Carried over existing J02" above is the design intent, not a
delivered capability.

### G5 — Transport evolution for larger/streamed/live results — ACCEPTED (ADR-0066)

Resolved: J02 v1 remains unchanged; `j14-framed-result-v1` is an additive
message family that carries only a complete, already-produced finite
application result, split into digest-verified chunks (RFC 8785 canonical
JSON, per-chunk and per-transfer SHA-256), with transfer-local (not
application-level) resume. No live family is defined yet — a future owner must
first accept the corresponding Consumer request/event/error/ordering/resume
semantics before any `j14-live-*` family exists. Freezes `J14`. Anchors:
ADR-0050 Amendment 1 §1-§2; ADR-0047.

### G6 — Omega client contract — ACCEPTED (ADR-0067)

Resolved: Omega's J15 remote client talks only to J02's wire contract
(`j02-request-v1`/`j02-response-v1`); the public Python API /
`PUBLIC_PYTHON_API.md` is never a remote-client fallback and must not be mixed
with or substituted for a J02 session. Compatibility is a wire-family
commitment (`wire_incompatible` fails closed on any mismatch, never silently
downgrades). The first and only capability is `j02.market_data.read`. **A
remote implementation of `J15` is explicitly blocked until `J09` is
implemented** — this ADR authorizes neither that server work nor an Omega
adapter by itself. Freezes `J15`. Anchors: ADR-0055; `PUBLIC_PYTHON_API.md`.

## DG-K — Process Topology v1 — RESOLVED and IMPLEMENTED within recorded bounds (ADR-0068, ADR-0069)

**Closed by operator closeout #381 (2026-10-16):** `P01`-`P06` are implemented and merged into
`implement/dgk` (PR #344-#353, #355, #356, #359, #361, #366, #373, #380). Amendments that landed
with them: ADR-0064 Amendment 1 (delivery attempt identity), ADR-0066 Amendment 1 (family
`j02-admitted-input-v2`), ADR-0068 Amendment 1 (worker exclusion bound to the process lifetime),
ADR-0069 Amendment 1 (attempt-attributed delivery, public error projection) and Amendment 2 (J13
reconstruction evidence, pre-decode `register` grant). Not decided and not implemented by DG-K:
composition of the families in an executable, the production K12 authority, K13 composition,
a deck-side bundle producer, credential issuance, deployment, a data-local J03 handler and an
end-to-end proof. These belong to a follow-on scope registered by its own governance issue.

**Resolved 2026-10-10 by design gate #319:** ADR-0068 (process topology v1)
and ADR-0069 (J02 carriage of consumer seams and deck handoff v1) are
`ACCEPTED`. `P01`-`P04b` are `FROZEN` under ADR-0068 and `P05a`, `P05b`, `P06`
under ADR-0069; implementation is complete within recorded bounds (closeout #381,
see above). Answers in summary: (1) five units: ingest, platform API, exactly one
J03 worker, PostgreSQL, deck compute; (2) every domain package stays an
in-process library; (3) a `runtime` schema with per-unit login roles, no deck
database login; (4) J03/K12/K13 stores move to `runtime` and K13 registration
becomes one transaction with the Experiment write; (5) four named byte
profiles reproduce today's identity bytes, RFC 8785 only for J14 and new
contracts, and server units run one commit; (6) per-host import rules for
`tools/`; (7) additive J02 message families with one scope each, synchronous
`J10`/`J12`/`J13` within unchanged budgets and J13 requests bounded by 16 MiB;
(8) a second worker needs recorded queue-wait evidence and an ADR-0062 §4
amendment first; (9) deck-initiated K12 delivery and K13 submission with a
`feature-provider-v1` code identity in replay bundles. Over-budget PBO and
training are deck work, which answers the J03 dispatch question ADR-0065
Amendments 1 and 2 had deferred. The text below is the registration record
(#318) the gate answered.

Registered by governance issue #318 (2026-10-10) after the Wave 8 closeout
(#327). ADR-0024 §6 leaves open whether any component becomes an
independently deployed service, where executable hosts live and the process
topology. The maintainer's direction (2026-10-07, refined 2026-10-10) is a
modular monolith deployed as a small fixed set of processes, not one service
per domain package, with heavy compute on the deck and the server kept as free
as possible. This section registers that direction as an open gate; it decides
no topology. The single design-gate issue is #319 (ADR-0068 process topology
v1; ADR-0069 J02 carriage of consumer seams and deck handoff v1). It gates the
candidate atoms `P01`, `P02`, `P03`, `P04a`, `P04b`, `P05a`, `P05b` and `P06`
in `CAPABILITY_DAG.md` (names provisional).

Owner decisions, fixed inputs to the gate (not open questions):

1. `P01` preserves every existing identity hash: one module with named,
   versioned byte profiles reproducing today's three profiles
   (sorted/compact/ASCII, sorted/compact/UTF-8 for frozen data-plane
   contracts, sorted with default separators); RFC 8785 is one profile, for
   `J14` and new contracts only. Changing existing identity bytes would need a
   separate ADR-0018 contract evolution.
2. `P03` keeps executable hosts in `tools/` (ADR-0043 names
   `tools/live_ingest_server.py`); no `apps/` move and no ADR-0043/ADR-0050
   amendment.
3. `P04` v1 runs exactly one J03 worker process: `recover_after_restart`
   (`application/durable_jobs.py:244-264`) is global, and per-process attempt
   ownership would need an ADR-0062 §4 amendment. More than one worker waits
   for a recorded evidence trigger.
4. `P05` (J02 carriage of `J10`/`J12`/`J13`) belongs to DG-K, not Wave 8
   (2026-10-08).
5. The server stays as free as possible (2026-10-10): server-side J03 handlers
   are data-local only (feature/representation materialization, historical
   imports, catalog maintenance); replay, sweeps, training and large PBO runs
   belong on the deck, on the deck's own initiative. The server never
   delegates or pushes jobs to the deck or any other machine, and no remote
   worker pulls from the server queue (ADR-0062 §1/§6, ADR-0057 §6).
6. `J11` is not planned (2026-10-10): replay runs only on the deck over
   `K12`-admitted inputs, with results returned through `K13`. No `J11` design
   gate follows DG-K (see `J11` below).

Question set for #319:

1. Which deployable units exist (proposed: ingest per venue/stream, platform
   API, one J03 worker, deck compute, PostgreSQL; live/paper execution
   deferred to Wave 10)?
2. Which domain packages remain in-process libraries in every unit, and why
   (replay calls `feature_provider` per tick,
   `src/quant_platform/replay/__init__.py:311,370`)?
3. Which PostgreSQL schema and DB role each unit owns (extending
   `db/init/002_roles.sh`)?
4. How `J03`/`K12`/`K13` state moves to a `runtime` schema and how `K13`
   registration becomes one transaction with the Experiment write (today
   PostgreSQL then SQLite, `application/governed_result_import.py:268-288`)?
5. Which byte profile each existing identity site uses, how `P01` proves
   parity, and how code identity is checked between units (ADR-0057 §2)?
6. How `tests/test_package_boundaries_v1.py` extends to per-host rules for
   `tools/`, with `clients/*` kept separate?
7. Which J02 wire messages and remote scopes carry `J10`/`J12`/`J13`
   (`P05a`/`P05b`), which operations are synchronous versus admitted through
   `J03`, and how a `J13` request carrying a full `SupervisedProjection` stays
   within the J02 16 MiB message bound (ADR-0050 §2; `J14` frames results
   only, ADR-0066)?
8. Which evidence trigger re-opens more than one J03 worker (ADR-0062 §3/§4)?
9. Which wire messages, scopes and deck-side client carry `K12` delivery and
   `K13` submission (`P06`), and which feature-provider identity the `K13`
   bundle must carry?

Valid closes: accepted ADRs, or an explicit "keep single process" disposition
with a recorded evidence trigger. Stop conditions: the gate stops and reports
if it would weaken ADR-0057's server authority, ADR-0062's no-automatic-retry
rule or attempt model, ADR-0064's sealed-input identity, ADR-0043's host
placement or the frozen Consumer API, or change any existing identity. Out of
this gate: L2 acquisition semantics (`A13`-`A15`), `J11`, `D05` bar-input
replay, and Omega remote use of `P05`/`P06` (needs its own ADR-0067
amendment).

## DG-K2 — Server/deck deployment composition v1 — RESOLVED (ADR-0072 ACCEPTED), CLOSED (#409)

**Closed by operator closeout #409 (2026-10-17).** `S01`-`S07` are implemented and merged into `implement/dgk2` within the bounds
recorded in `CAPABILITY_DAG.md`. ADR-0072 Amendment 1 (issue #431) raised the delivery bounds to 512 MiB per file, 1 GiB
and 16 files per admission after the real catalog showed that 719 of 2 116 daily partitions exceeded the original 32 MiB
member bound; the bounds quoted under "Decisions taken" below are the ones as accepted, before that amendment. Recorded
triggers (none authorized by the closeout) are in `SCOPE.md` under "Triggers recorded at closeout".

**Resolved 2026-10-16.** The design gate #383 produced ADR-0072, `ACCEPTED` by the maintainer with the clarification
of its section 11 (issue #387, PR #388). Governance issue #389 authorizes `S01`-`S07` in `CAPABILITY_DAG.md` (`FROZEN`,
implementation `MISSING`); `S08` stays `OPEN_DEFERABLE` on its trigger. The scope id `DG-K2` and the atom names are
confirmed; the integration branch name `implement/dgk2` is proposed for the maintainer's confirmation.

Decisions taken (the question set below is kept as the record of what was asked; each question is answered by the ADR
section named here): 1 -> section 1 (dataset selector only, `artifact_inputs_unavailable`, no time-based expiry, bounds
16 / 32 MiB / 64 MiB with a 96 MiB transfer store); 2 -> section 2 (six roles, checksum content identity); 3 -> section 3
(opt-in families, fail-closed startup); 4 -> section 4 (`openssl` scripts, restart-based rotation; `S04` stays in
scope); 5 -> section 5 (the deck is pinned to the server's commit, resolving the ADR-0068 section 8 / `K13` conflict in
`K13`'s direction) and section 11 (how a strategy is tested); 6 -> section 6; 7 -> section 7 (hermetic plus supervised
proof); 8 -> section 8 (Omega out of scope); 9 -> section 9 (an empty handler registry is accepted; `S08` deferable).

As originally registered:

Registered by governance issue #382 (2026-10-16) after the DG-K closeout (#381). The single design-gate issue was
#383 (one ADR). It gated the candidate atoms `S01`-`S07` in `CAPABILITY_DAG.md` (`S08`, the first
data-local J03 handlers, is `OPEN_DEFERABLE` on an evidence trigger).

DG-K delivered the J02 families, the `runtime` stores, the single worker host and the deck protocol client, but no
executable composes them and nothing proves them on the real topology: `tools/api_server.py` serves the J02 v1
market-data message only and has no TLS/mTLS options; the K12 server seams (`resolve`, `members`, `read_member`) have
only test authorities; K13 is composed in no host; no component produces a `GovernedResultBundleV1` for a deck
workload; certificate issuance, secret storage and deployment were left out of scope by ADR-0063; the J03 handler
registry ships empty; and the acceptance tests never put PostgreSQL behind the WSS/mTLS listener.

Fixed inputs (decided elsewhere; not open questions): ADR-0057 (two surfaces, the deck acts on its own initiative, the
server never pushes work), ADR-0063 (non-loopback is WSS with mTLS, fingerprint-to-principal mapping, no credential
service, IAM or bearer tokens), ADR-0064 (sealed manifest, server authority, no path or storage mount on the deck),
ADR-0068 (five units, hosts in `tools/`, one worker, code identity between units) and ADR-0069 with its amendments,
and `J11` not planned.

Question set for #383:

1. Which component resolves a semantic request to a sealed manifest, its ordered members, lengths and bytes (K12
   `resolve`/`members`/`read_member`); how partitions and content hashes are aligned; how accepted artifact identities
   are resolved; the closed sets of accepted operation and profile identities; the expiry policy values
   (`[DA CONFERMARE]` in #357); the byte and member bounds.
2. Which output roles exist, with which `ResultOutputContractV1` (including the replay roles that require
   `replay_feature_provider`); staging bounds and retention; which runtime role registers.
3. The configuration surface of `tools/api_server.py` (TLS/mTLS material, principals file, enabled families, J14 store
   bounds, evidence log), the fail-closed startup checks and the default composition.
4. How certificates are issued, rotated and revoked and where the fingerprint-to-principal mapping and secrets live,
   within ADR-0063 §2.
5. Where the producer of `GovernedResultBundleV1` lives given that `clients/deck/` must not import
   `quant_platform`; how the deck code identity and the feature-provider identity are assembled; how a workload is
   pinned to a commit.
6. systemd units and environment examples for the API and the worker, the runbook and promotion updates, and the
   compatibility rule between server and deck (wire family version, never package version).
7. What "ready to split" means: the real-topology proof (PostgreSQL + API + worker + deck client over WSS/mTLS), its
   pinned dataset and its fault cases (lost response, expired admission, concurrent attempts, a refused bundle,
   restart).
8. Omega: out of scope here, or the trigger for its own ADR-0067 amendment gate.
9. Whether any data-local J03 handler is required for the split (`S08`), or the empty registry is an accepted state
   until the maintainer names one.

Valid closes: an `ACCEPTED` ADR in which every closed set, bound, name and default is stated (no `[TBD]`, no
`[DA CONFERMARE]`), or an explicit recorded deferral with an evidence trigger. Stop conditions: the gate stops and
reports if it would require a credential service, a host outside `tools/`, a binary transport family, an admission path
that bypasses a sealed manifest, a canonical storage mount on the deck or a way for the server to push work to the
deck, or weaken ADR-0057, ADR-0063, ADR-0064, ADR-0068 or ADR-0069. Out of this gate: Omega adoption, more than one
J03 worker, a durable J14 transfer store, `J11`, L2 acquisition and Wave 9/10.

## DG-M — Canonical feature identity for deck results v1 — RESOLVED (ADR-0073 `ACCEPTED`, #457), CLOSED (#475)

Registered by #453, designed by #454 (ADR-0073), accepted on 2026-10-18 and reconciled by #458. All eight questions are answered
in the ADR: one canonical feature (`order_flow.failed_aggression@1`) and no server-known event (§1); a new additive `bar-flow-v1`
representation, with the candle, footprint and H01 identities pinned (§2); FINAL bars only, causal floor `bucket_end`, no warm-up
(§3); an exact, division-free rule with defaults 0.3 and 0.5 (§4); a closed in-code registry, an optional `definitions` field of
`admit` and the sealed `definition_identities` (§5); K13 unchanged and a deck builder that must declare the definitions (§6);
`alpha-research` consumes the canonical definitions and keeps hypotheses, event bindings and outcomes local (§7); the four facts
that show the server passing feature identity (§8). Implementation of `M01`, `M02`, `M04`, `M05` was authorized by #458 and is
merged into `implement/fid` (PR #466, #468, #473, #474; closeout #475); `M03` is superseded. ADR-0073 Amendment 1 (#469 / PR #471)
adds one bar duration per admission.

Live follow-ups, none authorized: the maintainer-accepted values that were not measured (thresholds, the 1 second to 1 day
duration range, at most 8 definitions per `admit`) are revisited only on evidence from a real-host run; a family identity across
parameterizations has the trigger "a study that needs to relate many parameterizations under one name"; server-side execution or
re-derivation of the feature (a claim stronger than naming it) is a different design gate.

### DG-M v1 reopening triggers (#475)

Each is a named condition that reopens a decision. None is authorized by the closeout; trigger 3 was later exercised (#477).

1. **Real-host run.** No deck has computed the feature over real delivered trades and registered the result on the real server.
   That run is operator evidence (ADR-0073 §8); correctness on real data and profitability are not claimed. Trigger: the operator
   performs it and records the result.
2. **Observation identity across durations.** Two FINAL observations from bars of different durations that start at the same
   instant have the same observation identity when produced by separate library calls (ADR-0026, F02's `bar:<RFC3339>`
   coordinate; H01 has the same property). ADR-0073 Amendment 1 holds it at the admission level (one duration per admission), at
   the cost of one admission and one delivery per timeframe. Trigger: a study that needs several timeframes under one admission,
   or a consumer that merges observation streams of several admissions on the support coordinate alone; it needs its own decision
   on E02/F02.
3. **Protocol client — EXERCISED and CLOSED (#477 / PR #478, governance #479).** `DeckHandoffClient.admit` now carries the optional
   `definitions` as given and refuses a sealed admission without definition identities when they were requested; the split topology
   proof uses it. Remaining limit: the client neither validates nor interprets the entries (every refusal is the server's).
4. **Reference kind.** The builder and K13 do not enforce the kind of a declared definition reference (ADR-0073 §6). Trigger: a
   reader or tool that must rely on it.
5. **More registry entries.** One entry; the H01 definitions are not in it. Adding one is a code change at the deck-pinned commit.
   A family identity across parameterizations keeps the ADR-0073 trigger "a study that needs to relate many parameterizations
   under one name".
6. **Naming versus computing.** The server attests that it admitted a request that named the definitions, not that the deck computed
   the feature; server-side execution or re-derivation is a different design gate.
7. **Unmeasured values and costs.** The defaults 0.3 and 0.5, the 1 second to 1 day duration range and at most 8 definitions per
   `admit` are maintainer-accepted, not measured; the throughput of bar-flow construction over the full delivered history and the
   cost of a full-history feature run on the deck are not measured. Trigger: evidence from a real-host run.
8. **Proof environment.** `PostgresSplitTopology` runs on the CI's PostgreSQL 17 and cannot run on Windows (the catalog requires an
   absolute POSIX `abs_path`). Trigger: a production-like run on a POSIX host.

## Explicitly deferable decisions

The following are classified and therefore not roadmap unknowns:

### Second provider / multi-capability resolution (`A12`,`C06`)

Wait for a real second venue or second representation. Do not freeze a generic resolver from first-provider symmetry.

### L3/MBO (`A15`)

Wait for a real L3/MBO feed and map its semantics without invention.

### Durable DatasetSnapshot (`B05`)

Wait for a concrete durable replay/reproduction requirement. Catalog-query replay alone remains insufficient; reference-vs-artifact shape is not frozen now.

### Schema evolution beyond accepted versions (`B08`)

Wait for a real next schema/version requirement.

### Feature provider extension (`E07`)

Wait for a real second provider/capability need; no generic plugin framework now.

### Multi-asset execution (`H06`)

Wait for explicit multi-asset product scope.

### Canonical API transport (`J02`) — RESOLVED

Consumer API semantics are already frozen. ADR-0050 resolves the bounded Wave 7
transport choice as **WebSocket** (a single unified transport, ADR-0050 §1)
over the existing Application-owned Consumer API surface. J02/J04/J05/J06 are
complete for that bounded API/client path. This does not activate J03, J07,
J08, broker/live execution, second venue, market depth branches or any product
runtime beyond the accepted transport/client boundary.

ADR-0050 Amendment 1 additionally froze a documented 50,000-row/16 MiB result
bound (`RESULT_TOO_LARGE`) and an explicit wire `max_size`. It left
authentication, TLS and authorization for non-loopback J02 open at the time;
**that gap is frozen under ADR-0063 and implemented by `J09` (PR #310, Wave 8)**
— see `DG-J` G1. ADR-0057 forbade using the unauthenticated non-loopback
override as a deck handoff channel; the authenticated WSS/mTLS surface now
exists for the `j02.market_data.read` scope only.

### Replay Consumer-API seam (`J11`)

ADR-0065 §5 explicitly defers this `DG-J` G4 atom rather than freezing it:
`HistoricalReplayRuntime.run()` requires an injected `feature_provider`
callable, and there is no accepted, identity-bearing, serializable,
server-resolved definition for it. Exposing it as a path, module name, opaque
executable, or client callback would invent a new semantic authority and alter
Strategy/Replay authority. Trigger: a future ADR that defines that semantic
feature/provider reference and its provenance/availability contract, and
chooses whether the resulting operation is synchronously bounded or must be
admitted through `J03`.

Owner decision 2026-10-10 (DG-K, #318): `J11` is **not planned**. Replay runs
only on the deck, in-process, over `K12`-admitted inputs, and results return
through `K13` (DG-K atom `P06` carries that handoff over J02). The trigger is
therefore narrowed: an explicit maintainer request for server-side or
remote-client replay, after which the ADR above is still required. No `J11`
design gate follows DG-K.

### Paper/shadow and live product runtime (`J07`,`J08`)

Wave 6 completes bounded data-plane/storage prerequisites only. It does not
activate or complete paper/shadow trading, broker/live execution or live product
mode. Those runtime/product decisions remain outside the completed Wave 6 scope.

## Implementation-local choices — not governance blockers

Unless a future accepted contract says otherwise, these remain local choices:

- internal helper/class names;
- concrete bounded-batch Python class/method naming where semantics are frozen;
- Parquet writer library/compression/row-group/page/dictionary settings;
- client presentation details;
- observability implementation technology after required signals are known;
- local reversible code organization within established ownership/dependency boundaries.

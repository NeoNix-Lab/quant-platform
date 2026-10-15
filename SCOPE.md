# Scope: DG-C / A14 — L2 contract, continuity and publication v1

Status: **Part A (design gate) CLOSED — ADR-0070 ACCEPTED (#367, PR #370, 2026-10-15). Part B implementation inventory AUTHORIZED on integration branch `implement/l2` (cut from `implement/dgk`); its implementation issues are not yet opened.**

Scope kind: this document has two parts. Part A recorded the L2 design gate registered by governance issue #364 and closed by #367. Part B is the implementation inventory that gate makes implementable, reconciled by governance issue #371. No implementation may start until its Part B issue exists and its start gate is met.

---

## Historical scope archive

This file previously recorded **DG-K — Process Topology v1** (Part A closed via ADR-0068/ADR-0069; Part B implemented on `implement/dgk`), and earlier **Wave 8 — Full Platform API & Remote Service Topology v1** (closed #327/PR #328). Both are preserved in git history (`git log -- SCOPE.md`). `SCOPE.md` is rewritten in place on each integration line; the DG-K scope remains authoritative on `implement/dgk` until its closeout, and the two versions are reconciled by hand at promotion to `main` (see "Promotion").

---

## Why this scope exists

1. PR #317 / issue #316 added L2 groundwork only: `schemas/l2-book-event-v1.json`, `L2BookEvent` / `L2LevelChange` (`quant_platform.data.models`) and `normalize_bybit_l2_message` (`quant_platform.source_adapters.l2`).
2. `A14` was nevertheless `OPEN_BLOCKING` / `MISSING`: no accepted ADR resolved snapshot/delta reconciliation, ordering, continuity, materialization, publication or reading for L2, and `CAPABILITY_DAG.md` required the L2 branch decision to ratify the #317 schema by ADR or supersede it.
3. The maintainer selected L2 on 2026-10-14. #364 turned that selection into a design gate; #367 produced ADR-0070.

---

## Owner decisions (inputs the design gate honored)

Taken on 2026-10-14 and recorded by #364:

1. **Aggregated price levels only.** No order-level, queue-position or L3/MBO semantics; no synthetic book from trades.
2. **`A13` (L1) is not a prerequisite of `A14`.** "L1 does not activate L2" is unchanged.
3. **Two batches.** `v1` is historical ingest; `v2` is live ingest.
4. **Consumer read is in scope of `v1`.** An L2 that can be written but not read does not close `A14`.
5. **`implement/l2` stays cut from `implement/dgk`**; promotion to `main` follows DG-K closeout. The by-hand `SCOPE.md` reconciliation toward `main` is accepted maintainer debt.
6. **Venue: Bybit only.** No second venue.

---

## Part A — Design gate outcome, CLOSED

| Gate | Issue | ADR | Disposition | One-line resolution |
|---|---|---|---|---|
| **DG-C / A14** L2 | #367 | ADR-0070 | ACCEPTED | Order by numeric `seq` alone (`exchange_ts` = `cts` is a certified monotone property, `u` is the continuity signal); `u + 1` segments with `known_gap` / `degraded` and no synthetic snapshot; canonical layer stores source events, one effective depth (200); `canonical/l2/bybit/BTCUSDT/l2-book-event-v1` with existing manifests and `l2` kind (no manifest evolution); sibling L2 modules with certification checks 1-9; additive `l2_book_events@1` representation with a byte-based result bound; `l2-book-event-v1` ratified unchanged; `ob500` history only by declared top-200 projection (`A14g`); live supervision rules for `v2`. |

Answers to the eight DG-C questions are in ADR-0070 §1-§8, with §9 for the projection. `OPEN_DECISIONS.md` (DG-C) records them in summary. ADR-0070 amends no accepted ADR and changes no existing identity, manifest vocabulary, J02 v1 or `trade-v1` code path.

### Evidence recorded for the gate

- Provider documentation, a 150 s live capture and archive availability probes (ADR-0070 Appendix).
- E1 (2026-10-15, first 2 MiB of one `ob200` day, 2025-12-01, and one `ob500` day, 2024-01-15; no full download): JSON lines in the live message shape; `cts` present in 3,647 of 3,647 and 3,638 of 3,638 messages; first record of each day is a `snapshot`; `u` always +1; `seq` strictly increasing and unique; no `cts` regression along `seq`; no delete of an absent level and no crossed book. The zip is a streamed archive with a data descriptor (sizes are `0` in the local header).
- Not covered by that sample and therefore still open: provider restarts (E2), the rest of the day (E3), mid-day re-snapshots (E4), real-day parity of the 500 -> 200 projection (E5).

---

## Part B — Authorized L2 implementation inventory

Each atom below has an `ACCEPTED` governing ADR section. Each later receives its own implementation issue; **this reconciliation opens none**.

| Atom | Batch | Capability | Governing ADR | Requires | Sequencing note |
|---|---|---|---|---|---|
| `A14a` | v1 | Bybit L2 source semantics, ordering provider and certification profile | ADR-0070 §1, §2, §5, §7 | `A14` | Startable. First task: measure E2-E4 on one full pinned `ob200` day (separate maintainer approval for the download) before freezing check-suite parameters. Includes the adapter mapping `exchange_ts` = `cts`, `provider_ts` = `ts` under `bybit-orderbook-l2-v2`. |
| `A14b` | v1 | Canonical L2 Parquet materializer and scanner | ADR-0070 §3, §4 | `A14a` | Start gate: HOLD until `A14a` is merged. `native_sequence` and `native_update_id` are `uint64` columns with exact string round-trip against the string-typed schema. |
| `A14c` | v1 | L2 certification, publication and catalog bridge (`kind="l2"`) | ADR-0070 §4, §5 | `A14a`, `A14b`, `A08`, `A09` | Start gate: HOLD until `A14a` and `A14b` are merged. PostgreSQL integration test confirms the catalog needs no DDL change. |
| `A14d` | v1 | Bybit historical L2 ingest (`ob200` day) with per-day outcome | ADR-0070 §2, §4, §7 | `A14b`, `A14c`, `A07` | Start gate: HOLD until `A14b` and `A14c` are merged. Streamed-zip reader with data descriptor; `ob500` and other depths refused as `source_depth_unsupported`. |
| `A14e` | v1 | L2 consumer read (`l2_book_events@1`) | ADR-0070 §6 | `A14c`, `B02` | Start gate: HOLD until `A14c` is merged. Independent of `A14d`. Per-partition origin (`source-acquired` or `projected`) is visible in the read; no `u + 1` assumption on projected rows; byte-based `result_too_large` bound. |
| `A14f` | v2 | Bybit live L2 ingest and resync | ADR-0070 §8 | `A14d`, `A11` | Authorized as scope, **held**: start gate is `A14` closed (v1 golden accepted) and an approved bounded soak plan. Outside the `A14` closure. |
| `A14g` | follow-on | Bybit `ob500` raw ingest and top-200 projection | ADR-0070 §9 | `A14b`, `A14c`, `A14d` | Authorized as scope, **held**: start gate is `A14d` merged and the E5 real-day parity test planned. Outside the `A14` closure. Certification check 9 (recomputation). |

```text
A14a ─> A14b ─> A14c ─┬─> A14d ─┬─> A14g
                      │         └─> A14f   (also requires A11)
                      └─> A14e
```

Immediately startable once its issue exists: `A14a`.

### v1 golden proof (batch closeout, not an atom)

Historical ingest -> materialization -> certification -> catalog publication -> consumer read of one pinned UTC day of the `ob200` archive. Fixtures: a pinned `ob200` day whose archive SHA-256 is recorded; a committed excerpt limited to snapshot, delta, delete and one discontinuity, under the repository's no-bulk-data rule. Hermetic proof exercises every check and outcome; one full real day is run by the maintainer and recorded in the closeout. Acceptance and refusals are stated in ADR-0070 ("v1 golden proof").

### Integration branch

`implement/l2` already exists (cut from `implement/dgk` @ `9db9637`). Atomic slices use `agent/issue-<N>-<slug>` from it and target it (`AGENTS.md`, Branching). Promotion to `main` follows DG-K closeout (see "Promotion").

---

## Authority

- `AGENTS.md`
- governance issues #364 and #371; design-gate issue #367
- ADR-0070 (L2 contract, continuity and publication v1)
- ADR-0004, ADR-0018, ADR-0019, ADR-0020, ADR-0022, ADR-0025, ADR-0029, ADR-0030, ADR-0032, ADR-0040 through ADR-0044, ADR-0050, ADR-0054, ADR-0057, ADR-0063, ADR-0066, ADR-0068
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`DG-C` section; `A14`-`A14g`), `docs/architecture/OPEN_DECISIONS.md` (`DG-C`)

Accepted ADRs and frozen contracts remain normative. No Part B issue may change `trade-v1`, `candle-v1`, `dataset-manifest-v1`, `partition-manifest-v1`, J02 v1, an existing representation contract or an existing identity, nor modify the `trade-v1` materializer, scanner, certification or gateway paths (ADR-0070 §4).

---

## Baseline and Credited State

```text
implement/l2 @ 4f1eb1f (PR #370 merged; cut from implement/dgk @ 9db9637)
DG-C: ADR-0070 ACCEPTED (#367); A14, A14a-A14g FROZEN / MISSING
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records; in particular `A07` (one-UTC-day BTCUSDT-linear historical acquisition, ADR-0054), `A08`, `A09`, `A11` and `B02`. The #317 groundwork (`l2-book-event-v1`, `L2BookEvent`, the Bybit normalizer) is credited as input; its `exchange_ts` mapping is corrected by `A14a`.

---

## Explicit non-claims

- `A14` and `A14a`-`A14g` are `FROZEN`/`MISSING`: decided, not built. No implementation issue exists yet.
- No L2 data is published or readable until `A14c`/`A14d`/`A14e` are implemented.
- E2-E5 are not closed; their rules are fail-closed and recorded in ADR-0070 ("Open evidence").
- The E1 sample covers the first minutes of two days; it does not prove behaviour across restarts or the whole day.
- Live L2 (`A14f`) and `ob500` history (`A14g`) are not part of the `A14` closure.
- A second effective depth or a second venue needs an identity dimension (ADR-0018 evolution) and is not authorized.

---

## Mutation policy

- Each Part B implementation issue produces code and tests for exactly one atom.
- Branch `agent/issue-<N>-<slug>` (or `codex/issue-<N>-<slug>`) from `implement/l2` and target it, not `main`.
- Do not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` as part of implementation work (`AGENTS.md` Governance boundary). Governance PRs use a `governance/` or `admin/` head branch.
- Start gates in the Part B table are binding: `A14b` after `A14a`; `A14c` after `A14a`+`A14b`; `A14d` after `A14b`+`A14c`; `A14e` after `A14c`; `A14f` and `A14g` as stated.

---

## Promotion

Each integration line rewrites `SCOPE.md` in place. DG-K promotes first; the L2 line then rebases and reconciles `SCOPE.md` by hand at its own merge. The order is recorded in `ROADMAP.md` and is not decided by an implementation issue. The by-hand reconciliation is accepted maintainer debt, paid at the L2 merge.

---

## Exit criteria for this scope

- Part A: **met** — ADR-0070 `ACCEPTED` (#367), reconciled here.
- Part B (`A14` closure): `A14a`-`A14e` implemented, merged into `implement/l2` and promoted to `main`, the `v1` golden proof accepted, followed by a governance closeout. `A14f` and `A14g` are separate follow-on scopes.

---

## Out of Scope

- order-level, queue-position, MBO or L3 semantics; synthetic books from trades;
- L1 (`A13`), a second venue, execution or trading on L2 data;
- any change to frozen `trade-v1` / `candle-v1` / manifest / J02 v1 contracts or to existing identities;
- a second effective book depth in one dataset;
- DG-K work (`P01`-`P06`) and the FEI feature-provider line;
- bulk market data, databases, experiment artifacts or secrets in Git.

---

## Stop / Escalation Conditions

Stop and report rather than implement or decide unilaterally if:

- an implementation issue cannot satisfy its ADR-0070 section without weakening an accepted ADR or changing a frozen contract or identity;
- a Part B issue starts before its start gate is met;
- measured evidence (E2-E5, or a full-day inspection) contradicts a premise of ADR-0070 (for example a `cts` regression along `seq` that would make whole days ineligible): record the finding and request an ADR amendment instead of relaxing a rule in code;
- implementing an atom would require answering a design question ADR-0070 leaves open.

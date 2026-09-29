# Scope: Wave 6 - Live Consumer Data Plane & Storage Lifecycle v1

Status: **OPEN**

Scope kind: **decision-gate resolution and bounded implementation scope for Wave 6**.

Target integration branch: **`implement/wave-6`**, branched from `main` after Wave 5 promotion.

---

## Prerequisite Integration Gate

Wave 6 may start only after the Wave 5 Experiment / Supervised ML path is promoted to `main`.

Current gate evidence:

```text
Wave 5 promotion PR: #187
main promotion commit: e4d6c1b667c6cd6b975dd512d7346d510798638d
Wave 5 tag: wave-5-experiment-supervised-ml-v1
Issue #174: Wave 5 Golden E2E supervised proof credited
Issue #175: Wave 5 governance closeout closed after main promotion
```

The dependency basis is `CAPABILITY_DAG.md`: `B06` requires `A11,B03`; `D04` requires `B06,D02`; `K07` requires `K05,K06`; `K09` requires `K08`.

`B06`/`D04` (Track A, Data Access/Representation) and `K07`/`K09` (Track B, Operations) are independent tracks — neither technically depends on the other. Any sequencing below is operational batching under the repository's one-bounded-mutation-slice-at-a-time rule, not a semantic dependency claim.

---

## Objective

Resolve the two still-open Decision Gate families that `ROADMAP.md`'s Decision-gate model tracks as `DG-B` (Historical/Live data convergence) and `DG-H` (Operational safety), and implement their remaining atoms:

```text
Track A — Live Consumer Data Plane (DG-B)
  A11 live acquisition (COMPLETE) + B03 result identity (COMPLETE)
                    |
                    v
          B06 live access / consumer cursor
                    |
                    v
       D04 incremental / live candle computation
                    |
                    v
   future J07 paper trading consumer (out of scope here)

Track B — Storage Lifecycle & Operational Safety (DG-H)
  K06 RAW/source protection (COMPLETE) + K08 backup/restore proof (COMPLETE)
                    |
                    v
            K07 storage tier relocation
                    |
                    v
       K09 retention / governed deletion authority
                    |
                    v
        sustainable long-running live operation
```

`B06` and `K07`/`K09` are `OPEN_BLOCKING` in `CAPABILITY_DAG.md`: their semantics are not yet frozen by any ADR. `D04` is already `FROZEN` (candle semantics are fixed by `ADR-0021`/`CANDLE_DEFINITION.md`); only its live/incremental implementation is `MISSING`. Wave 6 must therefore:

1. resolve `DG-B`'s live-consumer cursor semantics (resume, ordering, disconnect, explicit gap notification) through a design gate that produces an ADR;
2. implement `DataGateway.live_stream()` against that ADR without inventing a new repair engine or claiming completeness the provider cannot prove (per `ADR-0040`/issue #110's `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` disposition);
3. implement incremental/live candle computation that converges to the historical `D03`/`ADR-0021` `CLOSED` output, with `PARTIAL` state for candles still forming;
4. resolve `DG-H`'s tier-relocation crash-safety algorithm through a design gate that produces an ADR;
5. implement crash-safe hot→cold/deep-cold relocation without ever creating a window of consumer-visible data disappearance;
6. resolve `DG-H`'s retention/deletion-authority policy through a design gate that produces an ADR;
7. implement governed deletion that never removes protected or sole-recoverable evidence, gated on a verified `K08` restore.

Wave 6 delivers atoms **`B06`**, **`D04`**, **`K07`** and **`K09`**. It does not implement paper trading (`J07`), live product mode (`J08`), API transport (`J02`), a second venue, or L1/L2/L3 market depth.

---

## Authority

Read before mutating code or scope-derived issue bodies:

- `AGENTS.md`
- `README.md`
- `docs/product/PRODUCT.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/ROADMAP.md` (Decision-gate model; `DG-B`, `DG-H` boundaries)
- `docs/architecture/TARGET_ARCHITECTURE.md`
- `docs/contracts/DATA_GATEWAY.md`
- `docs/contracts/CANDLE_DEFINITION.md`
- `docs/architecture/STORAGE_LIFECYCLE.md`
- `docs/architecture/OPEN_DECISIONS.md` (`DG-B`, `DG-H`)
- `docs/decisions/ADR-0019-datagateway-boundary.md`
- `docs/decisions/ADR-0021-candle-definition-v1.md`
- `docs/decisions/ADR-0024-package-boundary-modular-monolith-v1.md`
- `docs/decisions/ADR-0028-pressure-policy-v1.md`
- `docs/decisions/ADR-0029-non-contiguous-coverage-reads-v1.md`
- `docs/decisions/ADR-0032-raw-source-protection-v1.md`
- `docs/decisions/ADR-0033-backfill-repair-v1.md`
- `docs/decisions/ADR-0039-backup-restore-v1.md`
- `docs/decisions/ADR-0040-bybit-live-trades-v1.md`

Accepted ADRs and frozen contracts remain normative semantic authority. Issue #110's `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` disposition is not reopened by this scope.

---

## Baseline and Credited State

Authoritative baseline for this branch:

```text
main @ e4d6c1b667c6cd6b975dd512d7346d510798638d
tag  wave-5-experiment-supervised-ml-v1
```

Credit, do not reimplement or re-prove absent invalidating evidence:

```text
A10 Backfill and repair v1                             COMPLETE
A11 Live trades acquisition                            COMPLETE
A16 General quality lifecycle                          COMPLETE
B02 Bounded historical scan (DataGateway.scan())       COMPLETE
B03 Result identity / provenance                        COMPLETE
B04 Non-contiguous coverage read                        COMPLETE
D02 CandleDefinition v1                                 COMPLETE
K02 Runtime identity v1                                 COMPLETE
K03 Observability                                       COMPLETE
K04 Capacity observation                                COMPLETE
K05 Health/pressure policy v1 (ADR-0028)                COMPLETE
K06 RAW/source protection v1 (ADR-0032)                 COMPLETE
K08 Backup/restore proof v1 (ADR-0039)                  COMPLETE
K10 Checkpoint/recovery v1 (ADR-0042)                   COMPLETE
```

Important credited implementation details:

- `quant_platform.access.gateway.DataGateway.scan()` already owns the bounded, ordered, finite historical read seam (`B02`). `B06` is a distinct, additive live/streaming seam on the same `DataGateway` class — it must not duplicate or replace `scan()`.
- `quant_platform.representation.candles` already owns `D03`'s historical, batch-computed candle sealing logic under `ADR-0021`. `D04` must converge to the same `CLOSED` output for identical trade input; it must not define a competing candle definition.
- `quant_platform.operations.protection`, `.pressure`, `.checkpoint` already own pure, catalog-independent decision seams for `K05`/`K06`/`K10`. `K07`/`K09` follow the same pattern: pure decision/algorithm logic in `operations`, composed with the catalog at the `application` layer — `operations` must not gain a dependency on `access`/`producer`.
- `quant_platform.application.live_ingest_server` and `.live_gap_orchestration` already demonstrate the application-composition pattern this scope reuses for wiring pure domain seams to the catalog and to a real running process.

Wave 6 composes these existing primitives. It must not duplicate `DataGateway.scan()`'s bounded-read semantics, `representation.candles`'s sealing logic, or `operations`'s existing pure decision seams.

---

## In-Scope Capability Inventory

| ID | Capability | Owner | Requires | Unlocks | Decision State | Target Impl State | Acceptance / Authority |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **B06** | `Live access / consumer cursor v1` | Data Access | `A11`, `B03` | `D04`, `J07` | OPEN_BLOCKING → RESOLVED (this scope) | COMPLETE | `DataGateway.live_stream()`: deterministic consumer resume/replay, explicit disconnect and gap notification; never invents missing trades. |
| **D04** | `Incremental / live candle computation v1` | Representation | `B06`, `D02` | `J07` | FROZEN | COMPLETE | `PARTIAL` forming candle plus `CLOSED` candle that is numerically identical to `D03`'s historical output for the same trades. |
| **K07** | `Storage tier relocation v1` | Operations/Data Plane | `K05`, `K06` | storage lifecycle | OPEN_BLOCKING → RESOLVED (this scope) | COMPLETE | Crash at any point yields either the old or the new valid placement, never neither; catalog updates only after target verification. |
| **K09** | `Retention / deletion authority v1` | Operations | `K08` | sustainable live | OPEN_BLOCKING → RESOLVED (this scope) | COMPLETE | Never deletes protected or sole-recoverable evidence; deletion requires a verified `K08` restore path first. |

---

## Decision Gate Resolution: DG-B and DG-H

Unlike Wave 5 (whose `I03`/`I04`/`I05` decision states were already `RESOLVED` before implementation began), `B06`, `K07` and `K09` are `OPEN_BLOCKING` today. This scope's Active Path therefore includes dedicated **design-gate** issues — using this repository's `[agent] Design gate` template — that must each produce a committed ADR before their corresponding implementation slice starts. This mirrors the exact pattern already used for `K02`/`K06` (`ADR-0041`, `ADR-0044`) during the Live Ingest Server Production Readiness effort.

- **DG-B / B06**: produces the next ADR (`ADR-0047`), resolving live-consumer cursor resume/ordering/disconnect/gap-notification semantics. Must not reopen issue #110's `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN` disposition — an unprovable gap stays an explicit, auditable non-complete interval, never a silently filled one.
- **DG-H / K07**: produces the next ADR (`ADR-0048`), resolving the tier-relocation algorithm and restart protocol left open by `STORAGE_LIFECYCLE.md` §4/§13.
- **DG-H / K09**: produces the next ADR (`ADR-0049`), resolving retention periods and deletion authority left open by `STORAGE_LIFECYCLE.md` §13, gated on `K08`.

`D04` requires no new design gate: its semantics are already `FROZEN` by `ADR-0021`.

DG-B remains open beyond this scope only for its already-disposed long-gap remediation proposition (#110, unchanged). DG-H remains open beyond this scope for no currently-identified atom.

---

## Package Boundary and Modular Monolith Updates

Wave 6 does not introduce a new top-level package, but extends existing owners:

```text
quant_platform.access.gateway   -- add DataGateway.live_stream() (owner: access, unchanged)
quant_platform.representation.candles -- add incremental/live sealing path (owner: representation, unchanged)
quant_platform.operations.<relocation module>  -- new submodule, owner: operations
quant_platform.operations.<retention module>   -- new submodule, owner: operations
quant_platform.application.<storage lifecycle composer> -- new submodule, owner: application
```

Each new submodule must be registered explicitly in `tests/test_package_boundaries_v1.py`'s `OWNERS` dict (module → owner), matching the existing one-entry-per-submodule pattern. No `ALLOWED` edge changes are anticipated:

- `access` remains `{"access", "physical", "shared"}` — `B06` does not need a new outbound edge.
- `representation` remains `{"representation", "shared"}` — `D04`'s live path must receive already-extracted primitive trade fields from its `application`-layer caller, exactly as `D03` already does for historical batches; it must never import `access` directly.
- `operations` remains `{"operations", "shared"}` — `K07`/`K09`'s relocation/retention algorithms must stay pure and catalog-independent; actual catalog mutation is composed at the `application` layer, exactly as `live_ingest_server.py` already composes `operations.checkpoint` with the catalog.
- `application`'s existing `{"application", "access", "representation", "feature", "producer", "operations", "validation", "source", "physical", "shared", "strategy", "execution", "portfolio", "replay"}` already covers every edge this scope needs; no `ALLOWED` change is anticipated there.

Exact new submodule names are an implementation decision for each design-gate ADR to fix, not this document.

---

## Active Path

The repository rule of one bounded mutation slice at a time governs execution. Track A and Track B are independent; the order below is operational batching, not a dependency claim between the tracks.

```text
1. Design gate - DG-B: B06 Live-Consumer Cursor Semantics
   - Produce ADR-0047 resolving resume/ordering/disconnect/gap-notification semantics.
   - Must not reopen issue #110's NO_AUTHORITATIVE_REPAIR_PATH_PROVEN disposition.
        |
        v
2. Slice B06 - DataGateway Live Streaming Access & Consumer Cursor
   - Implement DataGateway.live_stream() per ADR-0047.
   - Explicit disconnect handling and gap notification; no invented trades.
        |
        v
3. Slice D04 - Incremental and Live Candle Computation
   - Implement PARTIAL/CLOSED candle sealing over already-extracted trade fields.
   - Prove numeric equivalence with D03's historical CLOSED output.
        |
        v
4. Slice - Compose B06 into D04 (gap-safe watermark)
   - Implement the application-layer composer driving D04's IncrementalCandleBuilder
     from B06's live_stream() events.
   - Never advance the sealing watermark across an interval a LiveGapEvent has not
     yet proven complete (OPEN blocks advancement; CLOSED resumes it from upper_bound).
        |
        v
5. Design gate - DG-H: K07 Storage Tier Relocation Algorithm
   - Produce ADR-0048 resolving the crash-safe relocation algorithm and restart protocol.
        |
        v
6. Slice K07 - Storage Tier Relocation & Warm/Cold Migration
   - Implement crash-safe hot->cold/deep-cold relocation per ADR-0048.
   - Catalog location changes only after target verification; source removed only after verification.
        |
        v
7. Design gate - DG-H: K09 Retention & Deletion Authority Policy
   - Produce ADR-0049 resolving retention periods and deletion authority, gated on K08.
        |
        v
8. Slice K09 - Retention, Deletion Authority & Compliance Audit
   - Implement governed deletion per ADR-0049.
   - Never delete protected or sole-recoverable evidence; require a verified K08 restore first.
        |
        v
9. Wave 6 Golden E2E Proof
   - Prove a live consumer (B06) computing live candles (D04, composed per step 4) while a
     K07 relocation and a K09 retention pass run concurrently, without corruption or
     consumer-visible data disappearance.
        |
        v
10. Wave 6 Governance Closeout
   - Reconcile CAPABILITY_DAG.md, CAPABILITY_MAP.md, ROADMAP.md and OPEN_DECISIONS.md.
   - Prepare implement/wave-6 for promotion to main after the Golden proof and reconciliation.
```

Step 4 was added after D04 (#195) landed: neither #195 nor the Golden E2E proof (step 9) build the actual B06-to-D04 wiring on their own -- #195 explicitly excludes B06's stream/cursor semantics, and the proof step must not introduce new semantics. Step 4 closes that gap; see issue #206.

---

## Acceptance Criteria

Wave 6 is complete only when all of the following are observably true:

1. **No Invented Continuity**: `B06`'s live cursor never fabricates a trade or claims interval completeness the provider cannot prove; unprovable gaps remain explicit per issue #110/`ADR-0040`.
2. **Deterministic Resume**: a consumer that reconnects at a recorded cursor position resumes without duplication or loss of already-delivered records.
3. **Candle Equivalence**: `D04`'s sealed `CLOSED` candles are numerically identical to `D03`'s historical computation for the same underlying trades; `PARTIAL` candles never leak into `CLOSED`-only consumers.
4. **Crash-Safe Relocation**: a simulated crash at any point during a `K07` relocation leaves either the original or the fully-verified new placement authoritative, never neither, and never a consumer-visible gap.
5. **Governed Deletion**: `K09` refuses to delete any unique RAW/source or sole-recoverable evidence, and refuses deletion of anything without a prior verified `K08` restore.
6. **Package Boundary**: `tests/test_package_boundaries_v1.py` passes with all new submodules registered and no forbidden dependency (`representation`/`operations` do not gain an `access`/`producer` edge).
7. **Verification Gate**: `python tools/workflow.py preflight`, `python tools/check_markdown_links.py` and the relevant focused tests pass.
8. **Golden Proof**: Wave 6 records a live-consumer + live-candle + concurrent-relocation + concurrent-retention proof with no corruption or consumer-visible disappearance, on bounded canonical Bybit BTCUSDT evidence.

---

## Out of Scope

Do not pull into Wave 6 unless a later authorized scope explicitly changes it:

- Paper/shadow trading mode (`J07`) or live product mode (`J08`).
- API transport (`J02`) or UI/TUI clients (`J04`-`J06`).
- A second venue or generic provider resolution.
- L1/L2 market depth (`A13`/`A14`) or L3/MBO (`A15`).
- Strategy/Execution/Portfolio/Replay/ML/RL semantics of any kind.
- A generic job scheduler, broker, or workflow engine.
- HA/distributed consensus or off-site disaster recovery.
- Speculative long-gap filling without attributable source evidence (issue #110 stays disposed).
- Concrete deployment capacity thresholds, backup destination/topology, or monitoring/alerting technology selection (`STORAGE_LIFECYCLE.md` §13's still-open items beyond `K07`/`K09`'s own scope).

---

## Stop / Escalation Conditions

Stop and report rather than implement if:

- a design-gate ADR cannot resolve its decision without weakening an accepted contract (`DATA_GATEWAY.md`, `CANDLE_DEFINITION.md`, `STORAGE_LIFECYCLE.md`);
- `B06` cannot provide explicit gap notification without either inventing continuity or reopening issue #110;
- `D04`'s live path cannot converge to `D03`'s historical output without a semantic change to `ADR-0021`;
- `K07`/`K09` cannot guarantee crash-safety or protected-evidence non-deletion without direct `operations` → `access`/`producer` coupling that the package boundary forbids;
- the Golden E2E proof requires Strategy, Execution, Paper/Shadow, Live runtime, or client/API semantics to be meaningful.

# Scope: Live Ingest Server Production Readiness v1

Status: **ACTIVE**

Scope kind: **governance and planning scope for the next production-readiness
implementation path**.

This scope replaces the prior `Live Ingest Vertical v1` closeout scope. That
scope was centered on proving the K10 restart/checkpoint path. PR #122 closed
that missing proof by advancing the checkpoint after restart reconciliation and
was merged into `main` on 2026-09-24 as merge commit
`36e86e3dbb812734eeb8728e61be9ac49f793aff`.

The active objective is now narrower than full live trading and broader than a
single restart proof:

```text
make the first Bybit BTCUSDT live-ingest server production-ready for v1
```

Production-ready here means a governed, continuously operable ingest server
that can run under the accepted K02 identity, publish through the existing
canonical lifecycle, recover from ordinary restart, represent every unresolved
gap explicitly, and follow a governed path for long-gap remediation before any
missing interval may be claimed complete.

It does **not** mean paper trading, live trading, J08 live product mode, B06
live consumer cursor, API/client work, ML/RL, Strategy, Execution, L1/L2/L3, a
generic job scheduler, or a generic provider framework.

## Authority

Start from:

- `AGENTS.md`
- `README.md`
- `docs/product/PRODUCT.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/ROADMAP.md`
- `docs/architecture/TARGET_ARCHITECTURE.md`
- `docs/architecture/MARKET_DATA_INGEST.md`
- `docs/architecture/OPEN_DECISIONS.md`
- `docs/contracts/CORE_CONTRACTS.md`
- `docs/contracts/MARKET_DATA_INGEST_CONTRACTS.md`
- `docs/decisions/ADR-0033-backfill-repair-v1.md`
- `docs/decisions/ADR-0039-backup-restore-v1.md`
- `docs/decisions/ADR-0040-bybit-live-trades-v1.md`
- `docs/decisions/ADR-0041-live-ingest-runtime-identity-v1.md`
- `docs/decisions/ADR-0042-live-ingest-checkpoint-recovery-v1.md`
- merged evidence in PR #111, #112, #113, #114, #119 and #122

Accepted ADRs and frozen contracts remain semantic authority. This scope may
organize the next work, but it must not silently reinterpret any accepted
contract version.

## Credited State

Credit, do not reimplement or re-prove absent invalidating evidence:

```text
K08  backup / restore v1                 COMPLETE
A11  Bybit BTCUSDT live trades v1        COMPLETE
K02  live-ingest runtime identity v1      COMPLETE
K10  checkpoint / recovery v1             COMPLETE for restart/reconcile proof
A10  backfill / repair v1                COMPLETE as bounded repair foundation
B04  non-contiguous coverage reads v1     COMPLETE
K03  observability foundation             COMPLETE
K04  capacity observation                 COMPLETE
K05  pressure policy                      COMPLETE
K06  RAW/source protection                COMPLETE
```

The repository governance files outside this scope may still contain stale
phrasing from the pre-PR #122 state, especially references to K10 as
`PARTIAL` or `REAL_RESTART_PROOF_PENDING`. That is a governance audit finding
for this production-readiness path, not permission to re-open K10 semantics.

## Production-Readiness Audit Findings

The following gaps block a responsible v1 production claim for the live-ingest
server.

### P1 - Persistent Ingest Server Ownership

The prior scope noted the daemon/service gap but did not make it an active
owned slice. A11/K10 functions and proof entrypoints exist, but the production
claim requires a bounded long-running server composition:

```text
configure
-> acquire live Bybit BTCUSDT trades
-> canonicalize
-> publish/catalog/certify
-> advance checkpoint only after durable publication
-> observe health/failure/progress
-> stop/restart cleanly
```

This is not authorization to build a generic scheduler or generic job runtime.
It is the minimum bounded live-ingest server loop for the first provider,
instrument and schema.

### P2 - Long-Gap Remediation Path

ADR-0040 and ADR-0042 intentionally permit only bounded provider
reconciliation. If the last durable `TradeKeyV1` is outside the bounded recent
provider evidence, the system must record explicit non-complete coverage and
must not claim lossless continuity.

Issue #110 disposed the earlier evidence search as
`NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`. Production readiness does not erase
that result. It promotes the missing path into an explicit blocker:

```text
long gap detected
-> interruption evidence recorded
-> non-complete coverage remains visible
-> authoritative repair source/path is selected only with evidence
-> A10 repair candidate/cutover is used when the interval can be proven
-> repaired interval is re-verified before any complete claim
```

If no authoritative source/path can prove the missing interval, the production
server may continue with a new governed live segment, but the gap remains
explicit and non-complete.

### P3 - Source Authority For Missing Data

A candidate repair source is not enough. Before implementation can fill a long
gap, the selected source must prove:

- exact interval support for the missing live interval;
- source identity and provenance;
- mapping to Bybit `trade-v1`, `TradeKeyV1` and canonical ordering without
  invented sequence continuity;
- overlap or other strong evidence that archive/recent/live semantics converge;
- failure behavior when the source cannot prove completeness.

Bybit historical archives may be re-investigated, but only observed,
attributable evidence may change the prior #110 disposition.

### P4 - Gap State Machine And Repair Queue

The product needs a governed operational state path, not scattered comments.
At minimum, future implementation must model:

```text
CONTINUOUS
RESTART_RECONCILING
GAP_DETECTED
GAP_RECORDED_NON_COMPLETE
REPAIR_SOURCE_UNPROVEN
REPAIR_CANDIDATE_PENDING
REPAIR_CUTOVER_COMPLETE
RESUMED_WITH_EXPLICIT_GAP
```

Names are implementation-local, but the observable states and fail-closed
transitions are not. No state may silently turn an unresolved gap into complete
coverage.

### P5 - Publication, Checkpoint And Repair Ordering

Production operation must preserve all existing invariants:

```text
canonical publication durable
        BEFORE
checkpoint advance

repair candidate validated
        BEFORE
repair cutover

gap support proven
        BEFORE
coverage complete assertion
```

Restart reconciliation may publish recovered records and advance the
checkpoint, as proven by PR #122, but long-gap repair must still pass through a
governed A10-compatible evidence path.

### P6 - Operational Runbook And Observability

K03 provides the foundation, but production readiness requires the selected
server to expose enough operator evidence for:

- current session state and subscription health;
- last durable checkpoint identity and generation;
- last durable canonical trade key;
- publication/certification/catalog failures;
- reconnect and bounded reconciliation outcomes;
- explicit gap intervals and repair state;
- pressure/capacity decisions that affect ingestion;
- proof that the service is running under the K02 identity and expected
  catalog/storage authority.

The exact logging, service manager and alerting technology remain
deployment-local unless a future ADR freezes them.

### P7 - Governance State Reconciliation

The active planning documents must be reconciled after this scope is accepted
or as the first implementation slice:

- update K10 state from pending proof to complete where PR #122 is sufficient;
- keep DG-B long-gap remediation visible as a production-readiness blocker;
- classify the bounded ingest server loop so it is not hidden behind generic
  `J03` job runtime or accidentally confused with `J08` live product mode;
- keep B06, K07 and K09 outside this v1 ingest-server scope unless a concrete
  blocker proves they are required.

## Active Path

One bounded mutation slice remains active at a time.

```text
1. Governance and scope formalization
   audit current authority
   -> record production-readiness gaps
   -> open the dedicated remote branch/PR

2. Ingest server v1 design gate
   define the bounded daemon/service composition
   -> define operator/runbook evidence
   -> prove no generic scheduler/framework is required

3. Long-gap remediation design gate
   re-audit authoritative repair sources
   -> decide source authority or retain explicit-gap-only behavior
   -> bind the path to A10 repair semantics

4. Implementation slice: bounded ingest server loop
   run under K02
   -> use A11/K10/A10/K03/K05/K06 authorities
   -> preserve publication-before-checkpoint

5. Implementation slice: long-gap state and repair orchestration
   detect/record long gap
   -> enqueue or represent repair intent
   -> prove gap stays explicit until repair evidence passes

6. Real-server production-readiness proof
   deploy/run bounded server
   -> stop/restart
   -> bounded reconcile
   -> forced long-gap scenario or deterministic simulation
   -> verify no silent loss, duplicate or false complete coverage

7. Governance closeout
   reconcile Capability Map / DAG / Roadmap / Open Decisions
   -> archive this scope per the normal versioned-scope convention
```

## Acceptance

`Live Ingest Server Production Readiness v1` is DONE only when all of the
following are observably true:

1. The live-ingest server has one bounded production owner and does not rely on
   manual proof scripts as the normal operating path.
2. The server runs under the accepted K02 least-privilege identity and does not
   gain backup, deletion, repository or administrative authority.
3. A11 canonicalization/publication semantics are preserved for real Bybit
   BTCUSDT public trades.
4. K10 checkpoint advancement remains strictly after durable canonical
   publication.
5. Stop/restart/reconcile continues to prove one canonical history with
   idempotent duplicates and no silent loss.
6. Long-gap detection records explicit non-complete coverage when bounded
   reconciliation cannot prove continuity.
7. No unresolved long gap is claimed complete merely from missing `seq`,
   elapsed wall time, absence of trades, local buffer contents or operator
   convenience.
8. Any long-gap repair uses an authoritative source/path proven for the exact
   missing support and flows through A10-compatible candidate/cutover evidence.
9. If no repair source/path is proven, the server can continue from a new
   governed live segment while preserving the explicit gap.
10. Operators can inspect session, publication, checkpoint, gap, repair,
    pressure and authority evidence through documented runbook steps.
11. Production-readiness proof runs on the target server or an explicitly
    accepted equivalent and records all material evidence paths.
12. Governance files are reconciled to the strongest propositions actually
    proven, without declaring J08 live product mode, B06 live cursor, K07 tier
    relocation or K09 deletion authority complete by implication.

## Out Of Scope

Do not pull into this scope unless a concrete acceptance blocker proves
otherwise:

- live trading or order execution;
- paper/shadow mode;
- Strategy, Replay, Execution, Portfolio, ML or RL;
- B06 live DataGateway consumer cursor;
- J02 API transport or App/TUI/CLI work;
- L1/L2/L3/MBO acquisition;
- second venue or generic provider resolution;
- generic job scheduler, broker, workflow engine or distributed runtime;
- HA/distributed consensus/off-site DR;
- K07 tier relocation;
- K09 retention/deletion authority;
- speculative long-gap filling without attributable source evidence;
- mutation of `main` except through reviewed Git integration.

## Stop / Escalation

Stop and report rather than implement if:

- current provider evidence contradicts ADR-0040 or ADR-0042;
- production readiness would require weakening explicit coverage/gap semantics;
- long-gap repair cannot prove source completeness for the exact missing
  interval;
- the ingest identity would need broader filesystem/database/backup authority
  than ADR-0041 permits;
- satisfying the server loop requires silently activating a generic scheduler,
  B06, K07, K09, J08 or another excluded capability;
- governance documents conflict with accepted ADRs/contracts in a way this
  scope cannot resolve without a new decision.

## Verification Proportionality

Use targeted verification while designing and implementing each bounded slice.
Run the repository-required verification gate once a stable candidate exists.

Real-server evidence is required only for claims that depend on the real
runtime, permissions, provider behavior or storage/catalog topology. Hermetic
tests remain appropriate for deterministic state machines, repair semantics and
failure matrices.

## Current Branch Intent

This branch formalizes the scope change and audit result. It does not implement
the ingest daemon or the long-gap repair system. The next implementation branch
must select exactly one bounded slice from the active path above.

# Scope: Live Ingest Vertical v1

## Objective

Move Quant Platform from historical-only Bybit trade ingestion to the first continuously running live-ingest vertical on the real server.

The bounded product outcome is:

```text
Bybit BTCUSDT public live trades
        ↓
source-owned live acquisition
        ↓
canonical trade-v1
        ↓
existing quality / source-protection / publication lifecycle
        ↓
canonical durable storage + catalog
        ↓
restart-safe continuous server operation
```

The goal is not to implement Wave 6 generally and not to activate the live trading product. The goal is specifically to make real Bybit BTCUSDT trades enter the existing canonical data plane continuously, durably, observably and recoverably.

Historical and live acquisition must converge on the existing canonical data model and publication path. Do not create a second live-only storage, catalog, schema or identity system.

## Baseline

Authoritative integrated baseline after PR #116:

```text
main @ 7137bb1d23e6aa74a882960bbb3bf190c9d46a8f
integrated Live Ingest head @ 9b03d44281a62cdd5e6c6699d9b532e3fc04cf09
```

Wave 3 is complete through F08 and remains credited. Live-ingest semantic and implementation state is now:

```text
K08  backup / restore v1             FROZEN / COMPLETE   ADR-0039; PR #111 + PR #113 real-topology proof
A11  Bybit live trades v1            FROZEN / COMPLETE   ADR-0040; PR #112 + PR #113 real-server publication proof
K02  live-ingest runtime identity    FROZEN / COMPLETE   ADR-0041; PR #113
K10  checkpoint / recovery v1        FROZEN / PARTIAL    ADR-0042; PR #114, REAL_RESTART_PROOF_PENDING
```

K10's implementation and its full hermetic proof matrix (15/15 items, `tests/test_operations_checkpoint_v1.py` + `tests/test_bybit_live_checkpoint_v1.py`) are done. The bounded real-server restart proof ADR-0042/issue #109 also requires is **not** done -- it was explicitly left `REAL_RESTART_PROOF_PENDING`, a valid non-closing outcome under #109's own acceptance criteria, not a weakened one. This is the current blocker on Acceptance item 11 below and on M2 closeout; see "Remaining path to closeout" for the plan.

Relevant credited implementation state includes:

```text
Canonical data plane
  trade-v1                         COMPLETE
  manifests / coverage            COMPLETE
  Parquet materialization         COMPLETE
  certification / publication     COMPLETE
  catalog / eligibility           COMPLETE
  quality lifecycle               COMPLETE
  backfill / repair               COMPLETE

Operations
  K02 Runtime identity             COMPLETE
  K03 Observability               COMPLETE
  K04 Capacity observation        COMPLETE
  K05 Pressure policy             COMPLETE
  K06 RAW/source protection       COMPLETE
  K08 Backup/restore              COMPLETE
  K10 Checkpoint/recovery         PARTIAL

Live first vertical
  Bybit BTCUSDT acquisition       COMPLETE
  canonical publication           COMPLETE
  real-server DataGateway read    COMPLETE
  restart proof                   PENDING
```

Remaining closeout propositions at this scope state:

```text
K10  bounded real-server restart + deployed checkpoint proof   PENDING
DG-B long-gap remediation beyond bounded reconciliation        DISPOSED -- NO_AUTHORITATIVE_REPAIR_PATH_PROVEN (#110)
B06  live access/cursor                                          MISSING but outside this scope
```

The long-gap proposition has been investigated and disposed (#110, 2026-09-24): a real Bybit historical archive exists but exact-interval completeness and archive/recent-live overlap could not be proven with the evidence available. The gap therefore stays explicit -- this is a closed, honestly-disposed proposition, not still-open investigation, and it is no longer permission to invent a repair source than it was before. See `OPEN_DECISIONS.md`'s DG-B section for the full disposition and evidence.

## Authority

Start from:

- `AGENTS.md`
- `docs/decisions/ADR-0039-backup-restore-v1.md`
- `docs/decisions/ADR-0040-bybit-live-trades-v1.md`
- `docs/decisions/ADR-0041-live-ingest-runtime-identity-v1.md`
- `docs/decisions/ADR-0042-live-ingest-checkpoint-recovery-v1.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/architecture/OPEN_DECISIONS.md`
- merged evidence in PR #111, #112, #113 and #114
- existing trade-v1 / Dataset / coverage / publication authorities
- existing K03/K04/K05/K06 runtime/evidence

Use progressive discovery:

```text
named authority
→ directly affected implementation/evidence
→ expand only for a concrete unresolved proposition
```

Do not reopen the four accepted Live Ingest ADRs unless current repository/provider evidence directly contradicts them.

## Execution policy

Only one bounded implementation or decision-gate mutation slice is active at a time.

The current path is:

```text
1. K10 bounded real-server restart/deployed-checkpoint proof       REMAINING -- next cycle's first issue
        ↓
2. resolve DG-B long-gap proposition from observed/provider evidence  DONE -- #110, NO_AUTHORITATIVE_REPAIR_PATH_PROVEN
        ↓
3. Live Ingest Vertical v1 closeout                                  blocked only on step 1
```

K08, A11 and K02 are credited complete and must not be reimplemented or re-proved absent invalidating evidence.

## Integrated evidence — K08, A11 and K02

### K08 — COMPLETE

ADR-0039 semantics are implemented by PR #111. The recovery set/export/restore path proves isolated restoration of canonical identity, coverage, catalog resolution and historical DataGateway-visible records. PR #113 then closes the previously pending deployment-independence handoff on the real target topology and verifies that the normal ingest identity cannot rewrite/delete the independent recovery copy.

Do not pull K07 or K09 into K08.

### A11 — COMPLETE

ADR-0040 is implemented by PR #112 for the selected first source:

```text
venue       Bybit
category    linear
instrument  BTCUSDT
dataset     trades
schema      trade-v1
source      publicTrade.BTCUSDT
```

Credited semantics/evidence include:

- `TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)`;
- canonical order `(exchange_ts, trade_id)`;
- provider `seq` preserved as evidence, not a gap-free cursor;
- `receive_ts` remains null in canonical v1;
- idempotent equivalent duplicate handling and fail-closed conflicting duplicates;
- explicit historical/live cutover;
- bounded reconnect reconciliation;
- explicit non-complete coverage when continuity cannot be proven;
- live-specific certification profile feeding the existing S13/S14 path.

PR #113 proves the real provider/server composition through canonical materialization, publication/catalog and historical DataGateway read-back. No live-only storage/publication/catalog abstraction exists.

### K02 — COMPLETE

PR #113 proves the target deployment under the existing non-root `mkt-transform` identity and minimum catalog writer role. It verifies real storage topology and closes the K08 backup-authority separation gap by removing ingest write authority from the independent recovery tier under explicit operator authorization.

For public Bybit trade ingest v1 there is no provider API secret to provision.

The normal ingest service must not regain general authority to destroy/replace the independent K08 backup merely for convenience.

## K10 — checkpoint/recovery closeout pending

ADR-0042 semantics are implemented hermetically by PR #114.

The invariant remains:

```text
canonical publication durable
        BEFORE
checkpoint may advance
```

Credited PR #114 evidence includes deterministic checkpoint identity/binding, monotonic/refusal behavior, atomic local persistence, validation against durable publication state, A11 bounded restart/reconciliation composition, replay/dedup semantics and explicit gap behavior when the durable anchor is outside the bounded provider window.

K10 remains `PARTIAL` because operational closeout still requires the bounded real-server proof:

```text
start bounded live ingest
→ publish real trades
→ persist checkpoint
→ stop/restart process
→ validate checkpoint binding
→ bounded reconcile + dedup
→ resume publication
→ prove one canonical history / no silent loss or duplicate
```

The proof must run under the K02 least-privileged identity and demonstrate the deployed checkpoint path/permission. Until that is observed, retain:

```text
REAL_RESTART_PROOF_PENDING
```

## Long-gap remediation — disposed, gap stays explicit

ADR-0040 deliberately left one proposition open; it has now been investigated and disposed (#110, `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`, 2026-09-24). The rule below is the accepted standing behavior going forward, not a still-open question.

Trigger:

```text
last durable canonical TradeKeyV1
cannot be recovered from bounded provider reconciliation evidence
→ explicit non-complete interval remains
```

A11/K10 may continue with a new governed live segment. They may **not** call the unresolved interval complete.

Before the gap may be filled, prove:

- an authoritative source actually exposes the exact missing interval;
- its identity/order/economic records map without invention to the accepted Bybit `trade-v1` semantics;
- the existing A10 repair path can consume that evidence;
- repaired support can be re-verified strongly enough to supersede prior interruption evidence.

Issue #110's investigation found a real, attributable Bybit historical archive as a candidate source, but could not prove exact-interval completeness or overlapping archive-vs-live/recent convergence with the evidence available at the time -- the decision is closed with the gap remaining explicit, not left open pending further work. A future issue may reopen this once that evidence exists.

The gap remains explicit for the same reason it always would have: missing sequence numbers, elapsed time, absence of trades or local buffer exhaustion are never sufficient proof.

Do not speculatively implement archive/backfill machinery before source/evidence authority is proven.

## Milestones

### M1 — Live Ingest Capable — COMPLETE

```text
K08 proof
+ real provider connection
→ real trades
→ canonical trade-v1
→ durable canonical publication
→ historical DataGateway read-back
```

PR #113 supplies the real-server evidence for M1.

### M2 — Live Ingest Hardened — PENDING

```text
M1
+ governed historical/live cutover
+ disconnect/reconnect correctness
+ K02-conformant deployment boundary
+ checkpoint/recovery
+ real restart proof
+ no unresolved gap falsely claimed complete
```

All M2 propositions except the real K10 restart proof and the honest DG-B long-gap closeout disposition are credited. The macro-scope remains open until those propositions are resolved without weakening evidence requirements.

## Acceptance

`Live Ingest Vertical v1` is DONE only when all of the following are observably true:

1. K08 isolated restore proof satisfies ADR-0039. -- **done**
2. A real Bybit public live connection acquires BTCUSDT linear trades. -- **done**
3. Real live evidence maps into canonical `trade-v1` exactly under ADR-0040. -- **done**
4. Historical/live overlap has one deterministic governed result with no ungoverned duplicate economic trades. -- **done**
5. Disconnect/reconnect follows ADR-0040 and never fabricates continuity. -- **done**
6. Live-acquired trades enter the existing certification/publication/catalog lifecycle. -- **done**
7. Coverage/completeness claims derive from explicit evidence; absence of trades is not proof. -- **done**
8. Existing K05/K06 semantics remain applicable and are not bypassed. -- **done**
9. K03 makes the real ingest operationally observable at the needed failure/provenance level. -- **done** (existing seams reused, not extended)
10. Any production identity/ACL/role mutation conforms to ADR-0041. -- **done** (K08 backup-ACL gap found and closed on the real server)
11. K10 stop/crash/restart proof satisfies ADR-0042. -- **NOT done**; hermetic implementation + full proof matrix only. This is the sole remaining acceptance blocker.
12. Data acquired live becomes ordinary canonical historical data readable through the existing DataGateway after publication. -- **done**
13. Any long gap that cannot be authoritatively reconstructed remains explicit non-complete coverage; no closeout claim silently erases it. -- **done** (#110 disposed `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`)
14. No B06 live consumer, Strategy, Execution, ML/RL, client or API capability is required. -- **true**, none introduced
15. Existing historical Bybit ingestion and canonical data-plane behavior remain valid. -- **done**, no regressions across all four merged PRs
16. Package/architecture boundaries remain intact. -- **done**, `test_package_boundaries_v1.py` green throughout
17. Required authoritative verification for each bounded slice passes with no unresolved blocker. -- **done** locally (63/63); CI confirms on integration

Items 1–10 and 12–17 have integrated evidence subject to the explicit DG-B closeout disposition; item 11 remains operationally pending.

## Verification proportionality

For every bounded slice:

- CREDIT unchanged data-plane/operations evidence;
- use targeted tests during implementation;
- prove only the exact missing proposition;
- run repository-required authoritative verification once on a stable candidate;
- do not repeat expensive proof absent relevant mutation/failure/review/rebase;
- use real-server evidence only where the acceptance proposition requires the real environment.

## Stop / escalation

Stop only when:

- current repository/provider state contradicts ADR-0039..ADR-0042;
- the real K10 restart proof cannot preserve publication/checkpoint invariants without upstream semantic change;
- required deployed checkpoint permission would require unauthorized/shared-state mutation;
- provider evidence cannot support a claimed ordering/dedup/coverage/reconciliation proposition;
- filling an actual long gap requires choosing an unproven repair source/semantics;
- satisfying closeout would require silently pulling B06 or another excluded capability into this scope.

Do not stop for reversible implementation-local names, module layout or technology choices already inside a frozen contract.

## Out of scope

Do not pull into this macro-scope unless a concrete acceptance blocker proves otherwise:

- B06 live DataGateway consumer cursor;
- K07 tier relocation;
- K09 retention/deletion;
- G01+ Strategy / Replay;
- Execution / paper / live trading;
- supervised ML / RL;
- L1 / L2 / L3 / MBO;
- second venue / multi-venue ingest;
- generic provider framework;
- API / client / UI;
- generic stream-processing framework;
- generic job scheduler/executor;
- generic checkpoint framework;
- HA/clustering/off-site DR;
- speculative gap-fill implementation without attributable source evidence;
- unrelated governance cleanup;
- mutation of `main` except through separately authorized integration/governance workflow.

## Remaining path to closeout

K08, A11, K02 and K10's hermetic implementation are complete and merged (`main`, PR #116). DG-B is disposed. Exactly one acceptance item blocks M2/closeout: **item 11, K10's real-server restart proof.**

The path from here:

```text
1. Real-server restart proof (next cycle's first issue)
   start bounded live ingest on the #108 target server
   → publish real trades, persist a checkpoint
   → stop the process cleanly (or a bounded non-destructive interruption)
   → restart, validate checkpoint binding, reconcile, resume
   → verify one canonical history, no silent gap or duplicate
        ↓
2. M2 reached -> Live Ingest Vertical v1 macro-scope closeout
   (reconcile CAPABILITY_DAG/CAPABILITY_MAP/ROADMAP/OPEN_DECISIONS to this
   proven state; this SCOPE.md itself is archived per the normal
   versioned-scope convention)
```

Step 1 is a bounded proof, not new implementation: `application.bybit_live.resume_live_ingest`/`next_checkpoint` and K02's real catalog/DataGateway composition already exist and only need to be exercised against a real running process. It does not require a new SCOPE.

### What "production ready" does *not* mean here

`CAPABILITY_DAG.md`'s `J08` ("Live product mode") atom depends on `J07` (paper/shadow mode) and `K09` (retention/deletion) in addition to K02/K08/K10 -- neither touched by this vertical and both explicitly out of scope above. Reaching M2 makes the ingest vertical itself restart-safe and continuously operable; it does **not** make the platform a live trading product. Do not read "Live Ingest Vertical v1 production ready" as "J08 done" -- they are different claims.

### The daemon/service gap this SCOPE never named

Neither this document's Execution policy nor `CAPABILITY_DAG.md`'s A11 row ever assigned an explicit task to *writing and deploying the actual persistent process* that runs acquisition -> publication -> checkpoint in a loop indefinitely. The Objective's own "restart-safe continuous server operation" phrase assumed this would follow naturally once K10 existed; in practice it is real, undone work with no owning atom. `generic job scheduler/executor` is explicitly out of scope above -- correctly, a *generic* one should not be built -- but *this one bounded loop* (compose the existing acquisition/publication/checkpoint functions under a process-manager unit, per K02's own precedent that "exact OS/service-manager primitives remain deployment-local") still needs to exist before "continuous operation" is true rather than aspirational. Whoever plans the next cycle after the real-restart proof should decide explicitly whether this loop is part of M2/closeout or a separate follow-on scope -- it is flagged here so it is not silently assumed away a second time.

## Scope closeout

Status: **CLOSEOUT PENDING**.

At closeout, reconcile governance to the strongest propositions actually proven by integrated runtime and real-server evidence.

Do not declare the whole Live Data Plane, Wave 6, V8, B06 or the live trading product complete merely because this first ingest producer path is integrated.

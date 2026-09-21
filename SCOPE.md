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

Authoritative scope baseline after the first Live Ingest semantic freeze:

```text
implement/wave-3 @ current fast-forward descendant containing ADR-0039..ADR-0042
```

Wave 3 is complete through F08 and remains credited. The following live-ingest semantics are now accepted authority:

```text
K08  backup / restore v1             FROZEN / MISSING   ADR-0039
A11  Bybit live trades v1            FROZEN / MISSING   ADR-0040
K02  live-ingest runtime identity    FROZEN / MISSING   ADR-0041
K10  checkpoint / recovery v1        FROZEN / MISSING   ADR-0042
```

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
  K03 Observability               COMPLETE
  K04 Capacity observation        COMPLETE
  K05 Pressure policy             COMPLETE
  K06 RAW/source protection       COMPLETE

Historical first vertical
  Bybit BTCUSDT trades            COMPLETE
  deterministic source evidence   COMPLETE
  canonicalization                COMPLETE
  historical import tooling       COMPLETE
```

Primary implementation gaps at this scope state:

```text
K08  backup/restore proof          MISSING
A11  live trades acquisition       MISSING
K10  checkpoint/recovery           MISSING
K02  deployment mutation           CONDITIONAL / MISSING
B06  live access/cursor            MISSING but outside this scope
```

One semantic proposition remains deliberately open: **long-gap remediation beyond bounded provider reconciliation**. It is tracked in `OPEN_DECISIONS.md` as `OPEN_BLOCKING` for claiming such a gap filled / for lossless Live Ingest Vertical closeout. It is not permission to invent a repair source.

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
- existing trade-v1 / Dataset / coverage / publication authorities
- existing Bybit historical adapter and certification profile
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
1. K08 implementation + isolated restore proof
        ↓
2. A11 implementation
   Bybit BTCUSDT public live trades
   → canonical trade-v1
   → existing durable publication/catalog lifecycle
        ↓
3. Real server proof
   provider + actual storage/catalog/runtime
   + K02 deployment conformance if authorization boundary changes
        ↓
4. K10 implementation
   checkpoint/recovery against the actual A11 state
        ↓
5. Resolve/execute long-gap remediation only if a real unresolved gap
   and attributable repair source/path exist
        ↓
6. Live Ingest Vertical v1 closeout
```

The accepted ADRs remove the prior semantic-freeze steps; they do **not** authorize concurrent implementation of K08, A11 and K10.

## Active bounded slice — K08 proof

K08 is the first active implementation proposition.

ADR-0039 already freezes the semantics. The exact missing proposition is therefore only the minimum implementation/evidence required to prove:

```text
finalized canonical state
        ↓
identity-bound RecoverySetV1
        ↓
independent primary-storage loss boundary
        ↓
restore into empty isolated target
        ↓
same canonical identities / coverage / catalog resolution
        ↓
existing historical DataGateway reads restored data
```

Do not reopen recovery semantics merely to choose a local copy/snapshot mechanism.

Do not pull K07 or K09 into K08.

## A11 — next bounded implementation slice

After K08 proof, implement exactly ADR-0040.

The first live source remains:

```text
venue       Bybit
category    linear
instrument  BTCUSDT
dataset     trades
schema      trade-v1
source      publicTrade.BTCUSDT
```

Key frozen semantics include:

- `TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)`;
- canonical order `(exchange_ts, trade_id)`;
- provider `seq` is preserved evidence, not a gap-free cursor;
- `receive_ts` remains null in canonical v1;
- same-key equivalent observations deduplicate idempotently;
- same-key conflicting payload fails closed;
- historical/live cutover is explicit and deterministic;
- reconnect uses only bounded provider evidence capable of proving continuity;
- unresolved interruption becomes explicit non-complete coverage, never fabricated completeness;
- transport is at-least-once, canonical economic effect idempotent.

Reuse the existing canonical data plane. Do not create live-only storage/publication/catalog abstractions.

## Long-gap remediation — explicit open block

ADR-0040 deliberately leaves one proposition open.

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

If no authoritative source exists, the gap remains explicit. Missing sequence numbers, elapsed time or absence of trades are never sufficient proof.

Do not speculatively implement archive/backfill machinery before the trigger and source evidence exist.

## Real server proof and K02

A11 is not complete merely because local tests pass. Prove the bounded path against the real deployment environment:

- real Bybit public live source;
- real server runtime;
- real canonical storage paths;
- real publication/catalog state;
- K03 observability;
- K05 pressure behavior where applicable;
- K06 source-protection invariants where applicable.

ADR-0041 already freezes runtime-identity semantics. If deployment creates or changes a production identity/ACL/database-role boundary, conform that mutation to ADR-0041 before treating the service as production-governed.

For public Bybit trade ingest v1 there is no provider API secret to provision.

The normal ingest service must not gain general authority to destroy/replace the independent K08 backup merely for convenience.

## K10 — checkpoint/recovery implementation

After A11 exists, implement exactly ADR-0042 around the real A11 state.

The invariant is:

```text
canonical publication durable
        BEFORE
checkpoint may advance
```

Replay after crash is acceptable; ADR-0040 idempotent deduplication removes already-published duplicates. A checkpoint may never advance beyond durable canonical state.

Restart must validate the checkpoint, reconnect, buffer live evidence, perform bounded provider reconciliation, deduplicate and either prove continuity or record an explicit gap. It must never guess a resume cursor.

## Milestones

### M1 — Live Ingest Capable

```text
K08 proof
+ real provider connection
→ real trades
→ canonical trade-v1
→ durable canonical publication
```

### M2 — Live Ingest Hardened

```text
M1
+ governed historical/live cutover
+ disconnect/reconnect correctness
+ K02-conformant deployment boundary where activated
+ checkpoint/recovery
+ real restart proof
+ no unresolved gap falsely claimed complete
```

The macro-scope closes only at M2 and only with the long-gap proposition honestly represented: either no qualifying long gap occurred, or any occurred gap has been repaired from attributable evidence, or the scope closeout explicitly retains the non-complete interval rather than claiming lossless continuity.

## Acceptance

`Live Ingest Vertical v1` is DONE only when all of the following are observably true:

1. K08 isolated restore proof satisfies ADR-0039.
2. A real Bybit public live connection acquires BTCUSDT linear trades.
3. Real live evidence maps into canonical `trade-v1` exactly under ADR-0040.
4. Historical/live overlap has one deterministic governed result with no ungoverned duplicate economic trades.
5. Disconnect/reconnect follows ADR-0040 and never fabricates continuity.
6. Live-acquired trades enter the existing certification/publication/catalog lifecycle.
7. Coverage/completeness claims derive from explicit evidence; absence of trades is not proof.
8. Existing K05/K06 semantics remain applicable and are not bypassed.
9. K03 makes the real ingest operationally observable at the needed failure/provenance level.
10. Any production identity/ACL/role mutation conforms to ADR-0041.
11. K10 stop/crash/restart proof satisfies ADR-0042.
12. Data acquired live becomes ordinary canonical historical data readable through the existing DataGateway after publication.
13. Any long gap that cannot be authoritatively reconstructed remains explicit non-complete coverage; no closeout claim silently erases it.
14. No B06 live consumer, Strategy, Execution, ML/RL, client or API capability is required.
15. Existing historical Bybit ingestion and canonical data-plane behavior remain valid.
16. Package/architecture boundaries remain intact.
17. Required authoritative verification for each bounded slice passes with no unresolved blocker.

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
- K08 proof requires destructive/shared-state action outside authorization;
- A11 cannot satisfy its frozen provider/canonical semantics without changing accepted upstream contracts;
- provider evidence cannot support a claimed ordering/dedup/coverage/reconciliation proposition;
- real deployment requires authorization beyond ADR-0041;
- K10 cannot preserve publication/checkpoint invariants without upstream semantic change;
- filling an actual long gap requires choosing an unproven repair source/semantics.

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
- mutation of `main` except through separately authorized integration/promotion workflow.

## Scope closeout

At closeout, reconcile governance to the strongest propositions actually proven by integrated runtime and real-server evidence.

Do not declare the whole Live Data Plane, Wave 6, B06 or the live trading product complete merely because this first ingest vertical is complete.

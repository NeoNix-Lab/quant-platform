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

Authoritative baseline at scope selection:

```text
implement/wave-3 @ 3ccada1477c1f995b3c0f88500a34acb4f65d3f3
```

Wave 3 is complete through F08 and remains credited. Its final governance reconciliation is part of the baseline, not work to reopen.

Relevant credited state includes:

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

Primary live-path gaps at scope start:

```text
K08  Backup/restore proof          OPEN_BLOCKING / MISSING
A11  Live trades acquisition       OPEN_BLOCKING / MISSING
K10  Checkpoint/recovery           OPEN_BLOCKING / MISSING
B06  Live access/cursor            MISSING but not required for this scope
```

DG-B live semantics remain unresolved and must be frozen before A11 implementation where they affect correctness.

If the branch advances before a bounded slice starts, use the current fast-forward descendant and verify only directly relevant authority and evidence.

## Authority

Use the existing canonical authority set rather than recreating live semantics from conversational history.

High-value starting authorities are:

- `AGENTS.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/ROADMAP.md`
- `docs/architecture/OPEN_DECISIONS.md`
- `docs/decisions/ADR-0013-historical-live-semantics.md`
- existing trade-v1 / Dataset / coverage / publication authorities
- existing Bybit source-owned historical adapter and first-vertical certification profile
- existing K03/K04/K05/K06 runtime and evidence

Use progressive discovery:

```text
named authority
→ directly affected live/source/operations code and proof
→ expand only for a concrete unresolved proposition
```

Do not perform repository-wide rediscovery merely because this is a new macro-scope.

## Execution policy

Only one bounded implementation or decision-gate mutation slice is active at a time.

The intended path is:

```text
1. K08 backup/restore
   decision freeze + minimum independent restore proof
        ↓
2. DG-B live semantics
   historical/live overlap
   source precedence
   duplicate semantics
   live cursor / resume / replay identity
        ↓
3. A11 live trades acquisition
   Bybit BTCUSDT public live trades
   → canonical trade-v1
   → normal durable publication/catalog lifecycle
        ↓
4. Real server proof
   real provider + real storage/catalog
   continuous acquisition under actual operational constraints
        ↓
5. K10 checkpoint/recovery
   harden the state that A11 actually creates
   restart/reconnect proof
        ↓
6. Live Ingest Vertical v1 COMPLETE
```

This sequence is an outcome path, not authorization for concurrent implementation of every step. Each bounded slice must preserve the repository single-scope rule.

## First bounded slice — K08

K08 is the first real blocker on the A11 path.

The purpose is not to build a generic enterprise backup platform. The minimum required outcome is to freeze the recovery objective/topology necessary for this vertical and prove that the required canonical identities/state can be restored independently enough to permit live acquisition safely.

Do not pull K07 tier relocation or K09 retention/deletion into K08 merely because they are adjacent operational capabilities.

Minimum principle:

```text
existing canonical state
        ↓ backup
independent loss/failure condition
        ↓ restore
required identities/state recoverable
        ↓ proof
```

Use existing evidence where sufficient. Add only the missing proof proposition.

## DG-B live semantics

Before A11 materializes behavior that depends on them, freeze only the live semantics required by the first vertical.

At minimum resolve:

- historical/live overlap semantics;
- source precedence in the overlap window;
- duplicate identity and duplicate handling;
- deterministic ordering assumptions actually supported by Bybit live evidence;
- reconnect/resume/replay semantics;
- canonical live cursor/resume identity only to the extent required by A11;
- coverage/quality evidence required to distinguish genuine absence from missing acquisition;
- the minimum operational constraints required by the live source.

The central correctness questions are:

```text
historical coverage ends near T
live acquisition starts near T
→ exactly one deterministic canonical history
```

and:

```text
connection drops
→ reconnect / recovery path
→ no ungoverned loss
→ no ungoverned duplication
```

Do not generalize DG-B into multi-venue, generic stream processing or future L2/L3 semantics.

## A11 — first live acquisition vertical

The first live source is intentionally narrow:

```text
venue       Bybit
category    linear
instrument  BTCUSDT
dataset     trades
schema      trade-v1
```

A11 should materialize the minimum source-owned live acquisition seam necessary to transform real provider evidence into the existing canonical trade path.

Expected conceptual flow:

```text
Bybit live source
        ↓
connection/session handling
        ↓
source message validation
        ↓
source-native ordering / identity evidence
        ↓
TradeRecord / trade-v1
        ↓
DG-B overlap / duplicate semantics
        ↓
existing canonical materialization / certification / publication / catalog
```

Reuse existing canonical machinery. Do not build a second live-only publication system.

Do not fabricate source fields the provider does not evidence. In particular, any receive-time, sequence, ordering or continuity field must have explicit source/runtime semantics rather than being filled for convenience.

Coverage remains evidence-based:

```text
no trade observed
≠
proven data gap
```

A complete/healthy interval must be supported by the live acquisition evidence actually capable of proving it.

## Real server proof

The first vertical is not complete merely because unit/integration tests pass locally.

After A11 is stable, prove the bounded path against the real deployment environment:

- real Bybit public live source;
- real server process/runtime;
- real canonical storage paths;
- real publication/catalog state;
- existing K03 observability;
- existing K05 pressure policy where applicable;
- existing K06 source-protection invariants where applicable.

The proof must demonstrate that real live-acquired data becomes normal canonical data and is subsequently readable through the existing historical DataGateway path.

This does **not** require B06 live consumer access.

## K10 — checkpoint/recovery hardening

K10 follows A11 because checkpoint/recovery must be designed around the real state that A11 actually owns.

Do not create a generic checkpoint framework before that state exists.

After A11 is proven, K10 should provide the minimum sufficient persisted/recoverable state needed so that controlled stop, crash/disconnect and restart do not produce ungoverned loss or duplication.

Conceptually:

```text
A11 real runtime state
        ↓
minimum checkpoint identity/state
        ↓
stop / disconnect / restart
        ↓
resume under frozen DG-B semantics
        ↓
canonical history remains governed
```

## Conditional K02 activation

K02 is not automatically part of A11 implementation.

Activate only the minimum K02 branch if the real server deployment introduces an actual new production boundary requiring a decision about:

- service/runtime identity;
- credentials;
- filesystem ACLs;
- database roles;
- least privilege.

Do not invent runtime identities or credential systems before a concrete deployment need exists.

## Milestones

### M1 — Live Ingest Capable

```text
real provider connection
→ real trades
→ canonical trade-v1
→ durable canonical publication
```

M1 proves the first real acquisition path works.

### M2 — Live Ingest Hardened

```text
M1
+ backup/restore proof
+ governed historical/live overlap
+ disconnect/reconnect correctness
+ checkpoint/recovery
+ real restart proof
```

The macro-scope closes only at M2.

## Acceptance

`Live Ingest Vertical v1` is DONE only when all of the following are observably true:

1. A real Bybit public live connection acquires BTCUSDT linear trades.
2. Real live evidence is mapped into canonical `trade-v1` without fabricated source semantics.
3. Historical-to-live overlap has one deterministic governed result and does not create ungoverned duplicate economic trades.
4. Disconnect/reconnect behavior follows frozen DG-B semantics and does not create ungoverned data loss or duplication.
5. Live-acquired trades enter the existing canonical certification/publication/catalog lifecycle rather than a parallel live-only path.
6. Coverage/completeness claims derive from explicit evidence; absence of trades is never treated by itself as proof of a gap or proof of completeness.
7. Existing K05 pressure and K06 source-protection semantics remain applicable and are not bypassed.
8. K08 provides the minimum sufficient independent backup/restore proof required by the live path.
9. K03 makes the running ingest path operationally observable at the level needed to diagnose source/runtime failure.
10. Controlled stop/restart and relevant disconnect/recovery cases resume from governed state under K10 without corrupting canonical history.
11. Data acquired live becomes ordinary canonical historical data readable through the existing DataGateway path after publication.
12. No B06 live consumer, Strategy, Execution, ML/RL, client or API capability is required to satisfy this scope.
13. Existing historical Bybit ingestion and canonical data-plane behavior remain valid unless an accepted live authority explicitly requires a bounded compatibility correction.
14. Repository package/architecture boundaries remain intact.
15. Required authoritative verification for each bounded slice passes with no unresolved blocker.

## Verification proportionality

For every bounded slice:

- CREDIT existing canonical data-plane and operations evidence where unchanged;
- use targeted tests/checks during implementation;
- prove only the exact missing proposition introduced by that slice;
- run repository-required authoritative verification once on a stable candidate;
- do not repeat expensive verification absent a relevant mutation, failure, review finding, rebase/merge or newly unresolved proposition;
- use real-server proof only when the acceptance proposition actually requires the real provider/runtime/storage environment.

Do not add new harnesses/frameworks merely to create a second proof layer for properties already proven elsewhere.

## Stop / escalation

Stop only when:

- current repository state contradicts canonical live/data/operations authority;
- K08 cannot be satisfied without a destructive/shared-state action outside authorization;
- DG-B requires a genuine semantic choice not owned by existing authority;
- A11 would require changing frozen historical/canonical semantics rather than a bounded live adaptation;
- satisfying the first vertical requires loosening package/owner dependency direction;
- the provider cannot supply evidence needed for a claimed ordering, deduplication, coverage or resume proposition;
- real deployment requires a production identity/credential mutation beyond the explicitly activated K02 boundary;
- checkpoint/recovery cannot be represented without changing frozen upstream semantics.

Do not stop for local reversible choices already inside a bounded slice.

## Out of scope

Do not pull the following into this macro-scope unless a concrete acceptance blocker proves otherwise:

- B06 live DataGateway access/cursor for consumers;
- K07 storage tier relocation;
- K09 retention/deletion authority;
- G01+ Strategy / deterministic Replay;
- Execution / paper / live trading;
- I04+ supervised ML or RL work;
- L1 / L2 / L3 / MBO acquisition;
- second venue / multi-venue ingest;
- generic provider framework beyond the minimum second-provider-safe architecture already required by repository boundaries;
- API / client / UI work;
- generic stream-processing framework;
- generic job scheduler/executor;
- unrelated governance cleanup;
- mutation of `main` except through the separately authorized integration/promotion workflow.

## Scope closeout

At closeout, reconcile governance to the strongest propositions actually proven by the integrated runtime and real-server evidence.

Do not declare the whole Live Data Plane, Wave 6, B06 or the live trading product complete merely because this first ingest vertical is complete.

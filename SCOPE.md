# Scope: DG-K — Process Topology v1

Status: **CLOSED — operator closeout, issue #381 (2026-10-16). Part A (design gate) closed by #319 (ADR-0068, ADR-0069 ACCEPTED, 2026-10-10). Part B: `P01`-`P06` implemented and merged into `implement/dgk` (head `93471e4`) within the bounds recorded under "Closeout result". Promotion of `implement/dgk` into `main` is the operator's separate PR. No Golden proof is claimed, and the server/deck split is not composed in any executable (see "Not claimed").**

Scope kind: this document has two parts. Part A recorded the DG-K design gate registered by governance issue #318 and closed by #319. Part B is the implementation inventory that gate makes implementable. Every Part B atom had its own implementation issue citing the governing ADR section and targeting the DG-K integration branch `implement/dgk`; all are merged (see "Closeout result").

---

## Historical scope archive

This file previously recorded **Wave 8 — Full Platform API & Remote Service Topology v1** (CLOSED — operator closeout, issue #327/PR #328, 2026-10-10; implementation promoted to `main` by PR #322 and PR #326; no Golden proof claimed), and then **this same DG-K scope while its design gate was still open** (governance issue #318) and while its implementation was authorized (reconciliation #319). All are preserved in git history (`git log -- SCOPE.md`). `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Closeout result (issue #381)

| Atom | Merged evidence (into `implement/dgk`) | Bound of the credit |
|---|---|---|
| `P01` | PR #344 (issue #330) | `quant_platform.canonical`; byte parity and cross-process proof in `tests/test_canonical_v1.py`. |
| `P02` | PR #349 (#331), PR #350 (#332), PR #355 (#354) | `runtime` schema, unit-scoped roles, atomic K13 registration, concurrent identical resubmission refused. Operational confirmation that no production SQLite runtime state exists is recorded by the maintainer, not by code. |
| `P03` | PR #345 (#333) | Per-host import rules; no host moved. |
| `P04a` | PR #351 (#334) | Closed registry mechanism; `DATA_LOCAL_JOB_REGISTRY` ships **empty**. |
| `P04b` | PR #352 (#335) | `tools/job_worker.py`, PostgreSQL advisory lock plus process-lifetime lock (ADR-0068 Amendment 1). |
| `P05a` | PR #346 (#336), #347 (#337), #348 (#338), #380 (#339) | Per-family dispatch and scopes before decode; `j02-strategy-v1`, `j02-validation-v1`, `j02-training-v1` (ADR-0069 Amendment 2). |
| `P05b` | PR #353 (#340), PR #359 (#358) | `j02-job-v1`, opt-in handlers. |
| `P06` | PR #356 (#341), #361 (#342), #366 (#343), #373 (#357) | K12 delivery as `j02-admitted-input-v2`, K13 import with feature-provider identity, `clients/deck` protocol client; ADR-0064 Am.1, ADR-0066 Am.1, ADR-0069 Am.1. |

Amendments that landed: ADR-0064 Amendment 1, ADR-0066 Amendment 1, ADR-0068 Amendment 1, ADR-0069 Amendments 1 and 2.

## Not claimed (verified against `implement/dgk` at `93471e4`)

- No executable composes the new families: `tools/api_server.py` serves the J02 v1 market-data message only and has no TLS/mTLS options; `RemoteJ02SecurityConfig` is constructed only in tests.
- No production implementation of the K12 server-owned seams (`resolve`, `members`, `read_member`); no composition of `GovernedResultImportService` (K13) in a host; no output-contract registry.
- No deck-side executable or producer of `GovernedResultBundleV1` for a deck workload; `clients/deck/` is a protocol client only.
- No data-local J03 handler (the registry ships empty), so `j02-job-v1` has nothing to submit.
- No credential issuance, trust-bundle management, secret storage or deployment runbook for the new scopes (ADR-0063 leaves them out of scope).
- No end-to-end proof on the real topology (PostgreSQL + API + worker + deck over WSS/mTLS); the acceptance tests use SQLite or `FakeConnection` stores behind a real WSS/mTLS listener, and the PostgreSQL suite is separate.
- No Golden proof. Omega use of any new family (needs an ADR-0067 amendment); more than one J03 worker; a durable J14 transfer store (in-memory by design, ADR-0064 Amendment 1 point 7).
- `J11` is not planned and remains `OPEN_DEFERABLE`/`MISSING`.

The statement that the topology is deployable, or that the server/deck split is ready, must not be derived from this scope.

---

## Why this scope exists

ADR-0024 §6 left process topology, host placement and service split open. The Wave 8 closeout (#327) credited `J03`, `J10`, `J12`, `J13`, `K12` and `K13` only within explicit bounds: no J03 dispatch or worker, no J02 carriage of the consumer seams, no byte transfer between server and deck, and a non-atomic K13 registration across PostgreSQL and SQLite. The maintainer's direction (2026-10-07, refined 2026-10-10) is a modular monolith deployed as a small fixed set of processes, with heavy compute on the deck and the server kept as free as possible.

---

## Owner decisions (inputs the design gate honored)

1. P01 preserves every existing identity hash; RFC 8785 is one profile, for `J14` and new contracts only.
2. P03 keeps executable hosts in `tools/`.
3. P04 v1 runs exactly one J03 worker process.
4. P05 belongs to DG-K, not Wave 8 (2026-10-08).
5. The server stays as free as possible: server-side J03 handlers are data-local only; replay, sweeps, training and large PBO run on the deck, on the deck's own initiative; the server never delegates jobs to the deck or any other machine (2026-10-10).
6. `J11` is not planned: replay runs only on the deck over `K12`-admitted inputs, results return through `K13` (2026-10-10).

---

## Part A — Design gate outcome, CLOSED

| Gate | Issue | ADR | Disposition | One-line resolution |
|---|---|---|---|---|
| **DG-K** topology | #319 | ADR-0068 | ACCEPTED | Five units (ingest, platform API, exactly one J03 worker, PostgreSQL, deck compute); domain packages stay in-process libraries; `runtime` schema with per-unit roles and atomic K13 registration; four named identity byte profiles that change no existing bytes; closed data-local J03 handler registry; single worker enforced by a PostgreSQL advisory lock; hosts stay in `tools/`. |
| **DG-K** wire and handoff | #319 | ADR-0069 | ACCEPTED | Additive J02 message families authorized before decode, one least-privilege scope per family; synchronous `J10`/`J12`/`J13` with unchanged budgets; J03 submit/status/result/cancel; deck-initiated `K12` delivery and `K13` submission; feature-provider code identity in replay result bundles; deck client outside `src/quant_platform`. |

Answers to the nine DG-K questions are in ADR-0068 §1-§9 and ADR-0069 §1-§9; `OPEN_DECISIONS.md` (DG-K) records them in summary. Neither ADR amends another ADR or changes an existing identity. Work that ADR-0065 Amendments 1 and 2 left for "a separate J03 dispatch design gate" (over-budget PBO and training) is resolved as deck work, not server J03 work (ADR-0068 §3).

---

## Part B — Authorized DG-K implementation inventory

Each atom below has an `ACCEPTED` governing ADR and was delivered by its own implementation issue (see "Closeout result"). The table is kept as the authorized inventory and its start gates.

| Atom | Capability | Governing ADR | Requires | Sequencing note |
|---|---|---|---|---|
| `P01` | Canonical identity serializer v1 | ADR-0068 §5 | — | Independent. Byte parity on every pinned identity and a two-`PYTHONHASHSEED` cross-process proof are acceptance. |
| `P02` | Runtime store on PostgreSQL v1 | ADR-0068 §4 | `J03`, `K12`, `K13` (on `main`) | Independent. Must establish atomic K13 registration and confirm no production SQLite runtime state is lost. |
| `P03` | Executable host boundaries v1 | ADR-0068 §7 | — | Independent. Boundary-test rules only; no host moves. |
| `P04a` | J03 handler registry and dispatch v1 | ADR-0068 §6 | `P01`, `P02` | Start gate: HOLD until `P01` and `P02` are merged. Data-local handlers only. |
| `P04b` | Single J03 worker process v1 | ADR-0068 §6, §8 | `P03`, `P04a` | Start gate: HOLD until `P03` and `P04a` are merged. |
| `P05a` | J02 carriage of synchronous J10/J12/J13 v1 | ADR-0069 §1-§3 | `J02`, `J09` (on `main`) | Independent. |
| `P05b` | J02 submit/status/result for J03 data-local jobs v1 | ADR-0069 §1, §2, §4 | `P04b` | Start gate: HOLD until `P04b` is merged. |
| `P06` | Deck handoff over J02 v1 | ADR-0069 §1, §2, §5-§8 | `J09`, `J14` (on `main`), `P02` | Start gate: HOLD until `P02` is merged. |

```text
P01 ──┐
P02 ──┼─> P04a ─> P04b ─> P05b
P03 ──┘     (P04b also requires P03)
P05a       (J02/J09 on main)
P06        (J09, J14 on main; requires P02)
```

All atoms were started in dependency order and are merged.

### Integration branch

Default proposed by this reconciliation: one integration branch `implement/dgk` cut from `main` when the first Part B issue opens; atomic slices use `agent/issue-<N>-<slug>` from it and target it (`AGENTS.md`, Branching). DG-K is a design-gate line, not a numbered wave; the maintainer may rename the branch before it is created.

---

## Authority

- `AGENTS.md`
- governance issue #318; design-gate issue #319; Wave 8 closeout #327
- ADR-0068 (process topology v1), ADR-0069 (J02 carriage of consumer seams and deck handoff v1)
- ADR-0018, ADR-0024, ADR-0043, ADR-0050 (and Amendment 1), ADR-0055, ADR-0057, ADR-0058, ADR-0062 through ADR-0067
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`DG-K` section; `P` atoms), `docs/architecture/OPEN_DECISIONS.md` (`DG-K`)

Accepted ADRs and frozen contracts remain normative. No Part B issue may change an existing identity, ADR-0062's lifecycle or attempt model, ADR-0064's sealed-input identity, ADR-0043's host placement, J02 v1, or the frozen Consumer API.

---

## Baseline and Credited State

```text
main @ 56f6375 (Wave 8 governance closeout #327 / PR #328 merged) at registration
Wave 8: J03,J09,J10,J12,J13,J14,J15,K12,K13 COMPLETE within recorded bounds
DG-K: ADR-0068, ADR-0069 ACCEPTED (#319); P01-P06 FROZEN / COMPLETE within recorded bounds (#381)
implement/dgk @ 93471e4 (not yet promoted into main)
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records. `J14`'s RFC 8785 framing, `J15`'s `clients/omega` location, #313's replay identity parity tests and the K12/K13 server logic are reused as evidence, not rebuilt.

---

## Explicit non-claims

- `P01`-`P06` are `COMPLETE` only within the bounds under "Closeout result"; the list under "Not claimed" applies.
- `J11` is not planned and remains `OPEN_DEFERABLE`/`MISSING`.
- Omega remote use of ADR-0069 families needs its own ADR-0067 amendment and is not authorized.
- `J07`/`J08` (Wave 10), `I06`/`I07` (Wave 9), `A13`-`A15` and second venue are untouched.

---

## Mutation policy

- Each Part B implementation issue produces code and tests for exactly one atom.
- Branch `agent/issue-<N>-<slug>` (or `codex/issue-<N>-<slug>`) from the DG-K integration branch and target it, not `main`.
- Do not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` as part of implementation work (`AGENTS.md` Governance boundary). Governance PRs use a `governance/` or `admin/` head branch.
- Start gates in the Part B table are binding: `P04a` after `P01`+`P02`; `P04b` after `P03`+`P04a`; `P05b` after `P04b`; `P06` after `P02`.

---

## Exit criteria for this scope

- Part A: **met** — ADR-0068 and ADR-0069 `ACCEPTED` (#319), reconciled here.
- Part B: each of the eight atoms is implemented and merged into the integration branch, within the acceptance its governing ADR section states: **met** (closeout #381). Promotion of `implement/dgk` into `main` remains the operator's separate PR.

---

## Out of Scope

- a message broker, a distributed scheduler, a generic executor or a remote-execution framework;
- server-to-deck (or server-to-any-machine) job delegation, and remote workers pulling from the server J03 queue;
- one deployed service per domain package;
- a canonical-storage migration to the deck (ADR-0057 §6); moving runtime stores to PostgreSQL is not that migration;
- changing any existing identity bytes (an ADR-0018 contract evolution);
- the `J11` replay seam (not planned) and `D05` bar-input replay;
- a binary transport family or a second transport stack;
- L2 acquisition, publication and live daemon semantics (`A13`-`A15`);
- Omega remote use of ADR-0069 families (ADR-0067 amendment);
- `J07`/`J08`, `I06`/`I07`, second venue, multi-asset execution;
- repository mirroring outside Git history.

---

## Stop / Escalation Conditions

Stop and report rather than implement or decide unilaterally if:

- an implementation issue cannot satisfy its ADR-0068/ADR-0069 section without weakening an accepted ADR or changing an existing identity;
- a Part B issue starts before its start gate is met;
- a handler outside ADR-0068 §6's data-local registry rule is proposed for J03;
- implementing an atom would require answering a design question the ADRs leave open.

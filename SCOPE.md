# Scope: DG-K — Process Topology v1 (design gate)

Status: **OPEN — design gate only (governance issue #318, 2026-10-10). No implementation is authorized by this scope.** The single design-gate issue is #319 (ADR-0068 process topology v1, ADR-0069 J02 carriage of consumer seams and deck handoff v1).

Scope kind: this is a Part A-style design-gate sequencing scope. It registers the open decisions, fixes the owner decisions already taken as inputs, and names the candidate atoms the gate must make implementable. A Part B implementation inventory is added only by a later governance reconciliation, after #319's ADRs are `ACCEPTED` (or #319 closes with an explicit "keep single process" disposition and evidence trigger).

---

## Historical scope archive

This file previously recorded **Wave 8 — Full Platform API & Remote Service Topology v1** (CLOSED — operator closeout, issue #327/PR #328, 2026-10-10; implementation promoted to `main` by PR #322 and PR #326, `main` @ `cf1c510`; no Golden proof claimed). That content, including the Wave 8 closeout table of credited bounds, is preserved in git history (`git log -- SCOPE.md`) and is not duplicated here. `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Why this scope exists

ADR-0024 §6 and its implementation outcome leave open "whether any component later becomes an independently deployed service", executable host placement and process topology. The Wave 8 closeout (#327) credited `J03`, `J10`, `J12`, `J13`, `K12` and `K13` only within explicit bounds and carried the missing pieces here:

- `J03` has a durable admission record but no handler dispatch, worker process or PostgreSQL store (`application/durable_jobs.py:3-5`);
- `J10`/`J12`/`J13` are in-process seams with no J02 wire message or remote scope (owner decision 2026-10-08);
- `K12` seals a manifest but transfers no bytes to the deck; `K13` writes the Experiment to PostgreSQL and the evidence to SQLite with no common transaction (`application/governed_result_import.py:268-288`);
- identity bytes are produced by several hand-written canonical JSON sites with three distinct byte profiles and no single module.

The maintainer has chosen a direction (2026-10-07, refined 2026-10-10): **a modular monolith deployed as a small fixed set of processes, not one service per domain package, with heavy compute on the deck and the server kept as free as possible.** This scope records that direction as an open design gate; it decides no topology itself.

---

## Owner decisions (fixed inputs to the design gate)

Recorded from the architecture review and maintainer answers of 2026-10-08 and 2026-10-10 (`main` @ `cf1c510`):

1. **P01 preserves every existing identity hash.** RFC 8785 re-encodes floats (`2.0` -> `2`, `1e-07` -> `1e-7`) and non-ASCII strings, and floats enter identities today (`validation/robustness.py:173-185`). The code already uses three byte profiles: sorted/compact/ASCII (e.g. `application/durable_jobs.py:329`), sorted/compact/UTF-8 for frozen data-plane contracts (`representation/candles.py:186`, `representation/footprints.py:219`, `data/models.py:448`), and sorted with default separators (`application/retention_deletion.py:103`). P01 is one module with named, versioned profiles that reproduce today's bytes; RFC 8785 (`rfc8785` library, as used by `J14` in `application/framed_result_transport.py:13`) is one of those profiles, for `J14` and new contracts only. Any change to existing identity bytes needs a separate ADR-0018 contract evolution.
2. **P03 keeps executable hosts in `tools/`.** `tools/live_ingest_server.py` and `tools/api_server.py` are the existing hosts, named normatively by ADR-0043 and used by `infra/systemd/quant-platform-live-ingest.service`. No `apps/` move, so no amendment to ADR-0043/ADR-0050.
3. **P04 v1 runs exactly one J03 worker process.** `DurableJobStore.recover_after_restart` (`application/durable_jobs.py:244-264`) marks every `RUNNING` job `RECOVERY_REQUIRED`; per-process attempt ownership would need an amendment to ADR-0062 §4. More than one worker is deferred behind a recorded evidence trigger.
4. **P05 belongs to DG-K, not Wave 8** (2026-10-08).
5. **The server stays as free as possible; heavy compute runs on the deck, on the deck's own initiative** (2026-10-10). The server never delegates or pushes jobs to the deck or any other machine, and no remote worker pulls from the server queue (ADR-0062 §1/§6, ADR-0057 §6). Server-side J03 handlers are limited to **data-local** work (feature/representation materialization, historical imports, catalog maintenance); replay, sweeps, training and large PBO runs belong on the deck.
6. **`J11` (Replay Consumer-API seam) is not planned** (2026-10-10). Replay runs only on the deck, in-process, over `K12`-admitted inputs, with results returned through `K13`. `J11` stays `OPEN_DEFERABLE`; its trigger is an explicit maintainer request for server-side or remote-client replay. No `J11` design gate follows DG-K.

---

## Design gate

| Gate | Issue | Output | Atoms it must make implementable |
|---|---|---|---|
| **DG-K** | #319 | ADR-0068 (provisional) process topology v1; ADR-0069 (provisional) J02 carriage of consumer seams and deck handoff v1 | `P01`, `P02`, `P03`, `P04a`, `P04b`, `P05a`, `P05b`, `P06` |

Valid closes: `ACCEPTED` ADRs, or an explicit "keep single process" disposition with a recorded evidence trigger. Neither ADR may create one service per domain package.

### Candidate atoms (names provisional; final names are the design gate's call)

| Atom | Proposition | Requires |
|---|---|---|
| `P01` | Canonical identity serializer: one module, named versioned byte profiles, every existing site declares its profile; golden-hash parity on every existing identity vector (including the replay vectors pinned by #313); no identity changes | DG-K |
| `P02` | Runtime store on PostgreSQL: a `runtime` schema and DB role for the `J03`/`K12`/`K13` stores (today SQLite); establishes atomic `K13` registration with the Experiment write | DG-K, `J03`, `K12`, `K13` |
| `P03` | Executable hosts in `tools/` with per-host import boundaries in `tests/test_package_boundaries_v1.py`; `clients/*` (including `clients/omega`) stay outside `tools/` and `src/quant_platform` | DG-K |
| `P04a` | `J03` closed, data-local handler registry and dispatch inside `quant_platform.application` | DG-K, `P01`, `P02` |
| `P04b` | Single `J03` worker process (PostgreSQL-backed queue; no broker, no generic executor) | DG-K, `P03`, `P04a` |
| `P05a` | J02 wire messages and one remote scope per operation family for the synchronous `J10`/`J12`/`J13` operations | DG-K, `J02`, `J09` |
| `P05b` | J02 submit/status/result messages for data-local operations admitted through `J03` | DG-K, `P04b` |
| `P06` | Deck handoff over J02: `K12` delivery of admitted input bytes (`J14` framing for large payloads) and `K13` result-bundle submission, one least-privilege scope each, a deck-side client that verifies delivered digests and submits the bundle; the `K13` bundle carries the replay feature-provider code identity | DG-K, `J09`, `J14`, `P02` |

```text
P01 ──┐
P02 ──┼─> P04a ─> P04b ─> P05b
P03 ──┘     (P04b also requires P03)
P05a       (J02/J09 only; already on main)
P06        (J09, J14 on main; requires P02)
```

### Question set (see `docs/architecture/OPEN_DECISIONS.md`, DG-K)

1. Which deployable units exist (proposed: ingest per venue/stream, platform API, one J03 worker, deck compute, PostgreSQL; live/paper execution deferred to Wave 10)?
2. Which domain packages remain in-process libraries in every unit, and why (replay calls `feature_provider` per tick, `src/quant_platform/replay/__init__.py:311,370`)?
3. Which PostgreSQL schema and DB role each unit owns (extending `db/init/002_roles.sh`)?
4. How `J03`/`K12`/`K13` state moves to the `runtime` schema and how `K13` registration becomes one transaction with the Experiment write?
5. Which byte profile each existing identity site uses, how P01 proves parity, and how code identity is checked between units (ADR-0057 §2)?
6. How `tests/test_package_boundaries_v1.py` extends to per-host rules for `tools/`?
7. Which J02 wire messages and remote scopes carry `J10`/`J12`/`J13` (P05a/P05b), which operations are synchronous versus admitted through `J03`, and how a `J13` request carrying a full `SupervisedProjection` stays within the J02 16 MiB message bound (ADR-0050 §2; `J14` frames results only, ADR-0066)?
8. Which evidence trigger re-opens more than one J03 worker (ADR-0062 §3/§4)?
9. Which wire messages, scopes and deck-side client carry `K12` delivery and `K13` submission (P06), and which feature-provider identity the `K13` bundle must carry?

---

## Authority

- `AGENTS.md`
- governance issue #318; design-gate issue #319; Wave 8 closeout #327
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`DG-K` section; `P` atoms), `docs/architecture/OPEN_DECISIONS.md` (`DG-K`)
- ADR-0018, ADR-0024 (§3, §6, implementation outcome), ADR-0043, ADR-0050 (and Amendment 1), ADR-0055, ADR-0057, ADR-0058, ADR-0062 through ADR-0067
- `docs/contracts/CORE_CONTRACTS.md` §1, §32; `docs/contracts/DATA_GATEWAY.md`
- `tests/test_package_boundaries_v1.py`

Accepted ADRs and frozen contracts remain normative. ADR-0024, ADR-0043, ADR-0057, ADR-0062 and ADR-0064 in particular are not weakened by registering this gate.

---

## Baseline and Credited State

```text
main @ 56f6375 (Wave 8 governance closeout #327 / PR #328 merged)
Wave 8: J03,J09,J10,J12,J13,J14,J15,K12,K13 COMPLETE within recorded bounds
        (CAPABILITY_DAG.md rows); J11 OPEN_DEFERABLE; no Golden proof claimed
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records. In particular `J14`'s RFC 8785 framing, `J15`'s `clients/omega` location and #313's replay identity parity tests are reused as evidence, not rebuilt.

---

## Explicit non-claims

- DG-K and `P01`-`P06` are `OPEN_BLOCKING`/`MISSING`; nothing here is decided or built.
- `J10`/`J12`/`J13` remain unreachable over J02 until `P05a` is implemented.
- `J11` is not planned and remains `OPEN_DEFERABLE`/`MISSING`.
- Omega remote use of P05/P06 needs its own amendment to ADR-0067 (which limits `J15` to `j02.market_data.read`) and is not authorized here.
- `J07`/`J08` (Wave 10), `I06`/`I07` (Wave 9), `A13`-`A15` and second venue are untouched.

---

## Mutation policy

- #319 produces ADRs only; it implements no code and opens no implementation issue.
- Implementation issues for the `P` atoms are opened only by a later governance reconciliation that adds a Part B inventory to this file.
- Do not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` outside a governance-designated issue (`AGENTS.md` Governance boundary). Governance PRs use a `governance/` or `admin/` head branch (`tools/check_pre_push.py`, `tools/workflow.py`).

---

## Exit criteria for this scope

- #319 closes with ADR-0068 and ADR-0069 `ACCEPTED`, or with an explicit "keep single process" disposition and a recorded evidence trigger;
- each candidate atom is either implementable without further semantic decisions, in the recorded dependency order, or explicitly deferred with a trigger;
- a governance reconciliation records the outcome and, if any atom is authorized, a Part B implementation inventory.

---

## Out of Scope

- a message broker, a distributed scheduler, a generic executor or a remote-execution framework;
- server-to-deck (or server-to-any-machine) job delegation, and remote workers pulling from the server J03 queue;
- one deployed service per domain package;
- a canonical-storage migration to the deck (ADR-0057 §6); moving runtime stores to PostgreSQL is not that migration;
- changing any existing identity bytes (that is an ADR-0018 contract evolution);
- the `J11` replay seam (not planned) and `D05` bar-input replay;
- L2 acquisition, publication and live daemon semantics (`A13`-`A15`, #316 follow-ups): DG-K defines only the ingest process shell;
- Omega remote use of P05/P06 (needs an ADR-0067 amendment);
- `J07`/`J08`, `I06`/`I07`, second venue, multi-asset execution;
- repository mirroring outside Git history.

---

## Stop / Escalation Conditions

Stop and report rather than decide unilaterally if the design gate would require:

- weakening ADR-0057's server authority, ADR-0062's no-automatic-retry rule or attempt model, ADR-0064's sealed-input identity, ADR-0043's host placement, or the frozen Consumer API;
- changing any existing identity;
- implementing code to answer a design question.

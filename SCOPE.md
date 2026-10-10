# Scope: Wave 8 - Full Platform API & Remote Service Topology v1

Status: **CLOSED — operator closeout (issue #327, 2026-10-10). All nine Part B atoms are implemented and promoted to `main` (PR #322 `implement/wave-8` -> `main`; PR #326 `implement/wave-8.1` -> `main`; `main` @ `cf1c510`). No Wave 8 Golden E2E proof artifact is claimed (see Closeout result).**

Scope kind: this document has three parts. Part A (closed) was the design-gate sequencing scope. Part B (closed) authorized the implementation inventory and resolved the branch-baseline question. The Closeout result below records what was actually built and promoted, and what is explicitly not claimed. `implement/wave-8` and `implement/wave-8.1` are closed integration branches; no further issue targets them.

`implement/omega` is **CLOSED** as the bounded `DG-J` design-integration line (head `f79f1313` at design-gate closeout). It was then promoted into `main` by PR #305 (merge commit `30c48094`, tag `omega-stabilization-dgj-v1`) so that `implement/wave-8` could branch from `main` carrying the governance state it depends on. No further design-gate or implementation issue targets `implement/omega`.

---

## Historical scope archive

This file previously recorded **Wave 7 — API & Platform Transport v1** (CLOSED — exceptional operator closeout; `implement/wave-7` governance-closeout tip `73d5f7b2`, tag `wave-7-api-platform-transport-v1`, merged to `main` by `5c8d9af`), and then **this same Wave 8 scope while its design gates (G1-G6) were still open** (governance issue #280/PR #279). That content is fully preserved in git history (`git log -- SCOPE.md`) and is not duplicated here. `SCOPE.md` is overwritten in place for each governance reconciliation; no wave's scope has ever been copied aside to a separate archived file, and this one is not an exception.

---

## Closeout result (issue #327)

Closed by explicit maintainer direction on 2026-10-10 ("effettua il close out del ramo già mergiato") after every Part B implementation issue was merged and promoted to `main`.

```text
Wave 8 integration branch: implement/wave-8 (from main @ 30c48094)
implement/wave-8 head at promotion: 2a8ed1b15e9fb61d92a5ee141109980323e03158
Promotion to main: PR #322 (merge b5df0f8)
Follow-up integration branch: implement/wave-8.1 (from main @ b5df0f8)
implement/wave-8.1 head at promotion: f1aa0f1a23c1f950405de2f0b12b32a8df428c0d
Promotion to main: PR #326 (merge cf1c510)

J03 durable job runtime            issue #296  PR #307
J10 Strategy seam                   issue #300  PR #308
K12 admitted-input manifest         issue #298  PR #309
J09 authenticated/TLS J02           issue #297  PR #310
K13 governed result import          issue #299  PR #311
J12 Validation seam                 issue #301  PR #312
J13 Training seam                   issue #302  PR #315
J14 framed result transport         issue #303  PR #320
J15 Omega remote client             issue #304  PR #321
Owner-accepted extra: replay ledger identity cost       issue #313  PR #323 (implement/wave-8.1)
Owner-accepted extra: sub-quadratic uniqueness weights  issue #314  PR #324 (implement/wave-8.1)
Emergency slice outside the inventory: A14 L2 groundwork  issue #316  PR #317
```

What each atom is credited for (exactly its implementation issue's acceptance, nothing broader):

| Atom | Credited as `COMPLETE` for | Explicitly not claimed |
|---|---|---|
| `J03` | ADR-0062 durable admission record, deterministic `job_id`, §3 lifecycle, attempt evidence, no automatic retry, effect-safe explicit retry, restart -> `RECOVERY_REQUIRED` (`application/durable_jobs.py`) | handler dispatch, worker process, PostgreSQL store, client submission/status API (DG-K P02/P04a/P04b/P05b) |
| `J09` | ADR-0063 WSS/TLS 1.3/mTLS non-loopback J02, fingerprint -> principal -> scope mapping, `j02.market_data.read`, security evidence | any scope beyond `j02.market_data.read`; a production non-loopback deployment runbook |
| `J10`, `J12`, `J13` | ADR-0065 seams as **in-process application seams** (`application/strategy_consumer.py`, `validation_consumer.py`, `training_consumer.py`), including Amendment 1/2 synchronous bounds | reachability over J02: no wire message or remote scope exists. Owner decision 2026-10-08 assigns this to DG-K P05 (#318) |
| `J14` | ADR-0066 `j14-framed-result-v1` framing, RFC 8785 canonicalization, per-chunk and transfer digests, transfer-local resume, bounded retention (`application/framed_result_transport.py`) | dispatch of J14 frames by the J02 server; any live family |
| `J15` | ADR-0067 Omega remote client outside `src/quant_platform` (`clients/omega`), `j02.market_data.read` only, `wire_incompatible`, `remote_security_failure` | capabilities beyond market data; J14 support |
| `K12` | ADR-0064 §2 sealed `AdmittedInputManifestV1`, `admission_id`, delivery state machine (`application/admitted_input.py`) | physical transfer of admitted bytes to the deck; a deck-side reader |
| `K13` | ADR-0064 §3 full-bundle refusal, idempotent re-submission, distinct identity for new computations (`application/governed_result_import.py`) | one atomic transaction across the Experiment write (PostgreSQL) and the evidence record (SQLite); DG-K P02 must establish it |

`#313` and `#314` were owner-accepted Wave 8 work outside the nine-atom inventory. Both changed performance only and preserve every existing identity (their acceptance required bit-identical identities). They were integrated through an ad-hoc `implement/wave-8.1` branch cut from `main` after PR #322, which `AGENTS.md`'s branching section does not describe; this closeout records it rather than renaming history.

`#316`/PR #317 added the `l2-book-event-v1` schema, `L2BookEvent` identity and pure venue adapters as an operator-requested emergency slice. It is **groundwork only**: `A14` stays `OPEN_BLOCKING`/`MISSING` under DG-C because no accepted ADR resolves its snapshot/increment/gap semantics, and acquisition, publication and live wiring were out of #316's own scope. Governance finding for the next DG-C decision: a versioned L2 schema now exists in code without an accepted ADR; DG-C must either ratify it by ADR or supersede it.

**Golden proof.** No Wave 8 Golden E2E issue was ever opened (Part B deferred it until the inventory was built). Like Wave 7 (#222), this closeout claims **no** Golden proof artifact. The "Proposed vertical" below remains a target, not a result; it cannot be attempted remotely until DG-K P05 wires the seams onto J02.

**Next.** DG-K Process Topology v1 (#318 governance, #319 design gate) is the next scope; its start gate was this closeout.

---

## Part A — Design gate outcomes (G1-G6), CLOSED

Six design-gate issues (#282-#287, parent #281) each produced one ACCEPTED ADR on `implement/omega`, reconciled by governance issue #288 (parent #281) on 2026-10-04:

| Gate | Atom(s) | ADR | Disposition | One-line resolution |
|---|---|---|---|---|
| **G1** | `J09` | ADR-0063 | ACCEPTED | Non-loopback J02 is WSS+mTLS 1.3 or it refuses to start; exact-certificate-fingerprint principal mapping; sole current scope `j02.market_data.read`. |
| **G2** | `J03` | ADR-0062 | ACCEPTED | Durable Application-owned Job: deterministic admission fingerprint, `ADMITTED`..`RECOVERY_REQUIRED` lifecycle, prohibited automatic retry, proven-effect-safe explicit retry only. |
| **G3** | `K12`, `K13` | ADR-0064 | ACCEPTED | The existing ADR-0057 deck is the sole Wave 8 consumer surface (no third "consumer machine"); sealed deterministic admitted-input manifest (`K12`); exact result-import refusal/idempotency rule (`K13`). |
| **G4** | `J10`, `J12`, `J13` frozen; `J11` deferred | ADR-0065 | ACCEPTED (partial) | Strategy/Validation/Training get thin versioned application seams over existing J02. Replay (`J11`) is explicitly deferred: no accepted identity for `HistoricalReplayRuntime`'s `feature_provider` callable exists yet. |
| **G5** | `J14` | ADR-0066 | ACCEPTED | J02 v1 unchanged; additive `j14-framed-result-v1` finite chunked-result framing with digest-verified, transfer-local resume. No live family defined yet. |
| **G6** | `J15` | ADR-0067 | ACCEPTED | J02 is the sole J15 remote boundary; public Python API is never a remote fallback; `wire_incompatible` fails closed on any mismatch. Implementation explicitly blocked until `J09` is implemented. |

**Resolution of the one open maintainer question this scope carried (G3/"consumer machine" vs. ADR-0057's "deck"):** ADR-0064 §1 answers it directly — the deck is the sole Wave 8 consumer surface; no third physical surface exists or is introduced. This is no longer an open question.

This exits Part A's own exit criteria (see the original wording preserved in git history): every gate is `ACCEPTED` or explicitly `OPEN_DEFERABLE` with a recorded trigger (`J11`), and this reconciliation (issue #288) is that governance-designated pass.

---

## Part B — Authorized Wave 8 implementation inventory

Each atom below had an `ACCEPTED` governing ADR and received its own implementation issue. **All nine are now implemented and on `main`** within the bounds recorded in the Closeout result above. `J11` was excluded — it has no accepted contract (see Part A, G4).

| Atom | Capability | Governing ADR | Sequencing note |
|---|---|---|---|
| `J03` | Durable job runtime | ADR-0062 | Independent; `C03`/`I02` dependencies already complete. |
| `J09` | Authenticated/TLS transport | ADR-0063 | Independent. Several other atoms' *networked* use depends on this landing first (see below), but its own implementation has no inventory-internal blocker. |
| `K12` | Admitted-input manifest | ADR-0064 | Local sealing/manifest logic has no hard blocker; *networked* delivery to the deck requires `J09`. |
| `K13` | Governed result import/registration | ADR-0064 §3 | Depends on `K12` (resolves a `K12` admission before registering a result); already expressed as a `Requires` edge in `CAPABILITY_DAG.md`. |
| `J10` | Strategy Consumer-API seam | ADR-0065 §2 | Independent application-layer seam; does not require `J09` to build, only to expose non-loopback. |
| `J12` | Validation Consumer-API seam | ADR-0065 §3 | Same as `J10`. |
| `J13` | Training Consumer-API seam | ADR-0065 §4 | Same as `J10`. |
| `J14` | Transport framing (chunked large results) | ADR-0066 | Independent; builds directly on the existing J02 transport. |
| `J15` | Omega remote client adapter | ADR-0067 | **Hard sequencing dependency, stated by the ADR itself**: "a remote implementation is blocked until J09 provides ADR-0063's required WSS/mTLS server surface" (ADR-0067, Consequences). An implementation issue for `J15` must carry a start gate holding until `J09`'s implementation issue is merged. |

`J04`/`J05`/`J06` are **extended**, not replaced, once `J10`/`J12`/`J13` exist — no new client atom is minted for that extension. `J07`/`J08` (Wave 10), `I06`/`I07` (Wave 9) and L1/L2/L3 remain untouched and out of scope here.

### Minimum justified implementation issues

Nine implementation issues were opened by reconciliation #288, one per authorized atom above (`J03`, `J09`, `K12`, `K13`, `J10`, `J12`, `J13`, `J14`, `J15`). No Golden E2E issue was created at that time — that proof was to be defined only once the implementation inventory was actually built — and none was created before the operator closeout (#327). Each issue cited its governing ADR and this document, and targeted `implement/wave-8`.

---

## Wave 8 implementation branch — baseline resolved, branch CLOSED

The prior reconciliation (issue #288/PR #295) found `implement/omega` 61 commits ahead of `main` and flagged the resulting baseline ambiguity as an open maintainer decision between two options: (1) promote `implement/omega` into `main` first, or (2) branch `implement/wave-8` directly from `implement/omega`.

Option 2 was attempted first and found **mechanically blocked**: the repository's own pre-push governance-boundary check (`tools/check_pre_push.py`) compares any branch that is not `governance/`/`admin/`/`issue-N`-named against `main`; a `implement/wave-8` rooted in `implement/omega` would always show the governance files as "changed" relative to `main` and fail to push.

The maintainer selected **Option 1**. Executed as follows:

```text
tag omega-stabilization-dgj-v1  @ f79f1313 (implement/omega tip at DG-J closeout)
PR #305 "[closeout] Promote Omega stabilization line and DG-J gates into main"
  implement/omega -> main, merge commit 30c48094
main HEAD (post-promotion)      = 30c48094
implement/wave-8                = branched from main @ 30c48094, pushed clean
```

This mirrors the Wave 7 promotion pattern exactly (tag `wave-7-api-platform-transport-v1` @ `73d5f7b2` -> merge commit `5c8d9af`), applied to a non-wave stabilization line for the first time. `implement/wave-8` now exists and carries the full governance state (including this document) that its own implementation issues depend on.

Implementation issues #296-#304 were then executed in their declared sequence (`J15` after `J09`, `K13` after `K12`) and promoted to `main` by PR #322. `implement/wave-8` is closed.

---

## Context: why this scope exists (historical)

The post-Wave-7 "platform v1.0" sequencing recorded in `ROADMAP.md` set Wave 8 as closing the Consumer-API gap for Strategy, Replay, Validation and Training (the same way Wave 7 closed it for market data). While integrating the first real external client (`NeoNix-Lab/omega`) against the existing J02/J04/J05/J06 surface, four additional, concrete gaps surfaced that the original Wave 8 framing did not anticipate — J02's missing auth/TLS, the absent durable job runtime, the absent admitted-input/result-handoff path, and J04's in-process (not J02) CLI path. Part A's six design gates closed all four; Part B above is the resulting authorized inventory.

### Target topology (now frozen by ADR-0064, not an open question)

```text
                    first real client: Omega
                       |            \
        (all requests, jobs, inputs, results via the API service)
                       |             \
     +-----------------v-----+   +----v------------------------+
     | SERVER  (data-local)  |   | DECK (ADR-0057)             |
     | canonical data+catalog|   | replay sweeps, training,    |
     | materializations      |   | notebooks, Omega runtimes   |
     | server-side jobs      |   | over admitted server inputs |
     +-----------------------+   +-----------------------------+
```

ADR-0064 §1 confirms: the deck is the sole Wave 8 consumer compute surface; no separate third "consumer machine" exists. This was the one open question Part A's original text flagged; it is now closed.

---

## Authority

- `AGENTS.md`
- governance issues #280, #281, #288
- `docs/product/ROADMAP.md` ("Post-Wave-7 sequencing: platform v1.0"; Decision-gate model; Execution waves)
- `docs/product/CAPABILITY_DAG.md` (`DG-J` section; atoms `J03`, `J09`-`J15`, `K12`, `K13`)
- `docs/architecture/OPEN_DECISIONS.md` (`DG-J` — Remote Service Topology, full resolved G1-G6 text)
- ADR-0050 (and Amendment 1), ADR-0057, ADR-0055, ADR-0062 through ADR-0067
- `docs/contracts/PUBLIC_PYTHON_API.md`

Accepted ADRs and frozen contracts remain normative. No implementation issue against the inventory above may renegotiate `C02`/`C03`'s frozen Consumer API semantics, ADR-0050's transport/serialization decisions, or ADR-0057's server/deck authority split.

---

## Baseline and Credited State

```text
implement/omega @ f79f1313 (closed; design-gate work only, 2026-10-04)
Wave 7: J02/J04/J05/J06 COMPLETE (ADR-0050; PR #225/#226/#227/#228)
Omega stabilization line: COMPLETE for audited findings (ADR-0054-0061;
  PR #254, #256-#278; ADR-0037 Amendment 1; ADR-0050 Amendment 1)
DG-J design gates: RESOLVED (ADR-0062-0067; PR #289-#294; J11 deferred)
```

Credit, do not reimplement or re-prove absent invalidating evidence: `C01`-`C05`, `J01`-`J02`, `J04`-`J06`, `D05` semantics (ADR-0059; implementation still `MISSING`), `G01`-`G04`, `H01`-`H05`, `I01`-`I05`, `F01`-`F08`.

---

## Proposed vertical for a future Golden proof (not attempted; not claimed)

From Omega, through the API only:

1. submit a data-local server job and read its result by identity (exercises `J03`/`J09`);
2. obtain an admitted input, run a consumer-local replay or training run, and return the result bundle through the governed import path with identity and digests verified (exercises `K12`/`K13`).

Real transport, not in-process test doubles (ADR-0056 applies). This vertical is a *target*: it needs the implementation inventory above actually built, plus `J15`'s acceptance proof (ADR-0067 §5), before it can be attempted.

---

## Explicit non-claims

- `J03`, `J09`, `J10`, `J12`-`J15`, `K12`, `K13` are `FROZEN`/`COMPLETE` only within the bounds in the Closeout result table; nothing in its "Explicitly not claimed" column is complete.
- `J10`/`J12`/`J13` are not reachable over J02. `ROADMAP.md`'s original Wave 8 intent ("carried over the existing J02 transport") is now owned by DG-K P05 (#318).
- `J11` remains `OPEN_DEFERABLE`/`MISSING` — neither decided nor built.
- No Wave 8 Golden E2E proof artifact exists or is claimed.
- `J07`/`J08` (paper/live, Wave 10), `I06`/`I07` (RL, Wave 9), `A13`-`A15` (L1/L2/L3; `A14` groundwork only, see above), second venue and multi-asset execution are untouched by this scope's completion.
- Closing this scope authorizes no new implementation by itself; DG-K needs its own governance issue (#318) first.

---

## Mutation policy (historical, as applied during Wave 8)

- Each implementation issue produces code/tests for exactly one atom from Part B's table; it does not implement a second atom "while in there."
- Branch `agent/issue-<N>-<slug>` (or `codex/issue-<N>-<slug>`) from `implement/wave-8`. Do not branch from or target `implement/omega` (closed) or `main` directly.
- Do not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` as part of ordinary implementation work (`AGENTS.md` Governance boundary); only a governance-designated issue may, at the next reconciliation.
- `J15`'s implementation issue carries an explicit start gate: HOLD until `J09`'s implementation issue is merged.
- `K13`'s implementation issue carries an explicit start gate: HOLD until `K12`'s implementation issue is merged.

---

## Exit criteria for this scope

Part A's exit criteria are **met**: G1-G6 are each `ACCEPTED` or explicitly `OPEN_DEFERABLE` with a recorded trigger (`J11`), reconciled by #288.

Part B's exit criteria are **met** by operator closeout (#327): each of the nine implementation issues is implemented, merged and promoted to `main` (PR #322, #326), within the bounds recorded above. The Golden E2E criterion is not met and is not claimed; it was closed by explicit maintainer direction, as for Wave 7.

---

## Out of Scope

Historical record of what Wave 8 excluded (still not claimed by this closeout):

- `J07`/`J08` (paper/shadow, live product mode — now Wave 10) and any live/broker execution;
- `I06`/`I07` (Strategic/Execution RL — now Wave 9);
- a second venue, generic provider resolution, or L1/L2/L3 market depth (`A13`-`A15`);
- a generic distributed scheduler, remote-execution framework, or message broker beyond what a specific atom's own ADR strictly requires;
- wholesale canonical-storage migration to the deck;
- repository mirroring outside Git history;
- redesigning `C02`/`C03`'s frozen Consumer API semantics, or ADR-0050's existing transport/serialization decisions;
- implementing `J11` (Replay seam) — it has no accepted contract; its own future design-gate issue must land first.

---

## Stop / Escalation Conditions (historical, as applied during Wave 8)

Stop and report rather than implement or decide unilaterally if:

- an implementation issue cannot satisfy its cited ADR without weakening an already-accepted ADR (ADR-0050, ADR-0055, ADR-0057, ADR-0062 through ADR-0067, or any other);
- an implementation issue is proposed for `J11` before its own design-gate ADR is accepted;
- `J15` implementation work starts before `J09`'s implementation issue is merged, or `K13` before `K12`'s;
- a later issue would require implementing code to answer a design question (that is a design-gate issue's job, never concurrent with implementation).

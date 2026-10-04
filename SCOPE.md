# Scope: Wave 8 - Full Platform API & Remote Service Topology v1

Status: **Design gates CLOSED (G1-G6 each ACCEPTED, or explicitly `OPEN_DEFERABLE` with a recorded evidence trigger). The Wave 8 implementation inventory is AUTHORIZED below. Opening the dedicated `implement/wave-8` branch is NOT yet done — see "Wave 8 implementation branch — baseline pending" below.**

Scope kind: this document now has two parts. Part A (closed) was the design-gate sequencing scope; it is preserved below for the audit trail. Part B (new) is the authorized implementation inventory and the one open branch-baseline question that gates opening `implement/wave-8`.

`implement/omega` is **CLOSED** as the bounded `DG-J` design-integration line (head `f79f1313` at closeout). No further design-gate or implementation issue targets it.

---

## Historical scope archive

This file previously recorded **Wave 7 — API & Platform Transport v1** (CLOSED — exceptional operator closeout; `implement/wave-7` governance-closeout tip `73d5f7b2`, tag `wave-7-api-platform-transport-v1`, merged to `main` by `5c8d9af`), and then **this same Wave 8 scope while its design gates (G1-G6) were still open** (governance issue #280/PR #279). That content is fully preserved in git history (`git log -- SCOPE.md`) and is not duplicated here. `SCOPE.md` is overwritten in place for each governance reconciliation; no wave's scope has ever been copied aside to a separate archived file, and this one is not an exception.

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

Each atom below has an `ACCEPTED` governing ADR and is therefore eligible for its own implementation issue. **None is implemented yet.** `J11` is excluded — it has no accepted contract (see Part A, G4).

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

Nine implementation issues are opened by this reconciliation, one per authorized atom above (`J03`, `J09`, `K12`, `K13`, `J10`, `J12`, `J13`, `J14`, `J15`). No Golden E2E or Wave 8 closeout issue is created yet — per this governance issue's own acceptance criterion, that proof is defined only once the implementation inventory above is actually built, not speculatively now. Each issue cites its governing ADR and this document, and targets `implement/wave-8` once that branch exists (see below); none may branch from or target `implement/omega`, which is closed.

---

## Wave 8 implementation branch — baseline pending (open maintainer decision)

`AGENTS.md`'s Macro Wave convention branches `implement/<wave>` from `main`. Checking this mechanically at reconciliation time:

```text
main HEAD                 = 60065771 (merge PR #230, 2026-10-01) -- unchanged since before Wave 7 closeout
implement/omega HEAD      = f79f1313 (merge PR #294, 2026-10-04)
```

`implement/omega` is 61 commits ahead of `main` and 0 behind. Those 61 commits include the entire Omega stabilization line (ADR-0054-0061, PR #254/#256-#278), the Wave 7-closeout governance reconciliation (PR #279), and all six `DG-J` gates (ADR-0062-0067, PR #289-#294) — **including the very ADRs that authorize this scope's implementation inventory above.** `AGENTS.md` has no defined promotion mechanism for `implement/omega`: "Wave Promotion: Only the final wave closeout PR promotes `implement/<wave>` into `main`" applies to macro wave branches, and `implement/omega` is explicitly not a wave (it is a stabilization/design-gate line).

Branching `implement/wave-8` from `main`'s current head today would produce an implementation branch that is **missing the ADRs it is supposed to implement**. This is exactly the stop condition this governance issue names: "if `main`/`implement/omega` state has diverged such that the intended baseline is ambiguous." It has.

This reconciliation does **not** resolve this by itself. Two concrete options exist for the maintainer to choose between (neither is defaulted here):

1. **Promote `implement/omega` into `main` first**, via some closeout mechanism analogous to Wave 7's tag + merge (`wave-7-api-platform-transport-v1` @ `73d5f7b2` -> `5c8d9af`), then branch `implement/wave-8` from the resulting new `main` head. This keeps `AGENTS.md`'s "branched from `main`" convention literally true, but requires defining a promotion mechanism for a non-wave stabilization line that does not currently exist.
2. **Branch `implement/wave-8` directly from `implement/omega`'s current head** (`f79f1313`), treating it as the de facto new baseline since it is the actual current state of `main`'s intended lineage. This needs no new promotion mechanism, but means `implement/wave-8` is not literally "branched from `main`" at the moment it opens.

Until the maintainer picks one, **`implement/wave-8` is not created by this reconciliation.** The nine implementation issues above exist and are fully specified; their branch target is left as `implement/wave-8` (TBD) and none should be started before that branch exists.

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

## Proposed vertical for this scope's eventual Golden proof (not authorized yet)

From Omega, through the API only:

1. submit a data-local server job and read its result by identity (exercises `J03`/`J09`);
2. obtain an admitted input, run a consumer-local replay or training run, and return the result bundle through the governed import path with identity and digests verified (exercises `K12`/`K13`).

Real transport, not in-process test doubles (ADR-0056 applies). This vertical is a *target*: it needs the implementation inventory above actually built, plus `J15`'s acceptance proof (ADR-0067 §5), before it can be attempted.

---

## Explicit non-claims

- Nothing in this document marks any atom `COMPLETE`. `J03`, `J09`, `J10`, `J12`-`J15`, `K12`, `K13` are `FROZEN`/`MISSING` — decided, not built. `J11` is `OPEN_DEFERABLE`/`MISSING` — neither decided nor built.
- `J07`/`J08` (paper/live, Wave 10), `I06`/`I07` (RL, Wave 9), `A13`/`A14` (L1/L2), second venue and multi-asset execution are untouched by this scope.
- This document does not create `implement/wave-8`. It authorizes the implementation inventory and leaves the branch-baseline decision to the maintainer (see above).
- This scope's existence is not concurrent authorization for any implementation issue to merge code — each issue proves its own atom against its cited ADR.

---

## Mutation policy

- Each implementation issue produces code/tests for exactly one atom from Part B's table; it does not implement a second atom "while in there."
- Branch `agent/issue-<N>-<slug>` (or `codex/issue-<N>-<slug>`) from `implement/wave-8` once that branch exists. Do not branch from or target `implement/omega` (closed) or `main` directly.
- Do not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` as part of ordinary implementation work (`AGENTS.md` Governance boundary); only a governance-designated issue may, at the next reconciliation.
- `J15`'s implementation issue carries an explicit start gate: HOLD until `J09`'s implementation issue is merged.
- `K13`'s implementation issue carries an explicit start gate: HOLD until `K12`'s implementation issue is merged.

---

## Exit criteria for this scope

Part A's exit criteria are **met**: G1-G6 are each `ACCEPTED` or explicitly `OPEN_DEFERABLE` with a recorded trigger (`J11`), and this governance-designated issue (#288) reconciled `CAPABILITY_DAG.md`/`CAPABILITY_MAP.md`/`ROADMAP.md`/`OPEN_DECISIONS.md` to the gates' actual resolutions.

Part B remains open until: the maintainer resolves the branch-baseline question above, `implement/wave-8` opens, and each of the nine implementation issues is implemented and proven against its ADR. Golden E2E and Wave 8 closeout are defined only after that inventory is actually built.

---

## Out of Scope

Do not pull into any Wave 8 implementation issue:

- `J07`/`J08` (paper/shadow, live product mode — now Wave 10) and any live/broker execution;
- `I06`/`I07` (Strategic/Execution RL — now Wave 9);
- a second venue, generic provider resolution, or L1/L2/L3 market depth (`A13`-`A15`);
- a generic distributed scheduler, remote-execution framework, or message broker beyond what a specific atom's own ADR strictly requires;
- wholesale canonical-storage migration to the deck;
- repository mirroring outside Git history;
- redesigning `C02`/`C03`'s frozen Consumer API semantics, or ADR-0050's existing transport/serialization decisions;
- implementing `J11` (Replay seam) — it has no accepted contract; its own future design-gate issue must land first.

---

## Stop / Escalation Conditions

Stop and report rather than implement or decide unilaterally if:

- an implementation issue cannot satisfy its cited ADR without weakening an already-accepted ADR (ADR-0050, ADR-0055, ADR-0057, ADR-0062 through ADR-0067, or any other);
- an implementation issue is proposed for `J11` before its own design-gate ADR is accepted;
- `J15` implementation work starts before `J09`'s implementation issue is merged, or `K13` before `K12`'s;
- **the `main`/`implement/omega` baseline question above remains unresolved and someone attempts to open `implement/wave-8` by guessing one of the two options instead of the maintainer deciding** — this is the one standing escalation this reconciliation itself could not close;
- a later issue would require implementing code to answer a design question (that is a design-gate issue's job, never concurrent with implementation).

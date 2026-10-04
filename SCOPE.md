# Scope: Wave 8 - Full Platform API & Remote Service Topology v1

Status: **OPEN — design gates first. No implementation is authorized by this document.**

Scope kind: **design-gate sequencing scope**, not a bounded implementation scope. Each gate below (`G1`-`G6`) requires its own ADR before any implementation issue against the atom(s) it blocks may open.

Target integration branch for design-gate issues: **`implement/omega`** (the current active stabilization/integration line, head `01c6a75` at scope-opening), until an actual implementation atom's slice is ready, at which point a dedicated `implement/wave-8` branch opens from `main` per `AGENTS.md`'s standard Macro Wave convention. This document does not itself authorize opening that branch.

---

## Historical scope archive

This file previously recorded **Wave 7 — API & Platform Transport v1** (CLOSED — exceptional operator closeout; `implement/wave-7` governance-closeout tip `73d5f7b2`, tag `wave-7-api-platform-transport-v1`, merged to `main` by `5c8d9af`). That content is fully preserved in git history (`git log -- SCOPE.md`) and is not duplicated here. This is the existing versioned-scope convention: `SCOPE.md` is overwritten in place for each new wave; no wave's scope has ever been copied aside to a separate archived file, and this one is not an exception.

---

## Prerequisite Integration Gate

This scope opens after:

```text
Wave 7 promotion: main @ 5c8d9af (merge commit), tag wave-7-api-platform-transport-v1 @ 73d5f7b2
Omega stabilization line (tracking #232) reconciled into governance state
  by the governance issue that authored this document
  (25 PRs merged on implement/omega: #254, #256-#278)
```

No implementation atom in this scope requires a `main` promotion first; design-gate issues work against `implement/omega` directly.

---

## Context: why this scope exists now

The post-Wave-7 "platform v1.0" sequencing already recorded in `ROADMAP.md` set Wave 8 as closing the Consumer-API gap for Strategy, Replay, Validation and Training (the same way Wave 7 closed it for market data). While integrating the first real external client (`NeoNix-Lab/omega`) against the existing J02/J04/J05/J06 surface, four additional, concrete gaps surfaced that the original Wave 8 framing did not anticipate:

- J02 is loopback-only by default and **unauthenticated, with no TLS**, once an operator explicitly opts into non-loopback binding (ADR-0050 Amendment 1 §3). ADR-0057 §4 forbids using that override as a server-to-consumer-machine handoff channel.
- There is no durable job runtime (`J03`, `OPEN_BLOCKING`/`MISSING`); its dependencies (`C03`, `I02`) are already complete, so server-side long-running work reachable through the API depends on deciding it.
- There is no server-owned admitted-input export/reference or governed result-import path (ADR-0057 §2, §6); an operator today can only perform a manual, controlled, read-only transfer.
- `J04` (CLI) uses the in-process `C03` path, not `J02` (ADR-0050 §7); it does not exercise the remote-service path at all, so the "first real client" story is not yet proven end-to-end over the network for any domain.

This scope therefore does two things: it keeps the original Wave 8 Consumer-API-seam objective, and it adds the remote-topology/job pieces the Omega integration proved are actually load-bearing prerequisites, not speculative scope creep.

### Target topology (to be frozen gate-by-gate, not by this document)

```text
                    first real client: Omega
                       |            \
        (all requests, jobs, inputs, results via the API service)
                       |             \
     +-----------------v-----+   +----v------------------------+
     | SERVER  (data-local)  |   | CONSUMER MACHINE (local)    |
     | canonical data+catalog|   | replay sweeps, training,    |
     | materializations      |   | notebooks, Omega runtimes   |
     | server-side jobs      |   | over admitted server inputs |
     +-----------------------+   +-----------------------------+
```

Basis: ADR-0057 (the server is the single authority for canonical data and accepted artifacts; the consumer machine is a compute consumer and result producer, never a second authority) and ADR-0055 (Omega consumes only the public surface).

**Open question (see Stop/Escalation Conditions and the governance issue's own maintainer-decision list): whether "consumer machine" above is exactly ADR-0057's "deck" (the GPU-equipped surface), or a distinct third physical surface.** This scope's G3 gate must resolve that before freezing the placement contract; it is not assumed here.

---

## Authority

Read before opening any design-gate issue against this scope:

- `AGENTS.md`
- `docs/product/ROADMAP.md` ("Post-Wave-7 sequencing: platform v1.0"; Decision-gate model; Execution waves)
- `docs/product/CAPABILITY_DAG.md` (`DG-J` section; atoms `J09`-`J15`, `K12`, `K13`; reactivated `J03`)
- `docs/architecture/OPEN_DECISIONS.md` (`DG-J` — Remote Service Topology, full G1-G6 text)
- `docs/decisions/ADR-0050-api-transport-and-client-boundary-v1.md` and its Amendment 1
- `docs/decisions/ADR-0057-server-deck-runtime-topology-v1.md`
- `docs/decisions/ADR-0055-omega-validation-bridge-v1.md`
- `docs/contracts/PUBLIC_PYTHON_API.md`

Accepted ADRs and frozen contracts remain normative. No gate in this scope may renegotiate `C02`/`C03`'s already-frozen Consumer API semantics, ADR-0050's existing transport/serialization decisions, or ADR-0057's server/deck authority split; each gate *extends* accepted authority into a currently-undecided corner, it does not reopen it.

---

## Baseline and Credited State

```text
implement/omega @ 01c6a75 (head at scope-opening)
Wave 7: J02/J04/J05/J06 COMPLETE (ADR-0050; PR #225/#226/#227/#228)
Omega stabilization line: COMPLETE for audited findings (ADR-0054-0061;
  PR #254, #256-#278; ADR-0037 Amendment 1; ADR-0050 Amendment 1)
```

Credit, do not reimplement or re-prove absent invalidating evidence: `C01`-`C05`, `J01`-`J02`, `J04`-`J06`, `D05` semantics (ADR-0059; implementation still `MISSING`), `G01`-`G04`, `H01`-`H05`, `I01`-`I05`, `F01`-`F08`.

---

## Gap analysis (the facts this scope's gates must resolve)

| Fact | Evidence |
|---|---|
| J02 is unauthenticated/no-TLS once non-loopback is opted into | ADR-0050 Amendment 1 §3 |
| ADR-0057 forbids that override as a deck/consumer-machine handoff | ADR-0057 §4 |
| J02 is one request -> one JSON message, 50,000-row/16 MiB bound (`RESULT_TOO_LARGE`); no streaming/pagination/job semantics | ADR-0050 Amendment 1 |
| `J03` job runtime is `OPEN_BLOCKING`/`MISSING`; `C03`/`I02` dependencies complete | `CAPABILITY_DAG.md` J03 row |
| Strategy, Replay, Validation, Training have no Consumer-API-equivalent seam | `ROADMAP.md` Wave 8 (pre-existing framing) |
| No server-owned export/reference or governed result-import path exists | ADR-0057 §2, §6 |
| `J04` (CLI) uses in-process `C03`, not J02 -- does not exercise the service path | ADR-0050 §7 |
| Omega is a Python-distribution consumer (`PLATFORM_PIN`, `PUBLIC_PYTHON_API.md`), not an API client | ADR-0055; `PUBLIC_PYTHON_API.md` |

---

## In-Scope: Design Gates G1-G6

Full question/anchor text lives in `docs/architecture/OPEN_DECISIONS.md`'s `DG-J` section; this table is the scope-level index only.

| Gate | Resolves | Blocks (new atoms) | Anchors |
|---|---|---|---|
| **G1** | Authentication, TLS, authorization, least-privilege credentials for non-loopback J02 | `J09` | ADR-0050 Am.1 §3; ADR-0057 §4 |
| **G2** | `J03` durable job runtime: identity, lifecycle, retry/idempotency, result identity, failure semantics | reactivates `J03` | `OPEN_DECISIONS.md` DG-G `J03` (full 120-question decision scope already drafted there) |
| **G3** | Placement contract: data-local vs. consumer-local; admitted-input export/reference and governed result import | `K12`, `K13` | ADR-0057 §1-§3, §6 |
| **G4** | Consumer-API-equivalent seams for Strategy, Replay, Validation, Training | `J10`, `J11`, `J12`, `J13` | `ROADMAP.md` Wave 8; the existing `C02`/`C03` pattern |
| **G5** | Transport evolution: chunked/streamed large results, cursors, future live message types | `J14` | ADR-0050 Am.1 §1-§2; ADR-0047 |
| **G6** | Omega client contract: wire vs. public-Python API, version/compat policy, acceptance proof | `J15` | ADR-0055; `PUBLIC_PYTHON_API.md` |

**Recommended gate order: G2 then G1, before G3-G6** (both are prerequisites G3 through G6 depend on; this is a recommendation recorded here for the next design-gate issue to confirm or override, not a mandate this document enforces by itself).

Each gate is **one ADR, produced by one design-gate issue, one at a time** — this scope does not authorize opening two gates' ADRs concurrently. A gate issue may answer "not yet, defer until X evidence exists" for its own questions; that is a valid gate closure, matching this repository's `OPEN_DEFERABLE` convention, and does not require inventing an answer prematurely.

---

## Candidate implementation atoms (classified now; none implemented, none authorized)

Already added to `CAPABILITY_DAG.md` at `OPEN_BLOCKING`/`MISSING`, per gate above: `J09`-`J15`, `K12`, `K13`, plus the reactivated existing `J03`. Their `Requires`/`Unlocks` columns in `CAPABILITY_DAG.md` are the authoritative dependency record; this scope does not duplicate it.

J04/J05/J06 are **extended**, not replaced, once G4's seams exist — no new client atom is minted for that extension.

---

## Proposed vertical for this scope's eventual Golden proof (not authorized yet)

From Omega, through the API only:

1. submit a data-local server job and read its result by identity (exercises `G2`/`J03`, `G1`/`J09`);
2. obtain an admitted input, run a consumer-local replay or training run, and return the result bundle through the governed import path with identity and digests verified (exercises `G3`/`K12`/`K13`).

Real transport, not in-process test doubles (ADR-0056 applies: Golden proof orchestration lives in `application`, test doubles are classified and contained per that ADR's inventory). This vertical is a *target*, not a commitment this scope can satisfy without first closing G1-G3.

---

## Explicit non-claims

- Nothing in this document marks any new atom `COMPLETE` or `FROZEN`. `J09`-`J15`, `K12`, `K13` and the reactivated `J03` stay `OPEN_BLOCKING`/`MISSING` until their own gate's ADR is accepted and a dedicated implementation issue proves the atom.
- `J07`/`J08` (paper/live, now Wave 10), `I06`/`I07` (RL, now Wave 9), `A13`/`A14` (L1/L2), second venue and multi-asset execution are untouched by this scope.
- Authentication, TLS, the job runtime, and the server-consumer handoff are described here as **missing**, never as planned-as-done.
- This scope's existence is not concurrent authorization for any gate or slice; each needs its own issue.

---

## Mutation policy

One bounded gate at a time. A design-gate issue:

- produces exactly one ADR (or amends an existing one per `docs/decisions/README.md`'s amendment policy, if the gate narrows rather than extends);
- does not implement code;
- does not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` — only a governance-designated issue may, at the next reconciliation pass after a gate's ADR lands;
- branches `agent/issue-<N>-dg-j-g<K>-design` (or `governance/...` if the issue is itself designated governance-reconciliation work) from `implement/omega`, PR into `implement/omega`.

An implementation issue may only open once its specific gate's ADR is `ACCEPTED`. It must cite the ADR and may not implement a second gate's atom "while in there."

---

## Exit criteria for this scope

This scope (and the `DG-J` gate family) is exited — meaning Wave 8 implementation may begin in earnest under a dedicated `implement/wave-8` branch — only when:

- G1-G6 are each `ACCEPTED` (or explicitly `OPEN_DEFERABLE` with a recorded evidence trigger, for any gate where that is the honest answer);
- a governance-designated issue reconciles `CAPABILITY_DAG.md`/`CAPABILITY_MAP.md`/`ROADMAP.md`/`OPEN_DECISIONS.md` to the gates' actual resolutions;
- no atom this scope classified is misrepresented as implemented.

Reaching this exit is itself a future governance act, not something this scope pre-authorizes.

---

## Out of Scope

Do not pull into any `DG-J` gate issue or this scope's eventual implementation slices:

- `J07`/`J08` (paper/shadow, live product mode — now Wave 10) and any live/broker execution;
- `I06`/`I07` (Strategic/Execution RL — now Wave 9);
- a second venue, generic provider resolution, or L1/L2/L3 market depth (`A13`-`A15`);
- a generic distributed scheduler, remote-execution framework, or message broker beyond what a specific gate's own ADR strictly requires (ADR-0057's own exclusion);
- wholesale canonical-storage migration to any consumer machine;
- repository mirroring outside Git history;
- redesigning `C02`/`C03`'s already-frozen Consumer API semantics, or ADR-0050's existing transport/serialization decisions.

---

## Stop / Escalation Conditions

Stop and report rather than implement or decide unilaterally if:

- a gate's design cannot resolve without weakening an already-accepted ADR (ADR-0050, ADR-0055, ADR-0057, or any other);
- G3's placement contract cannot proceed without first resolving whether "consumer machine" is ADR-0057's "deck" or a distinct third physical surface — **this is an open maintainer decision, not something a design-gate issue should assume**;
- a gate would require implementing code to answer its own question (that is an implementation issue's job, strictly after the gate's ADR is accepted, never concurrent with it);
- an implementation issue is proposed before its gate's ADR is `ACCEPTED`.

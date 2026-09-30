# Scope: Wave 7 - API & Platform Transport v1

Status: **CLOSED — exceptional operator closeout**

Scope kind: **decision-gate resolution and bounded implementation scope for Wave 7**.

Target integration branch: **`implement/wave-7`**, branched from `main` after Wave 6 promotion.

---

## Prerequisite Integration Gate

Wave 7 may start only after the Wave 6 Live Consumer Data Plane & Storage Lifecycle path is promoted to `main`.

Current gate evidence:

```text
Wave 6 promotion PR: #214
main promotion commit: 093f4f6fc267cd294cac31527f4557e55f30cf51
Wave 6 tag: wave-6-live-consumer-data-plane-storage-lifecycle-v1
Issue #200: Wave 6 Golden E2E proof credited
Issue #201: Wave 6 governance closeout closed after main promotion
```

Closeout evidence:

```text
Wave 7 integration branch: implement/wave-7
Wave 7 head at closeout: b5a6aea66cf9b657a17b61abdef530d97050782d
DG-I / J02 ADR: ADR-0050
J02 implementation: PR #225
J04 implementation: PR #226
J05 implementation: PR #227
J06 implementation: PR #228
Issue #222: closed by explicit operator exception during expedited Wave 7 closeout
Issue #223: governance reconciliation scope
```

The Wave 7 Golden E2E proof item (#222) is closed by explicit operator direction
without adding a dedicated `docs/integration/` Golden proof artifact. This
closeout credits the merged J02/J04/J05/J06 implementation and focused PR
evidence, but it does **not** claim an additional full Golden client-to-service
round-trip proof beyond what is already recorded in the slice PRs.

**Historical note on the `J02` decision gate.** At scope opening, `docs/architecture/OPEN_DECISIONS.md` and `ROADMAP.md` both recorded `J02` as `OPEN_DEFERABLE`, gated on "a real remote/client need," and explicitly stated that roadmap/frontier completion is **not** concurrent authorization to open it. This scope was opened on an explicit operator decision to proceed; it did not itself assert or fabricate a specific external client story. Design gate step 1 below (`DG-I`) is where the concrete transport/serialization/runtime-host choice — and any concrete driving client scenario the operator wanted to name — was pinned.

---

## Objective

Resolve the still-open `J02` API-transport decision and implement the thin canonical clients that depend on it:

```text
Application Service (C01-C05, ASS-01/ASS-02/ASS-03 -- already COMPLETE)
                    |
                    v
        J02 canonical API transport runtime
           (REST / WebSocket / gRPC / other -- design-gate choice)
                    |
        +-----------+-----------+
        |           |           |
        v           v           v
   J04 CLI      J05 TUI      J06 App UI
  (or direct   (operator    (interactive
   bounded      dashboard)   visualization)
   C03 path)
```

At scope opening, `J02` was `OPEN_DEFERABLE` in `CAPABILITY_DAG.md`: no transport protocol, serialization, pagination/streaming shape or runtime host was frozen yet, even though the Consumer API semantics it must carry (`C02`/`C03`) were already `FROZEN`/`COMPLETE`. `J04`, `J05` and `J06` were already `RESOLVED` at the decision level (thin-client semantics are settled: no quantitative or storage logic in any client) but `MISSING` at the implementation level. Wave 7 therefore had to:

1. resolve `J02`'s concrete transport/serialization/runtime-host choice, and the exact package-boundary shape for the transport runtime and for client code, through a design gate that produces an ADR;
2. implement `J02` binding directly to the already-complete Application Service (`C02`/`C03`) without leaking quantitative business logic into the transport layer;
3. implement `J04` (CLI), reusing the DAG's own permitted alternative of an explicitly bounded in-process `C03` path where that is simpler than waiting on `J02`;
4. implement `J05` (TUI) and `J06` (App UI), both of which the DAG requires to go through `J02`;
5. prove the full client round-trip against the real Application Service on bounded canonical evidence, or explicitly close that proof item by operator exception at wave closeout.

Wave 7 delivers atoms **`J02`**, **`J04`**, **`J05`** and **`J06`**. It does not deliver `J03` (job runtime — see Out of Scope), `J07` (paper/shadow trading), `J08` (live product mode), a second venue, or L1/L2/L3 market depth.

---

## Authority

Read before mutating code or scope-derived issue bodies:

- `AGENTS.md`
- `README.md`
- `docs/product/PRODUCT.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/product/ROADMAP.md` (Decision-gate model; execution waves)
- `docs/architecture/TARGET_ARCHITECTURE.md`
- `docs/contracts/CORE_CONTRACTS.md` (Consumer API / ASS-02 sections)
- `docs/architecture/OPEN_DECISIONS.md` (`J02` — Canonical API transport)
- `docs/decisions/ADR-0024-package-boundary-modular-monolith-v1.md`

Accepted ADRs and frozen contracts remain normative semantic authority. `C02`/`C03`'s frozen Consumer API semantics (selector resolution, result/error envelope) are not renegotiated by this scope — `J02` carries them, it does not redesign them.

---

## Baseline and Credited State

Authoritative baseline for this branch:

```text
main @ 093f4f6fc267cd294cac31527f4557e55f30cf51
tag  wave-6-live-consumer-data-plane-storage-lifecycle-v1
```

Credit, do not reimplement or re-prove absent invalidating evidence:

```text
C01 Application ownership                               COMPLETE
C02 ASS-02 semantic selector resolution                  COMPLETE
C03 ASS-02 result/error translation                      COMPLETE
C04 Tool orchestration convergence (ASS-03)               COMPLETE
C05 Configuration convergence                            COMPLETE
```

Important credited implementation details:

- `quant_platform.application.market_data` already owns the full Consumer API surface (`ConsumerMarketDataQuery`, `ConsumerMarketDataResult`, `ConsumerApiError`, the six frozen `ConsumerErrorCode` values, `execute_market_data_query`, `resolve_market_data_request`). `J02` is a transport wrapper around this surface — it must not redefine request/result/error semantics, only carry them over a wire protocol.
- `quant_platform.application.composition` / `.conformity` already demonstrate the `application`-owned composition pattern this scope's transport runtime must reuse rather than duplicate.
- No client-facing package exists yet anywhere in `src/quant_platform`. `J02`/`J04`/`J05`/`J06` are wholly new.

Wave 7 composes the existing, already-complete Application Service. It must not duplicate `C02`/`C03`'s selector-resolution or result/error-translation logic anywhere in the transport or client layers.

---

## In-Scope Capability Inventory

| ID | Capability | Owner | Requires | Unlocks | Decision State | Target Impl State | Acceptance / Authority |
| :--- | :--- | :--- | :--- | :--- | :---: | :---: | :--- |
| **J02** | `Canonical API transport runtime v1` | API Runtime | `C03`, real client need (this scope) | `J04-J06` | RESOLVED | COMPLETE | Remote typed transport binding the Application Service without leaking quantitative/storage logic; exact protocol/serialization/runtime-host fixed by ADR-0050. |
| **J04** | `Canonical CLI client v1` | Client Layer | `J02` OR explicitly bounded in-process `C03` | operator workflow | RESOLVED | COMPLETE | Thin command-line client; no quantitative/storage logic; may bypass `J02` via the DAG's own bounded in-process alternative. |
| **J05** | `Terminal UI (TUI) operational client v1` | Client Layer | `J02` | interactive workflow | RESOLVED | COMPLETE | Thin terminal dashboard over `J02`; same canonical semantics as the CLI. |
| **J06** | `App UI client v1 (Web/Desktop visualization)` | Client Layer | `J02` | product workflow | RESOLVED | COMPLETE | Thin interactive client over `J02`; business logic remains behind the service boundary. |

---

## Decision Gate Resolution: DG-I (new)

`ROADMAP.md`'s Decision-gate model (`DG-A` through `DG-H`) has no existing family for API transport and client convergence. This scope introduces **`DG-I` — API Transport & Client Convergence** to resolve `J02`. Unlike Wave 6 (which resolved sub-branches of the already-named `DG-B`/`DG-H`), this is the first atom under `DG-I`; formal registration of the `DG-I` name in `ROADMAP.md`'s Decision-gate model table happens at this wave's governance closeout, following the same deferred-registration precedent every prior wave used (the gate model table is reconciled at closeout, not at scope-opening).

`J04`/`J05`/`J06` need no design gate of their own: their decision state is already `RESOLVED` (thin-client semantics are settled) and their only blocking dependency is `J02`'s concrete shape (or, for `J04` alone, the bounded in-process alternative).

- **DG-I / J02**: produces the next ADR (`ADR-0050`), resolving:
  - the concrete transport protocol(s) (REST / WebSocket / gRPC / Arrow Flight / other) and serialization format;
  - pagination/streaming shape for bounded and unbounded (live) Consumer API results;
  - the runtime host / process topology (in-process ASGI/WSGI app, standalone server, reused `application.live_ingest_server`-style composition, or other);
  - the package-boundary shape for the transport runtime (new owner, e.g. `quant_platform.transport`, bound only to `application`) and for client code (whether clients live inside `src/quant_platform` at all, given they must not depend on domain packages, or as a separate top-level directory);
  - the exact shape of `J04`'s "explicitly bounded in-process `C03`" alternative, if selected for the CLI, and confirmation it follows the same `tools/*.py`-style application-only import discipline already enforced by `tests/test_package_boundaries_v1.py`'s executable-orchestration seam test.

---

## Package Boundary and Modular Monolith Updates

Wave 7 introduces capability categories this repository has not yet needed:

```text
API Runtime    -- J02, likely a new owner (e.g. "transport") bound only to "application"
Client Layer   -- J04/J05/J06, thin processes that must not import domain packages
                  ("access", "producer", "representation", "operations", "strategy",
                  "execution", "portfolio", "replay", "source", "physical") at all
```

Boundary rules to be fixed exactly by the `DG-I` design gate, constrained as follows:

- the transport runtime may depend on `application` (to reach `C02`/`C03`) and `shared`; it must gain no dependency on any domain-owner package directly, mirroring every other `application`-composed capability in this repository;
- remote clients (`J05`/`J06`, and `J04` if built over `J02`) talk to `J02` only over its wire protocol — they have no legitimate reason to import `quant_platform` at all, and must not;
- `J04`'s bounded in-process alternative, if chosen, must follow `tools/*.py`'s existing constraint: import only from `quant_platform.application`, never reach into domain packages directly;
- no client may contain quantitative business logic, storage access, or strategy/execution/portfolio semantics of any kind — that discipline is enforced by `CAPABILITY_DAG.md`'s own `RESOLVED` disposition for `J04`/`J05`/`J06` and must not be silently narrowed or widened by this scope.

Exact new package/module names are an implementation decision for the `DG-I` ADR to fix, not this document.

---

## Active Path

The repository rule of one bounded mutation slice at a time governs execution.

```text
1. Design gate - DG-I: J02 API Transport Concrete Choice
   - Produce ADR-0050 resolving protocol, serialization, pagination/streaming,
     runtime host, and the transport/client package-boundary shape.
   - Must not renegotiate C02/C03's already-frozen Consumer API semantics.
        |
        v
2. Slice J02 - Canonical API Transport Runtime
   - Implement the transport runtime per ADR-0050, binding directly to C02/C03.
   - No quantitative/storage logic in the transport layer.
        |
        v
3. Slice J04 - Canonical CLI Client
   - Implement per ADR-0050's chosen path (over J02, or the bounded in-process
     C03 alternative). May proceed in parallel with step 2 if the bounded
     in-process alternative is chosen, since it would not depend on J02.
        |
        v
4. Slice J05 - Terminal UI (TUI) Operational Client
   - Implement over J02. Thin dashboard; no business logic.
        |
        v
5. Slice J06 - App UI Client (Web/Desktop Visualization)
   - Implement over J02. Thin interactive client; no business logic.
        |
        v
6. Wave 7 Golden E2E Proof
   - Normally prove a full client round-trip (at least one of J04/J05/J06)
     against the real Application Service on bounded canonical evidence, with
     request/response fidelity identical to a direct in-process C02/C03 call.
   - For this expedited closeout only, #222 is closed by explicit operator
     exception without claiming a new Golden proof artifact.
        |
        v
7. Wave 7 Governance Closeout
   - Register DG-I in ROADMAP.md's Decision-gate model.
   - Reconcile CAPABILITY_DAG.md, CAPABILITY_MAP.md, ROADMAP.md and OPEN_DECISIONS.md.
   - Prepare implement/wave-7 for promotion to main after the Golden proof and reconciliation.
```

Step 3 (`J04`) is drawn out of strict sequence deliberately: per the DAG's own `J02 OR explicitly bounded in-process C03` dependency, it is not required to wait for step 2 if the design gate selects the in-process alternative. Steps 4-5 (`J05`/`J06`) strictly require step 2, with no such alternative.

---

## Acceptance Criteria

Wave 7 is complete only when all of the following are observably true:

1. **Semantic Fidelity**: every transport-carried request/response is observably identical, in canonical content, to the same request executed directly against `C02`/`C03` in-process — `J02` carries the Consumer API, it does not reinterpret it.
2. **No Business Logic Leakage**: the transport runtime and every client contain no quantitative, storage, strategy, execution or portfolio logic; `tests/test_package_boundaries_v1.py` holds this structurally, not by convention alone.
3. **Thin Clients**: `J04`/`J05`/`J06` each contain no business logic and correctly render/submit through their chosen path (`J02` or, for `J04` only, the bounded in-process alternative).
4. **Package Boundary**: `tests/test_package_boundaries_v1.py` passes with every new owner registered and no forbidden dependency; no client package gains a domain-owner dependency.
5. **Verification Gate**: `python tools/workflow.py preflight`, `python tools/check_markdown_links.py` and the relevant focused tests pass.
6. **Golden Proof**: Wave 7 normally records a deterministic client-to-service round-trip proof with stable identities, on bounded canonical evidence. For this exceptional closeout, this criterion is waived by explicit operator direction and no additional Golden proof artifact is claimed.

---

## Out of Scope

Do not pull into Wave 7 unless a later authorized scope explicitly changes it:

- **`J03` (job runtime)**: still `OPEN_BLOCKING` under `DG-G`, and not required by any of `J02`/`J04`/`J05`/`J06`'s own DAG dependencies. Including it here would silently widen this scope into an unrelated, still-unresolved decision family; it is deferred to its own dedicated scope.
- Paper/shadow trading (`J07`) or live product mode (`J08`).
- A second venue or generic provider resolution.
- L1/L2 market depth (`A13`/`A14`) or L3/MBO (`A15`).
- Strategy/Execution/Portfolio/Replay/ML/RL semantics of any kind reachable from a client.
- Live broker connections, exchange order placement or live trading authorization.
- A generic job scheduler, workflow engine, or message broker beyond what `DG-I`'s own transport choice strictly requires.
- Redesigning `C02`/`C03`'s already-frozen Consumer API semantics.

---

## Stop / Escalation Conditions

Stop and report rather than implement if:

- `DG-I`'s design gate cannot resolve a transport/serialization/runtime-host choice without weakening `C02`/`C03`'s accepted Consumer API contract;
- a client requires any capability beyond thin rendering/submission over the chosen transport (or, for `J04`, the bounded in-process alternative);
- `J02` cannot be implemented without a new domain-package dependency from the transport layer;
- the Golden E2E proof requires `J03`, `J07`, `J08`, or any capability this scope's Out of Scope section excludes.

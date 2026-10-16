# Scope: DG-K2 — Server/deck deployment composition v1 (design gate)

Status: **OPEN — design gate only (governance issue #382, 2026-10-16). No implementation is authorized by this scope.** The single design-gate issue is #383 (an ADR for the composition decisions below; its number is the next free one at authoring time). The scope id `DG-K2`, the atom names `S01`-`S08` and the integration branch name are **provisional placeholders** for the maintainer to confirm: `DG-L` (FEI) and `DG-C` (L2) already exist on other lines.

Scope kind: a Part A-style design-gate sequencing scope. It registers the open decisions, fixes the decisions already taken as inputs, and names the candidate atoms the gate must make implementable. A Part B implementation inventory is added only by a later governance reconciliation, after #383's ADR is `ACCEPTED` (or #383 closes with an explicit disposition and evidence trigger).

---

## Historical scope archive

This file previously recorded **DG-K — Process Topology v1** (CLOSED — operator closeout, issue #381, 2026-10-16; design gate #319, ADR-0068 and ADR-0069; `P01`-`P06` implemented on `implement/dgk` within recorded bounds, no Golden proof claimed). That content, including the closeout result and the "Not claimed" list, is preserved in git history (`git log -- SCOPE.md`) and is not duplicated here, except for the facts this scope depends on. `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Why this scope exists

DG-K delivered the building blocks of the server/deck split — the J02 families for `J10`/`J12`/`J13` and data-local jobs, `K12` delivery as `j02-admitted-input-v2`, `K13` import, the deck protocol client in `clients/deck/`, the PostgreSQL `runtime` schema and the single worker host — but **no executable composes them** and nothing proves them on the real topology. Verified against `implement/dgk` @ `93471e4`:

- `tools/api_server.py` serves the J02 v1 market-data message only and has no TLS/mTLS options; `RemoteJ02SecurityConfig` is constructed only in tests, so no family is reachable from outside a test.
- The server-owned seams of `K12` (`resolve`, `members`, `read_member`) have only test authorities; nothing resolves a semantic request against the DataGateway/catalog.
- `GovernedResultImportService` (`K13`) is composed in no host, and there is no registry of output roles and their contracts.
- `clients/deck/` is a protocol client; no component produces a `GovernedResultBundleV1` (deck code identity, feature-provider identity, input digests) for a deck workload.
- ADR-0063 leaves certificate issuance, secret storage and deployment out of scope; none is defined.
- `DATA_LOCAL_JOB_REGISTRY` ships empty, so `j02-job-v1` has nothing to submit.
- The acceptance tests put SQLite or `FakeConnection` stores behind a real WSS/mTLS listener; the PostgreSQL suite does not go through the listener.

This scope exists so that the split can be composed, deployed and proven, without re-deciding anything DG-K decided.

---

## Fixed inputs (already decided; the gate does not reopen them)

1. **ADR-0057:** two surfaces, the server (sole authority for canonical data, `K12`/`K13`, data-local work) and the deck (compute, on its own initiative). The server never delegates or pushes work to the deck; no remote worker pulls from the server queue.
2. **ADR-0068:** five units; domain packages stay in-process libraries; hosts stay in `tools/`; exactly one J03 worker; the API records its deployment commit and the worker refuses a different one; the deck may run another commit, compatibility is the wire family version, never the package version.
3. **ADR-0069 and its amendments:** the families, one least-privilege scope per family, `j02-admitted-input-v2`, the J13 reconstruction representation, the deck client outside `src/quant_platform`.
4. **ADR-0063:** non-loopback is WSS with mutual TLS; credentials map by exact fingerprint to a principal and an explicit scope set; **no** credential service, IAM, OAuth/OIDC or bearer tokens.
5. **ADR-0064:** the sealed manifest, the server as sole authority, the deck without any path or storage mount.
6. **ADR-0067:** Omega's J15 client is limited to `j02.market_data.read`; Omega use of any new family needs its own ADR-0067 amendment (not in this scope unless the maintainer adds it).
7. `J11` is not planned (owner decision 2026-10-10).

---

## Design gate

| Gate | Issue | Output | Atoms it must make implementable |
|---|---|---|---|
| **DG-K2** | #383 | One ADR (next free number) for the composition decisions below, with its guard test | `S01`-`S07` (and `S08` only when its trigger exists) |

Valid closes: an `ACCEPTED` ADR in which every closed set, bound, name and default is stated (no `[TBD]`, no `[DA CONFERMARE]`), or an explicit, recorded deferral with an evidence trigger. The ADR may not create a credential service, move a host out of `tools/`, add a binary transport family or give the server a way to push work to the deck.

### Candidate atoms (names provisional; final names are the design gate's call)

| Atom | Proposition | Requires |
|---|---|---|
| `S01` | K12 server authority seams: `resolve`, `members`, `read_member` over the DataGateway/catalog; the closed sets of accepted operation and profile identities; expiry policy values; byte and member bounds | DG-K2, `P06` |
| `S02` | K13 composition: the output-role registry and its `ResultOutputContractV1` (including replay roles requiring `replay_feature_provider`), staging bounds, a `GovernedResultImportService` factory over the `runtime` role | DG-K2, `P02`, `P06` |
| `S03` | API host composition in `tools/api_server.py`: TLS/mTLS configuration, principals, all families, the J14 store, fail-closed startup, per-host import rules | DG-K2, `S01`, `S02`, `P03`, `P05a`, `P05b` |
| `S04` | Credential and trust operations under `infra/`: issuance, rotation, revocation procedure and tooling, fingerprint mapping, secret handling; no credential service | DG-K2 |
| `S05` | Deck workload library: producer of `GovernedResultBundleV1` with `deck_code_identity` and the feature-provider identity, submit-flow glue, commit pinning; `clients/deck/` keeps importing nothing from `quant_platform` | DG-K2, `P06` |
| `S06` | Deployment units: systemd units and environment examples for the API and the worker, the runbook (ADR-0057 handoff) and the promotion procedure | DG-K2, `S03`, `P04b` |
| `S07` | Real-topology proof: PostgreSQL + API + worker + deck client over WSS/mTLS, with the fault cases the gate defines | `S01`-`S06` |
| `S08` | First data-local J03 handlers (feature/representation materialization, historical imports, catalog maintenance), each through its own issue | `P04a`, `P04b`; evidence trigger: the maintainer names a first handler |

```text
S01 ──┐
S02 ──┼─> S03 ─> S06 ─┐
S04 ──┘               ├─> S07
S05 ──────────────────┘
S08   (deferable; trigger: a named first handler)
```

(`S04` and `S05` are independent of `S01`-`S03`; `S07` requires all of `S01`-`S06`.)

### Question set (see `docs/architecture/OPEN_DECISIONS.md`, DG-K2)

1. **K12 server authority.** Which component resolves a semantic request to a sealed manifest, its ordered members, lengths and bytes; how partitions and content hashes are aligned; how accepted artifact identities are resolved; the closed sets of operation and profile identities; the expiry policy values; the byte and member bounds.
2. **K13 composition.** The closed registry of output roles and their contracts, including replay roles; staging bounds and retention; the runtime role that registers.
3. **API host composition.** The configuration surface (TLS/mTLS material, principals file, enabled families, J14 store bounds, evidence log), the fail-closed startup checks and the default composition.
4. **Credential and trust operations.** Issuance, rotation and revocation, the location of the fingerprint-to-principal mapping and of secrets, within ADR-0063 §2.
5. **Deck side.** Where the producer of `GovernedResultBundleV1` lives given that `clients/deck/` must not import `quant_platform`; how the deck code identity and the feature-provider identity are assembled; how a workload is pinned to a commit.
6. **Operations.** systemd units and environment examples, the runbook and promotion updates, and the compatibility rule between server and deck.
7. **Proof.** What "ready to split" means: the real-topology proof, its pinned dataset and its fault cases (lost response, expired admission, concurrent attempts, a refused bundle, restart).
8. **Omega.** Out of scope, or the trigger for its own ADR-0067 amendment gate.
9. **Data-local handlers.** Whether any handler is required for the split (`S08`), or whether the empty registry is an accepted state until a handler is named.

---

## Authority

- `AGENTS.md`
- governance issues #318, #381, #382; design-gate issue #383; DG-K design gate #319
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`S` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-K2)
- ADR-0043, ADR-0050, ADR-0057, ADR-0062 through ADR-0069 (with Amendments)
- `tests/test_package_boundaries_v1.py`

Accepted ADRs and frozen contracts remain normative. Registering this gate weakens none of them.

---

## Baseline and Credited State

```text
implement/dgk @ 93471e4 (DG-K closeout #381 reconciled; promotion into main is the operator's separate PR)
DG-K: P01-P06 FROZEN / COMPLETE within recorded bounds
DG-K2: S01-S07 OPEN_BLOCKING / MISSING; S08 OPEN_DEFERABLE / MISSING
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records. The families, stores, the worker host and the deck protocol client are reused, not rebuilt.

---

## Explicit non-claims

- DG-K2 and `S01`-`S08` are decided nowhere and built nowhere; nothing here is authorized for implementation.
- The server/deck topology is not deployable until `S07` is proven.
- Omega remote use of ADR-0069 families is not authorized.
- `J11` is not planned; `J07`/`J08`, `I06`/`I07`, `A13`-`A15` and second venue are untouched.

---

## Mutation policy

- This scope is governance-only until the design gate closes: only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`.
- The design gate (#383) produces one ADR and its guard test and no code.
- Implementation issues are opened only after the gate's ADR is `ACCEPTED` and a later reconciliation authorizes the atoms; each then targets the scope's integration branch (name to be confirmed), never `main`.

---

## Exit criteria for this scope

- Part A: #383's ADR `ACCEPTED`, reconciled by a later governance issue.
- Part B (after that reconciliation): `S01`-`S07` implemented and merged into the integration branch within the acceptance the ADR states, `S07` proven, followed by a governance closeout and the operator's promotion PR.

---

## Out of Scope

- a message broker, a distributed scheduler, a generic executor or a remote-execution framework;
- server-to-deck job delegation, and remote workers pulling from the server queue;
- a credential service, IAM, OAuth/OIDC or bearer tokens;
- a binary transport family or a second transport stack;
- a host outside `tools/`, or one deployed service per domain package;
- a canonical-storage migration to the deck (ADR-0057 §6);
- changing any existing identity bytes;
- Omega remote use of ADR-0069 families (ADR-0067 amendment);
- more than one J03 worker, and a durable J14 transfer store;
- `J11`, L2 acquisition, `J07`/`J08`, `I06`/`I07`, second venue.

---

## Stop / Escalation Conditions

Stop and report rather than register or decide unilaterally if:

- a candidate atom cannot be stated without a semantic decision the design gate has not taken;
- the scope would require weakening an accepted ADR (a credential service, a non-`tools/` host, an admission path that bypasses a sealed manifest, giving the deck a canonical storage mount or the server a way to push work to the deck);
- an implementation issue is proposed before #383's ADR is `ACCEPTED` and a reconciliation authorizes its atom.

# Scope: DG-K2 — Server/deck deployment composition v1 (design gate)

Status: **OPEN — design gate CLOSED, implementation of `S01`-`S07` AUTHORIZED (governance issue #389, 2026-10-16).** The design gate #383 produced ADR-0072, `ACCEPTED` by the maintainer on 2026-10-16 with the clarification of its section 11 (issue #387, PR #388). The scope id `DG-K2` and the atom names `S01`-`S08` are confirmed. No atom is implemented yet and the topology is **not deployable** until `S07` is proven in both of its parts.

Scope kind: a design-gate sequencing scope that, after the gate closed, carries the Part B authorized inventory `S01`-`S07` below. `S08` stays deferred on its trigger.

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
2. **ADR-0068:** five units; domain packages stay in-process libraries; hosts stay in `tools/`; exactly one J03 worker; the API records its deployment commit and the worker refuses a different one; compatibility is the wire family version, never the package version. (The allowance for a deck on another commit is not exercised in v1: ADR-0072 section 5 pins the deck to the server's commit, which is what `K13` already enforces.)
3. **ADR-0069 and its amendments:** the families, one least-privilege scope per family, `j02-admitted-input-v2`, the J13 reconstruction representation, the deck client outside `src/quant_platform`.
4. **ADR-0063:** non-loopback is WSS with mutual TLS; credentials map by exact fingerprint to a principal and an explicit scope set; **no** credential service, IAM, OAuth/OIDC or bearer tokens.
5. **ADR-0064:** the sealed manifest, the server as sole authority, the deck without any path or storage mount.
6. **ADR-0067:** Omega's J15 client is limited to `j02.market_data.read`; Omega use of any new family needs its own ADR-0067 amendment (not in this scope unless the maintainer adds it).
7. `J11` is not planned (owner decision 2026-10-10).

---

## Design gate (closed)

| Gate | Issue | Output | Result |
|---|---|---|---|
| **DG-K2** | #383, #387 | ADR-0072 (`docs/decisions/ADR-0072-server-deck-deployment-composition-v1.md`) with its guard test | `ACCEPTED` 2026-10-16 (PR #386 proposed it, PR #388 accepted it with section 11); all nine questions answered |

Maintainer decisions recorded at acceptance: ADR-0072 accepted in full; `S04` stays in this scope; Omega adoption is
out of scope (ADR-0072 section 8); the K12 input is a dataset selector only and artifact inputs stay refused in v1 (a
server-side accepted-artifact store would be its own design gate); strategy testing under that rule is stated in
ADR-0072 section 11; the deck is pinned to the server's commit (ADR-0072 section 5).

## Part B — Authorized implementation inventory

Each atom is its own implementation issue (`[implementation]` template), opened after this reconciliation merges,
citing its row here. Every one targets the integration branch below, never `main`.

**Integration branch:** `implement/dgk2`, cut from `implement/dgk` (proposed name; the maintainer confirms it and the
cut). Atom branches are `agent/issue-<n>-<slug>`.

| Atom | Result | Requires (besides ADR-0072) | Start gate |
|---|---|---|---|
| `S01` | K12 server authority (`admitted_input_authority`) | `P06` | startable |
| `S02` | K13 composition (role registry and import factory) | `P02`, `P06` | startable |
| `S03` | API host composition in `tools/api_server.py` | `S01`, `S02`, `P03`, `P05a`, `P05b` | after `S01` and `S02` are merged |
| `S04` | Credential and trust operations under `infra/pki/` | none | startable |
| `S05` | Deck workload module (`deck_result_bundle`) | `P06` | startable |
| `S06` | Deployment units, promotion and runbook | `S03`, `P04b` | after `S03` is merged |
| `S07` | Two-part topology proof | `S01`-`S06` | after `S01`-`S06` are merged |
| `S08` | First data-local J03 handlers | `P04a`, `P04b` | **not authorized**; trigger: the maintainer names a first handler |

```text
S01 ──┐
S02 ──┼─> S03 ─> S06 ─┐
S04 ──┘               ├─> S07
S05 ──────────────────┘
S08   (deferable; trigger: a named first handler)
```

Order: the default is `S01`, `S02`, `S03`, `S04`, `S05`, `S06`, `S07`, one bounded mutation slice at a time.
`S01`, `S02`, `S04` and `S05` have no dependency on each other, so the maintainer may run them concurrently; that is
his call, not an implication of this scope.

### Acceptance per atom (from ADR-0072; an implementation issue adds no semantic decision)

- **`S01`** (ADR-0072 section 1). A module over the DataGateway that provides `resolve`, `members` and `read_member` for
  `admitted_input_family_handlers`. The input is a `ConsumerMarketDataQuery` resolved by `resolve_market_data_request`;
  the manifest is built from `DataScanOpenMetadata` without reading a record, with the field values of the ADR table
  (`git_identity` from `runtime.deployment_commit`, `result_identity` null, members = catalog partitions with
  `manifest_sha256`/`content_sha256`). Refusals: `no_coverage`, `source_not_found`, `unsupported_representation`
  through the public projection, and `invalid_request`/`artifact_inputs_unavailable` for any `artifact_identities`;
  nothing is sealed on a refusal. Operation/profile identities are carried, not a closed set. No time-based expiry.
  Bounds: `max_members` 16, `max_member_bytes` 32 MiB, `max_total_bytes` 64 MiB, `max_retained_payload_bytes` 96 MiB,
  `max_in_flight_transfers` 32, `retention_seconds` 300.
- **`S02`** (section 2). A factory for `GovernedResultImportService` over the API login's `runtime` connection with the
  Experiment repository on the same connection; the closed registry of the six roles in the ADR table
  (`ResultOutputContractV1` for the four supervised roles, `ReplayOutputContractV1` for `replay-result` and
  `replay-summary`); the content identity is the checksum of the uploaded bytes; staging bounds 64 KiB frames, 8 MiB
  output, 32 MiB staged, 8 uploads, 900 s; a role outside the registry is refused.
- **`S03`** (section 3). `tools/api_server.py` gains `--tls-certificate`, `--tls-private-key`, `--client-trust-anchor`,
  `--principals-file` (with environment equivalents) and `--families` (default none, so an unchanged invocation stays
  the J02 v1 market-data server); the policy and trust-bundle versions are SHA-256 of the file bytes and appear in the
  startup output and the security evidence; every fail-closed startup refusal of the ADR before bind; one shared
  `FramedResultTransferStore` with `framed_resume_scopes`; one database connection per request; the per-host import
  rule (ADR-0068 section 7).
- **`S04`** (section 4). `openssl` scripts under `infra/pki/` (offline CA, server certificate, client certificate with
  its `sha256:<hex>` fingerprint; EC P-256; CA 3650, server 397, client 90 days), an example principals file without
  fingerprints, the key-handling rules (outside Git, mode `0600`, never environment or unit file) and the
  restart-based rotation and revocation procedure with the 14-day overlap limit. No credential service, CRL or OCSP.
- **`S05`** (section 5). A `quant_platform.application` module `deck_result_bundle` that takes the verified admission as
  plain data and returns the wire document: refuses an unclean tree or a commit different from the manifest's
  `git_identity`; builds the `RunIdentity` covering the logical inputs and definitions; builds each output's upload
  bytes and `ArtifactRegistration` with the checksum content identity for a registry role; adds
  `replay_feature_provider` for replay roles; builds the bundle and `bundle_to_wire`. `clients/deck/` imports nothing
  from `quant_platform`; the module opens no connection and imports no client.
- **`S06`** (section 6). `quant-platform-api.service` and `quant-platform-worker.service` with environment examples
  under `infra/systemd/` (hardening as `quant-platform-live-ingest.service`), the worker ordering and exit-3 rule, the
  promotion and the same-commit rule for the deck, the compatibility rule (family versions plus commit equality for
  result acceptance), and the rewritten handoff runbook in `docs/engineering/` with the ADR-0064 Amendment 1 failure
  procedures.
- **`S07`** (section 7). The hermetic topology proof (API and worker as separate processes, the deck client and the
  workload module as a third; loopback TLS 1.3 mTLS; a disposable PostgreSQL; the catalog from the generated trade
  fixtures; the six listed groups of cases including the faults, the security cases with the rotation drill and the
  measured peak API memory) and the supervised real-environment proof (the `real-server-proof` pattern, recording the
  real partition sizes against the bounds). The topology may be called deployable only after both parts.

---

## Authority

- `AGENTS.md`
- governance issues #318, #381, #382, #389; design-gate issues #383, #387; DG-K design gate #319
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`S` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-K2)
- ADR-0043, ADR-0050, ADR-0057, ADR-0062 through ADR-0069 (with Amendments), **ADR-0072** (`ACCEPTED`; the normative text of Part B)
- `tests/test_package_boundaries_v1.py`

Accepted ADRs and frozen contracts remain normative. Registering this gate weakens none of them.

---

## Baseline and Credited State

```text
implement/dgk @ the merge of PR #388 (ADR-0072 ACCEPTED; DG-K closeout #381 reconciled; promotion into main is the operator's separate PR)
DG-K: P01-P06 FROZEN / COMPLETE within recorded bounds
DG-K2: S01-S07 FROZEN / MISSING (authorized by #389); S08 OPEN_DEFERABLE / MISSING (not authorized)
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records. The families, stores, the worker host and the deck protocol client are reused, not rebuilt.

---

## Explicit non-claims

- No atom is implemented: `S01`-`S07` are decided and authorized, built nowhere.
- The server/deck topology is not deployable until `S07` is proven in both parts (hermetic and supervised).
- `S08` and any data-local J03 handler are not authorized; the registry stays empty.
- Omega remote use of ADR-0069 families is not authorized.
- Accepted-artifact inputs, time-based expiry, a closed operation/profile registry and a deck on a commit other than the server's are not provided in v1.
- `J11` is not planned; `J07`/`J08`, `I06`/`I07`, `A13`-`A15` and second venue are untouched.

---

## Mutation policy

- `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` and `OPEN_DECISIONS.md` change only on `governance/` or `admin/` branches under an issue designated governance-only.
- Each of `S01`-`S07` is an ordinary `[implementation]` issue on an `agent/issue-<n>-<slug>` branch targeting `implement/dgk2`; it edits no governance file and reports staleness findings instead.
- An implementation issue may add or update only what its atom authorizes (its module, tests, `infra/` files, docs); a change to ADR-0072 needs a design-gate issue.

---

## Exit criteria for this scope

- Part A (done): ADR-0072 `ACCEPTED`, reconciled by #389.
- Part B: `S01`-`S07` implemented and merged into the integration branch within the acceptance above, `S07` proven in both parts, followed by a governance closeout and the operator's promotion PR.

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
- an implementation issue needs a semantic decision ADR-0072 does not state (report it; do not decide it in the issue);
- an implementation issue is proposed for `S08` or for any atom outside `S01`-`S07`.

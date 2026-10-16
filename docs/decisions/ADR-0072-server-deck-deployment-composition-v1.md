# ADR-0072 - Server/deck deployment composition v1 (DG-K2)

**Status:** PROPOSED (design gate #383, for maintainer acceptance; it becomes ACCEPTED when the maintainer accepts it)
**Date:** 2026-10-16

The number is the next free one at authoring time: ADR-0070 exists on `implement/l2` (L2) and ADR-0071 is the number
the FEI line (issue #363) is drafting. Renumber at merge if the maintainer prefers; nothing else references it by
number except the guard test.

## Context

DG-K (ADR-0068, ADR-0069 and their amendments) delivered the parts of the server/deck split: the J02 families for
`J10`/`J12`/`J13` and jobs, `K12` delivery as `j02-admitted-input-v2`, `K13` import, the deck protocol client in
`clients/deck/`, the PostgreSQL `runtime` schema and the single worker host. Verified on `implement/dgk` @ `93471e4`
(closeout #381), what is missing is composition:

- `tools/api_server.py` serves the J02 v1 market-data message only and has no TLS/mTLS options; `RemoteJ02SecurityConfig`
  is constructed only in tests, and `_validate_family_routes` refuses family routes without it.
- The server-owned seams of `K12` (`resolve`, `members`, `read_member`) have only test authorities.
- `GovernedResultImportService` (`K13`) is composed in no host, and no output-contract registry exists.
- Nothing produces a `GovernedResultBundleV1` for a deck workload, and `clients/deck/` cannot import
  `quant_platform`.
- ADR-0063 leaves certificate issuance, secret storage and deployment out of scope.
- `DATA_LOCAL_JOB_REGISTRY` ships empty.
- The acceptance tests put SQLite or `FakeConnection` stores behind a real WSS/mTLS listener; PostgreSQL is tested
  separately.

Fixed inputs, not reopened: ADR-0057 (two surfaces; the deck acts on its own initiative; the server never pushes
work), ADR-0063 (non-loopback is WSS with mTLS; exact-fingerprint principals; no credential service, IAM or bearer
tokens), ADR-0064 (sealed manifest; the server is the sole authority; no path or storage mount on the deck), ADR-0068
(five units; hosts in `tools/`; one worker; code identity between units), ADR-0069 with its amendments, `J11` not
planned.

One finding of this gate, flagged because it touches two accepted texts. `K13` refuses a bundle whose
`deck_code_identity` or `RunIdentity.code_identity` differs from the sealed manifest's `git_identity`
(`GovernedResultImportService._validate_admission` and `_validate_run_compatibility`), and the K12 admit request
carries no commit. ADR-0068 §8 says the deck "may run a different commit" and that compatibility is decided by wire
family version. Both cannot hold for a manifest whose `git_identity` is a server-side fact. Section 5 resolves it in the
direction the implemented `K13` already enforces, and records the alternative as a deferral.

## Decision

### 1. K12 server authority (S01)

A new application module (`admitted_input_authority`) provides the three seams of
`admitted_input_family_handlers` over the DataGateway. It is composed by the API host (section 3); the deck never
names a dataset, catalog entry, partition or path.

**Admit.** The only admissible input in v1 is a **dataset selector**: a `ConsumerMarketDataQuery` (venue, instrument,
interval, `trades@1`), resolved by the same `resolve_market_data_request` the Consumer API uses, so the semantic
request is identical to a C03 query. The manifest is built from the gateway's *open* scan metadata
(`DataScanOpenMetadata`), without reading a record:

| Manifest field | Value |
| --- | --- |
| `logical_input_identities` | the dataset's natural identity (one) |
| `natural_partition_identities`, `manifest_hashes`, `content_hashes` | the selected partitions, in the gateway's order, with the catalog's `manifest_sha256` and `content_sha256` |
| `schema_identity`, `schema_version`, `schema_hash` | the dataset's record schema (`trades@1`) |
| `declared_coverage` | the requested interval, only if the eligible coverage is complete |
| `request_identity` | the gateway's request identity |
| `result_identity` | `null` (no scan is completed) |
| `definition_identities` | empty (raw trades have no representation definition) |
| `implementation_identity` | `null` |
| `git_identity` | the platform commit recorded in `runtime.deployment_commit` by the API at startup |
| `operation_identity`, `profile_identity` | as the deck declared them |

Incomplete coverage, an unknown dataset, an unsupported venue or representation are refused with the Consumer API's
existing codes (`no_coverage`, `source_not_found`, `unsupported_representation`) through the public projection of
ADR-0069 Amendment 1; nothing is sealed.

**Accepted-artifact inputs are not admissible in v1.** No accepted-artifact store or resolver exists in the
repository (there is no server-side catalog of sealed `FeatureArtifact` objects), so an `admit` carrying
`artifact_identities` is `invalid_request` / `artifact_inputs_unavailable`. Trigger to reopen: a server-side store of
accepted artifacts with a resolver.

**Members and bytes.** Member *i* is partition *i*: its identity is the natural partition identity, its content hash is
the catalog's `content_sha256`, its length is the size of the partition file, and its bytes are that file read through
the catalog's storage root (`aligned_partition_members`). The path is resolved internally; the transport still hashes
every member against the manifest before anything is retained.

**Operation and profile identities are not a closed set.** Nothing in the platform interprets them: they are carried
verbatim in the manifest and the bundle. They are accepted when they pass the identity-text grammar the transport
already enforces (bounded, non-empty, no locator-shaped text) and are never an authorization. A closed registry is
deferred until some component interprets them.

**Expiry.** v1 has **no time-based expiry**. The recoverability window of the bytes is bounded by the J14 retention;
a pending attempt is ended by the deck (`end_pending_attempt`, ADR-0064 Amendment 1 points 5 and 7); `EXPIRED` is
applied only by an explicit server-side operator action, which v1 does not provide as a tool. Trigger to add a sweep:
a recorded abandoned `DELIVERY_PENDING` admission that matters operationally, at which point the store gains a
timestamp (`runtime.admitted_inputs` has none today).

**Bounds.** Configuration, not wire format; v1 values are `max_members` 16, `max_member_bytes` 32 MiB,
`max_total_bytes` 64 MiB, with the J14 transfer store sized by the rule the composition already enforces
(`4 * ceil(max_total_bytes / 3)` plus 8 KiB per member): `max_retained_payload_bytes` 96 MiB,
`max_in_flight_transfers` 32, `retention_seconds` 300. The previous defaults (3 MiB member, 4 MiB total, 8 MiB
retained) would refuse any realistic daily partition. These values are a proposal derived from the composition rule;
no real partition size was measured for this gate, and `S07` records the measured sizes. If a measured partition
exceeds the member bound the remedy is configuration; a request beyond what bounded JSON/base64 framing can carry
needs the deferred binary family (ADR-0069 §5) and is not a v1 input.

### 2. K13 composition (S02)

A factory builds `GovernedResultImportService` over the `runtime` PostgreSQL connection of the API login, with the
Experiment repository on the **same connection** (the atomic registration of ADR-0068 §4; the service already refuses
anything else).

**Output roles (closed registry).** The registry is exactly the four existing supervised roles and two replay roles:

| Role | Kind | Schema | Contract class |
| --- | --- | --- | --- |
| `normalizer` | `supervised-normalizer` | `supervised-normalizer-v1` | `ResultOutputContractV1` |
| `model` | `supervised-model` | `supervised-model-v1` | `ResultOutputContractV1` |
| `predictions` | `supervised-predictions` | `supervised-predictions-v1` | `ResultOutputContractV1` |
| `metrics` | `supervised-metrics` | `supervised-metrics-v1` | `ResultOutputContractV1` |
| `replay-result` | `replay-result` | `replay-result-v1` | `ReplayOutputContractV1` |
| `replay-summary` | `replay-summary` | `replay-summary-v1` | `ReplayOutputContractV1` |

The two replay kinds and schemas reuse the existing identity domains `replay-result-v1` and `replay-summary-v1`
(`replay/__init__.py`); no artifact kind existed for replay before. A role outside the registry is refused. The
content identity of an output is the K13 checksum of the uploaded bytes (`sha256:<hex>`), because `ResultOutputEvidenceV1`
requires the registration's content identity to equal the checksum the server re-computes from the staged bytes.

**Staging bounds** keep the shipped defaults: 64 KiB frames (the client's default, so no layout negotiation),
`max_output_bytes` 8 MiB, `max_staged_bytes` 32 MiB, `max_in_flight_uploads` 8, `retention_seconds` 900. An output
above the bound is not registrable in v1 (ADR-0069 §6) and stays deck-local evidence.

**Which role registers.** The API login registers; the deck holds no database login (ADR-0068 §4).

### 3. API host composition (S03)

`tools/api_server.py` stays the host (argv, environment, signals) and calls one composition function in
`quant_platform.application`. New options, all with environment equivalents:

- `--tls-certificate`, `--tls-private-key`, `--client-trust-anchor`: the ADR-0063 material;
- `--principals-file`: the fingerprint-to-principal mapping, a JSON document
  `{"version": 1, "principals": [{"principal_id": "...", "fingerprint": "sha256:<hex>", "scopes": ["..."]}]}`;
- `--families`: a comma-separated subset of `strategy,validation,training,jobs,admitted-input,result-import`; the
  **default is none**, so a host started as today stays the J02 v1 market-data server.

`authorization_policy_version` is the SHA-256 of the principals file bytes and `server_trust_bundle_version` the
SHA-256 of the client trust-anchor file; both appear in the startup output and in every security evidence event
(ADR-0063 §3).

**Fail-closed startup** (each is a refusal before bind): any family without complete TLS/mTLS material; a non-loopback
bind without it (already enforced); a principals file that is malformed, has a duplicate fingerprint or a scope outside
`J02_REMOTE_SCOPES`; a family enabled whose scope no principal holds is allowed (it is simply unreachable) but is
printed; `jobs` without the runtime DSN; `admitted-input` or `result-import` without the catalog DSN or the runtime DSN;
the deployment commit not recorded (already enforced).

One `FramedResultTransferStore` is shared by every family that frames results and given to the connection handler with
`framed_resume_scopes` equal to the scopes of the enabled framing families. Every request opens its own database
connection (the stores are used from worker threads). The per-host import rule of ADR-0068 §7 applies: the host
imports only `quant_platform.application`, the standard library and declared third-party packages.

### 4. Credential and trust operations (S04)

Within ADR-0063 §2: certificates and a fingerprint file, no credential service.

- **Tooling** under `infra/pki/`: shell scripts around `openssl` (already the tool of the test fixtures) that create
  an offline CA, issue a server certificate with the endpoint's DNS/IP names, issue a client certificate per principal
  and print its SHA-256 DER fingerprint in the `sha256:<hex>` form the principals file uses. Keys are EC P-256; TLS 1.3
  only.
- **Validity:** CA 3650 days, server certificate 397 days, client certificate 90 days.
- **Secrets:** private keys never enter Git, an environment variable or a unit file; they are files outside the
  repository, mode `0600` (owner: the service user), read at startup. The CA private key stays offline.
- **Mapping:** the principals file lives next to the other operational configuration (`/etc/quant-platform/`), not in
  Git; the repository carries an example without fingerprints.
- **Rotation and revocation are restart-based and atomic.** Configuration is read once at startup. Rotation adds the
  replacement fingerprint (same or narrower scopes) for an overlap of at most 14 days, then removes the old one;
  emergency revocation removes the fingerprint. Each is "edit the file and restart the API unit", which terminates
  every session, as ADR-0063 §3 requires, and cannot leave a half-applied policy. There is no CRL or OCSP: authorization
  is by exact fingerprint, so a CA-valid certificate whose fingerprint is not in the file is denied.
- **Evidence:** the two versions above identify which policy decided a connection.

### 5. Deck side (S05)

**Code identity (the finding).** v1 pins the deck to the server's commit. The manifest's `git_identity` is the platform
commit the API recorded (section 1); a deck workload must run that exact commit, and its `deck_code_identity` and
`RunIdentity.code_identity` equal it, which is what `K13` enforces. This is consistent with ADR-0057 §2 ("both
surfaces use an immutable commit or release tag") and means ADR-0068 §8's allowance for a deck on a different commit
is **not exercised** in v1: the wire family version still decides whether a deck can talk to a server, but a result is
accepted only for the server's commit. Allowing a different deck commit would need the admit request to carry the
commit the deck will run, hence a required field and a new family version under ADR-0066 §1; it is deferred behind the
trigger "a deck workload that must run a commit other than the server's".

**Where the producer lives.** `quant_platform.application` gains a deck-workload module (`deck_result_bundle`). It is
imported by deck workloads (Omega, notebooks), which already run the platform library in-process (ADR-0069 §8); the
protocol client stays in `clients/deck/` and imports nothing of `quant_platform`, so the module takes the verified
admission as plain data and returns the wire document the client submits. It:

1. refuses to run unless the workload tree is clean and its commit equals the manifest's `git_identity` (the
   clean-tree rule of `feature_provider_payload`);
2. builds the `RunIdentity` so that it carries the manifest's code, covers `logical_input_identities` in its data
   identities and covers `definition_identities` (the `K13` compatibility checks);
3. turns each output into upload bytes and an `ArtifactRegistration` whose content identity is the checksum of those
   bytes, for a role of the closed registry (section 2);
4. adds `replay_feature_provider` (ADR-0069 §7) for replay roles;
5. builds the `GovernedResultBundleV1` and its wire document (`bundle_to_wire`).

The upload and submit calls are the client's `upload_output` and `submit`; the module neither opens a connection nor
imports the client.

### 6. Operations (S06)

- Units under `infra/systemd/`, modeled on `quant-platform-live-ingest.service` (dedicated user, `NoNewPrivileges`,
  `ProtectSystem=strict`, `UMask=0027`): `quant-platform-api.service` and `quant-platform-worker.service`, each with an
  environment example. The worker has `After=` the API and PostgreSQL; it exits `3` on a commit mismatch and
  `Restart=on-failure` does not mask that, because the API records its commit before it listens and a restart of the
  pair always converges.
- **Promotion.** `infra/promote-main.sh` (clean tree, fast-forward only) updates the server checkout; the API and
  worker restart together. Because a deck result is accepted only for the server's commit (section 5), the deck
  checks out the same commit before a run; a mismatch is refused by the workload module before any work.
- **Compatibility rule.** A deck can use a server when the message family versions of the families it speaks are the
  server's (ADR-0066 §1, ADR-0067 §3 rule applied per family); acceptance of a result additionally requires the commit
  equality above. The package version decides nothing.
- **Runbook.** The ADR-0057 handoff runbook is rewritten as a procedure over these units and the deck client (select a
  commit, `admit`, `deliver`, verify, run, upload, `submit`, record the registration), in
  `docs/engineering/`, with the failure procedures of ADR-0064 Amendment 1 (lost response, stale acknowledgement).

### 7. What "ready to split" means (S07)

The split is ready when both parts of the proof pass and are recorded without secrets or bulk market data.

**Hermetic topology proof** (repeatable, loopback with real TLS 1.3 mTLS, a disposable PostgreSQL, a catalog built from
the repository's generated trade fixtures, as the Golden harnesses do): the API and the worker as separate processes
started from the units' commands; the deck client and the workload module as a third. It must show:

1. admit, deliver, verify, acknowledge, run a small replay and a small training, upload, submit, and see the
   registration in the Experiment tables and the evidence ledger in one transaction; an identical resubmission returns
   the same registration;
2. `J10`, `J12` and `J13` (with `register: true` and without) over the same listener, equal to the in-process results;
3. `j02-job-v1` composed and refusing an unregistered kind (no handler is claimed: the registry is empty);
4. faults: a lost `deliver` response ended by `end_pending_attempt` and redelivered; a corrupted frame and a
   consistently rewritten member refused by the client; a stale acknowledgement refused; a bundle with a wrong
   output checksum refused with no Experiment mutation; an API restart between upload and submit (staged bytes gone,
   submit refused, nothing registered) and between deliver and acknowledge; a worker started on another commit exits
   `3` and touches no Job;
5. security: a client certificate of another CA, an unmapped fingerprint and a scope-less family are denied; the
   rotation drill (add the new fingerprint, restart, remove the old one, the old is denied);
6. measured peak memory of the API during a maximum-size delivery, as evidence.

**Supervised real-environment proof** (the `real-server-proof` pattern): the same procedure with the real server and
the real deck on the smallest real partition, recording the partition sizes against the bounds of section 1. A bound
exceeded by a real partition is a recorded finding about configuration, not a failure of the mechanism.

The hermetic proof is `S07`'s acceptance; the supervised proof is its second criterion, and the topology may be called
deployable only after both.

### 8. Omega

Out of scope. Omega's J15 client stays limited to `j02.market_data.read` (ADR-0067). Trigger: a maintainer request for
Omega to use the deck handoff, which then needs its own ADR-0067 amendment.

### 9. Data-local handlers

The empty `DATA_LOCAL_JOB_REGISTRY` is an accepted state for the split: no handler is required to deploy or prove it
(`S08` is deferable). Trigger: the maintainer names a first handler; each enters through its own issue citing
ADR-0068 §6.

### 10. Why none of this adds a broker, a credential service or a host outside `tools/`

The composition is configuration and composition code inside `quant_platform.application`, one host that stays in
`tools/`, shell tooling around `openssl`, and units that start existing executables. Nothing is delegated to the deck,
nothing pulls from the queue, nothing is pushed, and no transport other than WebSocket/JSON over the existing
families is used (ADR-0068 §9, ADR-0063).

## Consequences

- A deck result is accepted only for the server's commit. The server and the deck move together on promotion; this
  trades independence for a check `K13` already makes, and the trigger in section 5 is the way out.
- Dataset inputs only, and only up to the configured bounds. Artifact inputs and very large partitions are future work
  with explicit triggers.
- No time-based expiry and no CRL: operational procedures (deck-side recovery, restart-based revocation) carry what a
  service would.
- Raising the transfer bounds makes the API process hold up to about 96 MiB of retained frames plus a transient
  second copy during `deliver`; `S07` measures it.

## Implementation atoms

`S01` K12 authority; `S02` K13 composition; `S03` API host composition; `S04` credential and trust operations; `S05`
deck workload module; `S06` deployment units and runbook; `S07` the two-part proof; `S08` deferred. `S04` and `S05`
are independent of `S01`-`S03`; `S06` requires `S03`; `S07` requires all of them. Each is its own implementation issue
under the next governance reconciliation, which also authorizes them; this ADR authorizes none.

## Out of scope

- Omega adoption; a credential service, IAM, OAuth/OIDC or bearer tokens; a binary transport family; a host outside
  `tools/`; server-to-deck delegation; more than one J03 worker; a durable J14 transfer store; accepted-artifact
  inputs; time-based expiry; a closed operation/profile registry; `J11`; L2 acquisition.

## Acceptance evidence

`tests/test_deck_deployment_composition_contract_v1.py` guards the decisions above as a regression guard for this
design authority. It is not implementation or a proof of the topology.

## Related

Governance issues #381, #382; design gate #383. Builds on ADR-0057, ADR-0063, ADR-0064 (Amendment 1), ADR-0066
(Amendment 1), ADR-0067, ADR-0068 (Amendment 1) and ADR-0069 (Amendments 1 and 2). Amends no ADR; section 5 records a
conflict between ADR-0068 §8 and the implemented `K13` and resolves it in `K13`'s direction without editing either.

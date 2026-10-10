# ADR-0069 - J02 carriage of consumer seams and deck handoff v1 (DG-K)

**Status:** ACCEPTED
**Date:** 2026-10-10

## Context

Wave 8 implemented `J10`, `J12` and `J13` as in-process application seams
(`application/strategy_consumer.py`, `validation_consumer.py`,
`training_consumer.py`). ADR-0065 deliberately defined no wire message or
remote scope for them, and ADR-0063 defines only `j02.market_data.read`
(`application/api_transport_server.py`). `J03` admits Jobs but has no client
submission or status API (ADR-0062, Consequences). `K12` seals manifests and
`K13` imports bundles, but ADR-0064 §4 defines "no wire shape, endpoint,
credential, TLS configuration, or transport mechanism" for them, so nothing
moves admitted bytes to the deck or result bundles back.

Constraints already accepted: J02 v1 stays unchanged and new message families
are additive (ADR-0066 §1); WebSocket and JSON are the only transport stack;
every J02 message is bounded by the 16 MiB `max_size` on both ends (ADR-0050
Amendment 1 §2); J14 frames only complete, already-produced finite results
(ADR-0066 §2); authorization precedes decoding and denial is close `1008`
(ADR-0063 §2). ADR-0068 fixes the topology this ADR serves: bounded work runs
in the API process, data-local Jobs run in the single worker, and the deck
initiates every server/deck exchange. `J11` is not planned (#318, decision 6).

## Decision

### 1. Common envelope and dispatch

Every family below is additive to J02 on the same WSS/mTLS endpoint (ADR-0063).
A request is one JSON message:

```json
{
  "message_family": "<family>",
  "schema_version": "<family>-request-v1",
  "request_id": "<caller-supplied opaque string, echoed back>",
  "operation": "<operation name declared by the family>",
  "request": {}
}
```

and its response is one JSON message with `message_family`,
`schema_version: "<family>-response-v1"`, the echoed `request_id`, and either
`status: "ok"` with `result` or `status: "error"` with `error`. `request` and
`result` are the lossless canonical payloads of the owning application seam;
`error` carries the existing Consumer API error vocabulary (`code`, `message`,
`context`, `request_identity`) and its stable reasons, never a new
transport-specific code. A message without `message_family` remains a J02 v1
market-data message, unchanged.

The server reads only `message_family`, `schema_version` and `operation`
before authorization. It resolves the family's required scope (§2), denies a
principal without it with close `1008` before decoding `request` (ADR-0063
§2), and rejects an unknown family or version as unsupported. Clients reject an
unexpected family or version as `wire_incompatible` (ADR-0067 §3 rule,
applied per family). Every request and response message stays within the
16 MiB bound; results that may exceed it are delivered only as J14 transfers
(§5, §6).

### 2. Remote scopes, one per operation family

| Scope | Family | Grants |
| --- | --- | --- |
| `j02.market_data.read` | J02 v1 (existing) | C03 market-data query only |
| `j02.strategy.compose` | `j02-strategy-v1` | J10 `strategy-compose-v1` |
| `j02.validation.evaluate` | `j02-validation-v1` | J12 finite operations within budget |
| `j02.training.evaluate` | `j02-training-v1` | J13 train-and-evaluate without registration |
| `j02.training.register` | `j02-training-v1` | J13 with server-side Experiment registration |
| `j02.jobs.submit` | `j02-job-v1` | J03 submission and cancellation request for registered data-local handlers |
| `j02.jobs.read` | `j02-job-v1` | J03 status and result reference |
| `j02.admitted_input.read` | `j02-admitted-input-v1` | K12 admission request, manifest read, byte delivery |
| `j02.result.submit` | `j02-result-import-v1` | output upload and K13 bundle submission |

Scopes never imply one another; a principal's scope set is explicit and
immutable per ADR-0063 §2. None grants canonical storage, catalog, checkpoint,
backup, publication, Job recovery or administrative access. Recovery and
retry of a `RECOVERY_REQUIRED` Job stay operator actions in v1.

### 3. P05a - synchronous J10, J12 and J13 over J02

- `j02-strategy-v1`, operation `compose`: request and result are exactly the
  ADR-0065 §2 `strategy-compose-v1` request and `StrategyCompositionResult`.
- `j02-validation-v1`, operations `build_folds`, `classify_candidate`,
  `evaluate_dsr` and `evaluate_pbo`: the ADR-0065 §3 requests and results,
  including Amendment 1's `pbo_sync_budget_exceeded` refusal.
- `j02-training-v1`, operation `train_evaluate` with a boolean `register`:
  the ADR-0065 §4 request and `SupervisedTrainingRunResult`, including
  Amendment 2's `training_sync_budget_exceeded` refusal. `register: true`
  requires `j02.training.register` in addition to `j02.training.evaluate`.

No request semantics, budget, result payload or identity changes. A request
outside its synchronous budget keeps its refusal; it is not converted into a
Job (ADR-0068 §3: that work belongs on the deck).

J13 request size: a J13 request carries a full `SupervisedProjection` inline,
and J14 frames results only. In v1 the whole request message must fit the
16 MiB bound. The client computes the encoded request size before sending and
refuses locally with `request_too_large` when it exceeds the bound; the
server's `max_size` close remains defense in depth, never the resource
control. Amendment 2's measured request cost (about 660-790 bytes per
sample-feature, at most 20,000 sample-features) keeps admissible requests
below the bound in that measurement. Referencing a projection by an admitted
identity instead of inlining it is deferred; its trigger is a recorded
admissible J13 request that the bound refuses.

### 4. P05b - data-local Jobs over J02

`j02-job-v1` operations:

- `submit`: an operation kind from the closed ADR-0068 §6 registry and that
  handler's semantic request. The API persists the ADR-0062 admission record
  and returns the deterministic `job_id`; an identical re-submission returns
  the same Job, a conflicting one fails closed (ADR-0062 §2).
- `status`: the Job's bounded ADR-0062 §5 fields (state, attempts, reason
  code, result and evidence references).
- `result`: the immutable result references of a `SUCCEEDED` Job. A result
  body larger than one message is delivered as a J14 transfer whose logical
  result identity is that result reference.
- `cancel`: records cancellation for an `ADMITTED`/`QUEUED` Job or
  `CANCELLATION_REQUESTED` for a `RUNNING` one, per ADR-0062 §5.

An unknown operation kind is `invalid_request`; there is no way to submit a
callable, module path or handler that is not registered. This depends on
P04b; without a worker, nothing would execute.

### 5. P06 - K12 delivery to the deck

`j02-admitted-input-v1` operations, all initiated by the deck:

- `admit`: a semantic input request (dataset selector with interval and
  representation, or accepted artifact identities, plus the declared
  operation/profile identity). The server resolves it against its own
  authority and seals an `AdmittedInputManifestV1` (ADR-0064 §2), returning
  the manifest, `manifest_digest` and `admission_id`. The deck never supplies
  a path, catalog UUID or storage root.
- `manifest`: returns the sealed manifest for an `admission_id`.
- `deliver`: starts a delivery attempt for a `SEALED` admission
  (`DELIVERY_PENDING`) and delivers each manifest member (natural partition
  or artifact) as its own J14 transfer. The framed application result is
  `admitted-input-member-v1`: `admission_id`, `manifest_digest`, member index,
  member identity, its manifest content hash, `byte_length`,
  `encoding: "base64"` and the member bytes. Its logical result identity is
  `admitted-input-member-v1:<admission_id>:<member index>`.
- `acknowledge`: the deck reports that every member verified against the
  manifest content hashes (`DELIVERED`) or names the member that failed
  (`DELIVERY_FAILED`). A failed attempt is retried as a new delivery of the
  same sealed admission; an expired admission needs a new `admit`.

The deck uses member bytes only after the J14 digests and the manifest content
hash both verify, and records `admission_id` and `manifest_digest` with its
work (ADR-0064 §2). Base64 inside JSON is the v1 cost of keeping one transport
stack; a binary family would need its own ADR-0066 amendment. The server
bounds bytes per delivery and in-flight transfers and may refuse an `admit`
that exceeds them.

### 6. P06 - K13 submission from the deck

`j02-result-import-v1` operations, all initiated by the deck:

- `upload_output`: uploads one output's bytes in chunks that follow the J14
  frame rules (deterministic offsets, per-chunk and whole-payload SHA-256,
  transfer-local resume, bounded retention), in the client-to-server
  direction. The server stages the bytes as non-canonical evidence keyed by
  their checksum. Staged bytes not referenced by a successful registration
  expire.
- `submit`: one `GovernedResultBundleV1` (ADR-0064 §3). K13 verifies the
  admission, every declared input digest and every output checksum against
  the staged bytes, then registers through its existing owner; any mismatch
  refuses the whole bundle with no mutation. Re-submission of the identical
  bundle returns the same registration.

An output that exceeds the configured upload bound cannot be registered in
v1: its bundle is refused, and the output stays deck-local evidence.

### 7. Feature-provider code identity in replay results

A deck replay result submitted through K13 carries the code identity of the
`feature_provider` it ran with, so the result is reproducible from Git:

```json
{
  "identity_domain": "feature-provider-v1",
  "repository": "<repository name, no credentials>",
  "commit": "<full Git commit of a clean tree>",
  "qualified_name": "<module>:<attribute>",
  "source_sha256": "<SHA-256 of the defining module file at that commit>",
  "parameters": {}
}
```

Its identity is `feature-provider-v1:sha256:<hex>` over this payload in the
ADR-0068 `sorted-compact-ascii-v1` profile. A tree with uncommitted changes
cannot produce a valid identity. The deck places the payload and its identity
in the bundle's `configuration` under the key `replay_feature_provider`; the
K13 output contract for every replay-produced output role requires it and
refuses the bundle without it. `GovernedResultBundleV1`'s fields and digest
rules are unchanged.

This identity is attribution evidence only. The server never resolves,
imports or executes it, so it does not supply the server-resolved provider
reference whose absence defers `J11` (ADR-0065 §5).

### 8. Deck-side client

The deck client lives in `clients/deck/`, outside `tools/` and
`src/quant_platform`, and does not import `quant_platform`: it speaks only the
families in §5 and §6 over WSS/mTLS with the deck's credential holding
`j02.admitted_input.read` and `j02.result.submit` and nothing else. It
verifies every delivered digest before handing bytes to the deck workload and
submits bundles the workload produced. It never falls back to an in-process
call or another transport (ADR-0067 §1 rule). Deck workloads (Omega,
notebooks) run replay and training in-process with the platform library.

### 9. Omega

Omega's J15 client remains limited to `j02.market_data.read`. Omega use of any
family in this ADR needs its own ADR-0067 amendment and is not authorized
here.

## Consequences

- `J10`/`J12`/`J13` become reachable remotely with unchanged semantics and
  least-privilege scopes; over-budget work stays a refusal, not a hidden Job.
- Data-local Jobs get a remote client path without any route to arbitrary
  execution.
- The deck can fetch exactly the sealed input it asked for and return
  verifiable, attributable results, so ADR-0057's handoff runbook is
  executable over the network.
- Moving bytes through JSON costs about a third more bandwidth than binary;
  accepted for v1.

## Implementation atoms

`P05a` requires J02/J09; `P05b` requires `P04b`; `P06` requires J09, J14 and
`P02`. Each is its own implementation issue under the next governance
reconciliation.

## Out of scope

- changes to J02 v1, J14's frame rules, ADR-0065 request/result semantics or
  budgets, ADR-0064's manifest or bundle identity, or ADR-0062's lifecycle;
- a binary transport family, REST, or another transport stack;
- `J11`, server-side replay, Omega remote use (ADR-0067 amendment);
- remote Job recovery or retry, credential issuance and deployment runbooks.

## Acceptance evidence

`api_transport_server.py` defines only `J02_MARKET_DATA_READ_SCOPE` and a
16 MiB `J02_MAX_WIRE_MESSAGE_BYTES`; `GovernedResultBundleV1` carries
`deck_code_identity`, `configuration` and output checksums but no provider
identity; `AdmittedInputManifestV1` carries per-member identities and content
hashes; ADR-0065 Amendment 2 records the J13 request-size measurement.
`tests/test_j02_seam_carriage_contract_v1.py` guards the decisions above; it
is a design regression guard, not an implementation proof.

## Related

Governance issue #318; design gate #319; ADR-0068. Amends no ADR.

# ADR-0064 - Server/deck admitted handoff v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

ADR-0057 already assigns the server sole authority for canonical data, catalog,
accepted artifacts, recovery evidence, and data-local work. It assigns the
existing GPU-equipped deck replay sweeps, training, Omega integration, and
interactive analysis over a server-admitted input. No evidence identifies a
separate third consumer machine or a need to create one. The accepted topology
therefore has two surfaces: the server and that deck.

The DataGateway contract identifies datasets, natural partitions, request/result
fingerprints, manifests, content hashes, coverage, and code/definition evidence
without treating catalog UUIDs or filesystem locations as semantic identity. I02
persists Experiment `Run` and Artifact facts idempotently, but does not make a
deck result canonical until a server-owned boundary validates and registers it.
Neither current J02 nor a file copy is an admitted-input or result-import
protocol.

## Decision

### 1. The deck is the sole Wave 8 consumer compute surface

Wave 8's consumer machine is the existing ADR-0057 deck, logically a compute
consumer and result producer. Omega runs there only through its accepted public
platform boundary. No third surface is introduced. The deck may keep ephemeral
working data, caches, notebooks, and unaccepted outputs, but never a canonical
storage mount, catalog write path, checkpoint/recovery authority, or publication
authority.

The server remains the sole authority for canonical datasets, materialized
representations, accepted feature artifacts, and registered experiment results.
No handoff, local cache, or retry creates a replica canonical store.

### 2. K12 creates a sealed admitted-input manifest, not a path identity

K12 is a future server-owned capability. It resolves an explicitly requested
logical input against server authority, then persists one immutable
`AdmittedInputManifestV1` before any bytes or read-only reference are delivered.
Its deterministic `admission_id` is the canonical fingerprint of this manifest;
it excludes absolute paths, storage-root ids, catalog UUIDs, transport URLs,
delivery attempts, and deck-local locations.

The manifest includes, as applicable:

- the complete logical dataset and/or accepted artifact identities;
- ordered natural partition identities, schema identity/version/hash, manifest
  and content hashes, declared coverage, and result/request identities;
- representation/feature definition and implementation identities, immutable Git
  commit or release tag, and the requested operation/profile identity; and
- the manifest's own canonical digest and an explicit `SEALED` state.

K12 may deliver a server-selected read-only export bundle or an opaque
server-controlled read-only reference. That delivery locator is an operational
handle only, never an input identity. The deck must verify the delivered bytes
against the sealed manifest before use. It records `admission_id` and the exact
manifest digest with its work; it must not substitute a nearby dataset, a path,
or a new catalog lookup.

K12 separates immutable admission from delivery attempt state:

```text
ADMITTED -> SEALED -> DELIVERY_PENDING -> DELIVERED | DELIVERY_FAILED | EXPIRED
```

Only `SEALED` evidence is admissible to the deck. An interrupted or digest-
mismatched transfer becomes `DELIVERY_FAILED` and may be retried as a new
delivery attempt against the same sealed admission. It never changes the
manifest or produces a usable partial input. Expiry requires a new server
admission, not a revived locator.

### 3. K13 admits only an exact, immutable result evidence bundle

K13 is a future server-owned import/registration capability. A deck submits one
immutable result evidence bundle containing:

- `admission_id` and the exact sealed manifest digest it consumed;
- the deck's immutable code identity, declared runtime/environment,
  configuration, seeds where applicable, and any `RunIdentity` references;
- each output's role, schema/version, content checksum, and non-authoritative
  retrieval locator; and
- the declared metrics and bounded production/failure evidence needed by the
  owning Experiment/Artifact boundary.

Before any registration, K13 resolves the admission on the server and compares
every declared logical input, manifest/content digest, coverage, definition, and
code identity with its authoritative sealed evidence. Missing, expired,
unsealed, partial, malformed, or mismatching input evidence refuses the entire
bundle with no catalog or Experiment mutation. An output checksum mismatch, an
unknown role/schema, or an incompatible `RunIdentity` also refuses the bundle.

On success, the server verifies the declared output checksums, preserves the
bundle as attributable evidence, and registers a new governed Experiment/result
Artifact through its existing owner. It never overwrites a prior accepted
artifact or changes canonical input data. Re-submission of the exact same bundle
is idempotent and returns the same registration; a conflicting bundle for the
same identity/role fails closed. A new computation needs a distinct Result/Run
identity rather than replacing a previous result.

### 4. Security, Job, and transport boundaries remain separate

Every networked K12/K13 implementation uses the remote J02 security authority
from ADR-0063: the deck receives only the least-privilege read/reference or
submit-result scope selected by its implementation issue. This ADR defines no
wire shape, endpoint, credential, TLS configuration, or transport mechanism.
It also does not redefine J03 Job lifecycle: a Job may reference an admission
or result bundle, but K12/K13 admission and delivery evidence are not Job
attempt states.

## Consequences

- A later K12 implementation has a concrete server-owned output: a sealed,
  deterministic admitted-input manifest plus a non-semantic delivery handle.
- A later K13 implementation has an exact refusal and idempotency rule before
  it can register a deck-produced result.
- Physical relocation, a new export path, or a deck-local cache cannot change
  the identity of an admitted input or accepted result.
- Unaccepted deck output remains deck-local evidence, not a canonical artifact.

## Acceptance evidence

ADR-0057 directly selects the deck as the compute surface and requires a
server-selected read-only input carrying logical identity, manifests/content
digests, coverage, definition/implementation identity, and Git identity. The
DataGateway contract explicitly separates those stable fields from paths,
storage roots, and catalog UUIDs. I02's Experiment persistence keeps `RunIdentity`
and Artifact content/provenance distinct and refuses conflicting durable records.

`tests/test_server_deck_handoff_contract_v1.py` guards the surface choice,
sealed input, path exclusion, exact K13 refusal, and source contract reference.
It is a regression guard for this design authority, not K12/K13 implementation
or an end-to-end transfer proof.

## Out of scope

- K12/K13 implementation, storage migration, canonical mounts, or repository
  mirroring;
- network transport, API endpoint/schema, TLS, credentials, or authorization
  implementation;
- J03 runtime implementation, client workflow, or any new consumer surface;
- changes to accepted dataset, Experiment, Artifact, or Consumer API semantics.

## Related

Parent tracking: #281. Closes issue #284.

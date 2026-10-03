# ADR-0057 — Server/deck runtime topology and artifact handoff v1

**Status:** ACCEPTED
**Date:** 2026-10-03

## Context

The homelab server already owns the canonical market-data plane and the first
long-running ingest runtime.  The deck has the GPU and is the appropriate
compute surface for exploratory and batch research, but it must not become a
second authority for canonical datasets, catalog state, or operational
checkpoints.

Existing authority is deliberately narrower than a general remote-compute
platform.  `PRODUCT.md` requires data-local transformations close to canonical
data and excludes bulk data, caches, and large research artifacts from Git.
ADR-0041 constrains the server ingest identity and separates backup authority.
ADR-0050's J02 Consumer API is loopback-only by default; its non-loopback
override is explicitly unauthenticated and has no TLS.  No existing remote
artifact-export or experiment-result-import runtime is therefore assumed here.

## Decision

### 1. Two runtime surfaces, one canonical authority

| Surface | Owns and runs | Must not own |
| --- | --- | --- |
| **Server** | canonical trades; catalog and storage resolution; accepted materialized representations and feature artifacts; live-ingest checkpoints; recovery sets; bounded API/runtime evidence; server-side materialization where data locality is material | deck notebooks, deck-local caches, GPU training state, or an ungoverned experiment result as canonical evidence |
| **Deck** | replay sweeps; supervised/rl training; Omega integration; notebooks and interactive analysis; heavy experiment execution over an admitted input snapshot | canonical data/catalog mutation; canonical storage mounts; checkpoint or backup mutation; publication, retention, or recovery authority |

The server is authoritative for canonical data, accepted artifacts, and their
catalog evidence.  The deck is a compute consumer and result producer.  It is
not a replica server and is not a second publication path.

### 2. Source of truth and handoff rules

| Concern | Source of truth | Handoff rule |
| --- | --- | --- |
| Code, schemas, definitions, tests | Git history | Both surfaces use an immutable commit or release tag. Repository copying/mirroring outside Git history is prohibited. |
| Canonical datasets and accepted materializations | Server catalog plus server-resolved storage and manifests | The deck receives a server-selected, read-only export/sync or artifact reference. Logical identity, manifest/content digests, declared coverage, and code/definition identity travel with the selected input; a filesystem path alone is never an input identity. |
| Deck-local working data and caches | Deck | Ephemeral and non-authoritative. They are not published by copying into canonical storage. |
| Experiment results and large model/analysis outputs | Deck until accepted by a server-side import | Return an immutable evidence bundle, not manual catalog or filesystem writes. The server-side importer must validate the declared input identities and digests before registering a governed result/artifact. |

The required export/sync and import mechanisms are future implementation
capabilities.  Until they exist, an operator may perform only a controlled,
read-only, identity-and-digest-preserving transfer; neither manual edits to
canonical storage nor direct deck writes to the catalog are an acceptable
substitute.

### 3. Large artifact discipline

Git carries code and small fixtures only.  Large data, features, model
checkpoints, sweep traces, and notebook outputs remain outside Git.  A deck
job records the exact server input it consumed; at minimum this includes the
dataset/feature identity, relevant manifest and content digests, declared
coverage, definition/implementation identity, and the Git commit.

An accepted return bundle additionally records the deck runtime/environment,
configuration, seeds where applicable, metrics, and checksums/locations of
its output files.  Registration creates a new governed experiment/result
artifact.  It cannot revise the referenced canonical data or silently replace
an earlier result.

### 4. Network and operational boundary

The current J02 WebSocket server is not a deck-facing data plane.  It binds to
loopback by default and only permits a non-loopback bind after an explicit
operator override; ADR-0050 records that this override has neither
authentication nor TLS.  The deck must not use that override as a production
handoff channel.

Any networked server-to-deck access requires a future dedicated security
decision covering authentication, TLS, authorization, credential handling,
and an allowed network path.  That decision must preserve least privilege:
deck credentials may read only an admitted export or API capability and may
submit a result bundle, never write canonical partitions, checkpoints,
recovery sets, or arbitrary catalog state.

### 5. Operational runbook

1. Select and record an immutable Git commit/tag plus the server dataset or
   feature artifact identities and their evidence.
2. On the server, resolve an admitted read-only input export/reference; retain
   the manifest/digest evidence with the job record.
3. Transfer/sync only that selected input to the deck by the controlled path.
   Do not mount canonical storage read-write and do not duplicate repository
   history outside Git.
4. Run the deck workload with its resolved configuration and preserve output
   checksums, metrics, seeds, and runtime/environment evidence.
5. Return an immutable result evidence bundle through the future governed
   import path.  Until that path exists, results remain deck-local and cannot
   be represented as accepted server artifacts.

### 6. Sequencing

This ADR creates no implementation issues before it is accepted.  After
acceptance, the next bounded candidates are:

1. a server-owned read-only artifact export/reference and controlled-sync
   capability;
2. an authenticated/TLS deck-facing transport decision and implementation;
3. a server-owned experiment result/artifact import and registration seam;
4. deck adapters for replay/training/Omega that consume only the admitted
   handoff contract.

These are separate slices; this ADR does not authorize a distributed
scheduler, generic remote execution framework, or a storage migration.

## Consequences

- Canonical data and accepted artifacts retain one server-side authority while
  GPU-heavy research can run on the deck.
- Existing J02 remains a loopback/local-client transport, not an accidental
  unauthenticated homelab API.
- Future deck results become attributable only after server-side validation and
  registration; an ad-hoc file copy cannot claim canonical status.

## Out of scope

- a distributed scheduler or remote-execution framework;
- live/paper trading;
- wholesale canonical-storage migration to the deck;
- repository mirroring outside Git history;
- authentication/TLS or artifact-transfer implementation.

## Acceptance evidence

`tests/test_api_transport_server_v1.py` proves the current non-loopback bind
requires an explicit override.  The test is intentionally evidence of the
current limitation, not evidence that the override is safe for deck access;
ADR-0050 Amendment 1 documents its unauthenticated/no-TLS status.

## Related

Parent tracking: #232. Closes issue #238.

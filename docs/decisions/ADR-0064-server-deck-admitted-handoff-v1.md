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
Its deterministic `admission_id` identifies a non-recursive canonical payload;
it excludes absolute paths, storage-root ids, catalog UUIDs, transport URLs,
delivery attempts, and deck-local locations.

The manifest includes, as applicable:

- the complete logical dataset and/or accepted artifact identities;
- ordered natural partition identities, schema identity/version/hash, manifest
  and content hashes, declared coverage, and result/request identities;
- representation/feature definition and implementation identities, immutable Git
  commit or release tag, and the requested operation/profile identity; and
- an explicit `SEALED` state.

`canonical_payload_v1` is exactly the preceding enumerated immutable fields,
serialized as canonical JSON with a fixed v1 field schema. K12 computes
`manifest_digest = sha256(canonical_payload_v1)` and derives
`admission_id = admitted-input-v1:<manifest_digest>`. Neither
`manifest_digest` nor `admission_id` participates in the canonical payload;
they are derived, attributable fields carried beside it. This makes the digest
and admission identity deterministic without a self-referential hash.

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

## Amendment 1 (issue #357) — a delivery attempt has an identity

**Date:** 2026-10-15

Recorded at the maintainer's direction after the independent audit of the
`j02-admitted-input-v1` implementation (PR #356, issue #341); it takes effect
when the correcting PR of issue #357 is merged. The maintainer chose a new
family version on 2026-10-15; the rules for duplicate reports (refused) and
for the state read (the existing `manifest` operation) are as in the draft of
this text and stand until amended.

Section 2 says that an interrupted or mismatched delivery "may be retried as a
new delivery attempt against the same sealed admission", but gives an attempt
no identity: K12 kept one state per admission. The audit reproduced the
consequence on PostgreSQL. Deliver A, acknowledge A as failed, deliver B (same
manifest, same members), repeat A's acknowledgement: it was applied to B, which
ended as `DELIVERY_FAILED`, and B's own correct acknowledgement was then
refused. A late write from an attempt that had been replaced landed on the one
that replaced it, and neither the state nor the manifest digest could tell
them apart.

Section 2 is amended only for delivery attempts. The manifest fields,
`canonical_payload_v1`, `manifest_digest`, `admission_id` and their derivation,
the six states and their order, sections 1, 3 and 4, and the rule that expiry
needs a new admission are unchanged.

1. **A delivery attempt has a number.** An admission carries a
   `delivery_attempt` integer, `0` while it has never been delivered. Each entry
   into `DELIVERY_PENDING`, including the retry of a `DELIVERY_FAILED`
   admission, increments it by exactly one, in the same atomic step as the state
   change. It is never reused and never reset by a state change.
2. **It is delivery metadata, not identity.** It is not a state, and it is not
   part of `canonical_payload_v1`, `manifest_digest` or `admission_id`; section
   2's exclusion of "delivery attempts" from the admission identity stands. It
   changes nothing about what a sealed admission is.
3. **Terminal transitions are attributed.** `DELIVERED` and `DELIVERY_FAILED`
   that report an attempt are applied only when the admission is
   `DELIVERY_PENDING` and its current `delivery_attempt` equals the number the
   report presents. The comparison and the transition are one atomic step (the
   write itself carries the comparison). A report for another number, older or
   unknown, changes nothing and is refused, whatever outcome it asks for. A
   repeated report for an attempt that already ended is refused too; it is
   never applied to a later attempt.
4. **Expiry** applies to the current attempt of the admission. An expiry that
   names an attempt is applied only if it is the current one. `EXPIRED` is
   final, as in section 2.
5. **The number travels with the delivery.** A delivery returns its number to
   the deck; the deck records it with `admission_id` and the manifest digest and
   presents it when it reports the attempt's outcome. Reading the admission's
   state also returns the current number, so that a deck whose delivery response
   was lost can end that attempt with a failure report for the number it
   observed. A report made against a number that has since been replaced is
   refused by point 3.
6. **Recoverability of delivered bytes follows the admission.** Transfers that
   carry the members of a delivery are recoverable by the deck only while their
   admission exists and is not `EXPIRED`; the admission's state is read at the
   time of recovery, not inferred from the presence of retained frames, and a
   state that cannot be read is "not recoverable". After `DELIVERED` or
   `DELIVERY_FAILED` they remain recoverable until the transfer's own retention
   ends. An attempt and the publication of its members are one atomic step on
   the K12 side: the attempt is recorded only if its members were published,
   and no other transition can occur between the two. A delivery that cannot
   publish records no attempt and consumes no number, and it withdraws nothing
   that an earlier or a concurrent attempt retained.
7. **A pending attempt without recoverable transfers is a valid state.** The
   in-memory transfer store and PostgreSQL are not one atomic unit. If a
   response is lost, or the database commit fails after publication, or the
   process restarts, the attempt may be pending while its transfers are not
   recoverable. The server does not end the attempt itself. The deck ends it
   with a failure report for the number it observed and delivers again.

**Wire and schema consequences.** `admit`, `manifest` and `deliver` return
`delivery_attempt`; `acknowledge` requires it. A required field of an accepted
family is a new family version under ADR-0066 section 1, so the family becomes
`j02-admitted-input-v2`, and a server that composes it accepts no v1
acknowledgement on the same endpoint; `j02-admitted-input-v1` is withdrawn (it
was never composed by a host and never released). A mismatch is the Consumer API
`invalid_request` with the stable reason `delivery_attempt_mismatch`; no new
error code. `runtime.admitted_inputs` gains `delivery_attempt integer NOT NULL
DEFAULT 0`, a backward-compatible addition for existing rows.

**Accepted constraints.**

- The attempt number makes a stale report refusable; it does not make the
  PostgreSQL state and the server's in-memory transfer store one atomic unit.
  Point 6 therefore makes the *admission* the authority for recoverability, and
  point 7 states what a pending attempt without transfers means.
- The in-memory transfer store is not durable across a server restart; a
  restart ends in point 7, recovered by the deck.
- One server host is assumed, as in ADR-0068; nothing here addresses several.

# ADR-0067 - Omega remote client contract v1 (J15)

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

J15 makes Omega the first real remote consumer of the platform.  That is a
different concern from the existing phase-1 Omega validation bridge:
ADR-0055 permits a deliberately bounded, in-process dependency on the public
`quant_platform.validation` package surface.  It does not define remote
transport, remote authorization, or a generic Omega-to-platform dependency.

`PUBLIC_PYTHON_API.md` likewise documents an installed-package consumer
surface and explicitly says that it is not the Consumer API/transport
contract.  ADR-0050 instead freezes J02 as the WebSocket/JSON carrier of the
Consumer API, and places remote clients outside `src/quant_platform` with no
reason to import that package.  ADR-0063 adds the required non-loopback WSS
and mTLS policy; it defines only the `j02.market_data.read` scope.  ADR-0057
keeps canonical data and accepted artifacts server-authoritative.

The Omega checkout contains earlier local orchestration imports.  They neither
constitute a J15 remote adapter nor satisfy this ADR.  This gate chooses the
boundary that a later J15 implementation must use without changing those
existing local paths.

## Decision

### 1. J02 is the sole J15 remote boundary

Omega's J15 remote client communicates only through the J02 WebSocket/JSON
wire contract.  For its first capability, that means the exact
`j02-request-v1` request and `j02-response-v1` response envelopes defined by
ADR-0050 and retained by ADR-0066.  The client constructs a semantic Consumer
API request and renders the complete result or typed Consumer API error; it
does not reconstruct market data, resolve storage, or calculate quantitative
domain behavior.

The supported public Python API is not a remote-client abstraction and is not
a fallback.  A J15 remote operation must not select between a local package
call and J02, retry through the public Python API, or merge results from both.
ADR-0055 remains valid only for its specified in-process validation bridge;
its pinning rule does not authorize an alternate remote path.

### 2. Capability and authority are deliberately narrow

The first J15 operation is C03 market-data read, admitted only with
`j02.market_data.read`.  Strategy, Validation, Training, and any future
result-import capability need their own application request semantics and
remote scopes.  In particular, a J15 client must not infer a scope from an
existing local import, and Replay remains deferred under ADR-0065.

Omega is a consumer.  It may retain an operation-local presentation copy of a
returned response, but it never becomes an authority for canonical data,
catalog state, accepted artifacts, or artifact registration.  Its inputs and
outputs retain the server-issued request identity, result provenance, and
coverage; local paths, catalog UUIDs, and storage locators remain absent from
the J15 boundary.

### 3. Compatibility is a wire-family commitment, not a package pin

The J15 v1 client supports exactly this remote contract pair:

| Direction | Accepted version |
| --- | --- |
| request sent by Omega | `j02-request-v1` |
| response accepted by Omega | `j02-response-v1` |

The client sends the exact request version and accepts only the exact response
version for the selected operation.  An unsupported request version, an
unexpected response version, or a J14/new-family envelope in place of J02 v1
is a local typed `wire_incompatible` failure.  It is not a Consumer API error,
does not trigger schema probing or downgrade, and must not be retried with a
different wire family.  J14 remains additive: its framed family is unsupported
by J15 v1 until a later, explicit client-contract amendment admits it.

`quant_platform.__version__`, an installed-distribution pin, and ADR-0055's
`PLATFORM_PIN` identify a local package dependency only.  They are never used
to declare a remote server compatible with J15.  A later J15 release changes
its compatibility table only by explicitly adding a wire family/version and
its corresponding acceptance tests; changing the meaning of an accepted J02
pair requires a new wire version, not a client-side tolerance rule.

### 4. Security, transport, and Consumer failures stay distinct

TLS trust, endpoint-name, client-certificate, or handshake failures yield no
WebSocket application session.  An authenticated principal without the
required scope receives the ADR-0063 generic policy denial/close `1008` before
JSON decoding.  Omega reports these as a local typed `remote_security_failure`;
it must not convert them into `ConsumerApiError`, fall back to plaintext, or
reveal whether a denied capability exists.

After an authorized J02 session exists, a valid J02 `status: "error"` envelope
is the existing Consumer API error and remains lossless.  A malformed or
unexpected envelope is `wire_incompatible`, not a reinterpretation of a
domain error.  The current loopback listener is not remote-ready merely because
this contract is accepted: J15 remote use waits for the J09 implementation of
ADR-0063's WSS/mTLS enforcement.

### 5. Minimum acceptance proof for a later J15 implementation

The later adapter's acceptance proof uses a real WSS connection to a J09
configured server, a named Omega client credential, and the resolved server
`principal_id` with exactly `j02.market_data.read`.  It records bounded
transport evidence sufficient to associate the connection with the server's
trust-bundle and authorization-policy versions, without exposing keys,
certificates, DSNs, paths, or raw payloads.

That proof must:

1. send a real `j02-request-v1` Consumer market-data request and compare the
   decoded remote result's request identity, result/provenance/coverage, data,
   and typed error behavior with the same server-owned C03 execution;
2. show an intentionally mismatched response/family is rejected as
   `wire_incompatible`, with no downgrade or public-Python fallback;
3. show an untrusted, expired, revoked, or insufficiently scoped credential
   cannot create an application session or yield a Consumer API error; and
4. show that Omega records and renders returned identities rather than creating
   canonical data or accepted-artifact identities of its own.

This is a bounded remote-contract proof, not the later Golden E2E.  It neither
implements a remote adapter nor replaces ADR-0057's admitted artifact-handoff
and server-side registration proof.

## Consequences

- J15 has one implementation target: a remote J02 client outside
  `src/quant_platform`, not a package wrapper or hybrid client.
- The separately pinned ADR-0055 bridge stays local and cannot silently widen
  into a remote data or artifact path.
- J15 v1 can fail compatibility and security boundaries deterministically
  without changing frozen Consumer API error semantics.
- A remote implementation is blocked until J09 provides ADR-0063's required
  WSS/mTLS server surface; this ADR authorizes neither that server work nor an
  Omega adapter.

## Out of scope

- implementation of Omega's remote adapter, SDK, credential store, or UI;
- changes to the public Python API or ADR-0055's local validation bridge;
- new remote scopes or remote Strategy, Replay, Validation, Training, export,
  import, or artifact-registration operations;
- J14 framing support, pagination, live Consumer messages, or a Golden E2E;
- canonical storage, catalog, or accepted-artifact mutation from Omega.

## Related

Parent tracking: #281. Resolves issue #287.

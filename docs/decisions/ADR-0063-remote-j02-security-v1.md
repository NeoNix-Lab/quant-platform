# ADR-0063 - Remote J02 security v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

J02 is a frozen WebSocket/JSON carrier for the completed C03 market-data
Consumer API. Its current `allow_non_loopback` flag prevents accidental exposure
only. Direct inspection confirms that the server currently has neither TLS nor
authentication, and ADR-0050 Amendment 1 records an opted-in non-loopback bind
as unauthenticated by design.

ADR-0057 makes the server authoritative for canonical data and accepted
artifacts, and requires any networked server-to-deck access to have
authentication, TLS, authorization, credential handling, and least privilege.
It does not authorize a deck to mutate canonical partitions, checkpoints,
recovery sets, or arbitrary catalog state. G3 has not selected a physical
consumer placement or an export/import handoff, so this decision must not assume
one.

## Decision

### 1. Non-loopback is WSS with mutual TLS or it does not start

The existing loopback-only J02 deployment remains unchanged. A future J09
implementation may bind J02 to a non-loopback interface only when it has a
complete remote-security configuration. `allow_non_loopback` alone is never a
production-safe mode and must fail startup when that configuration is absent,
malformed, or incomplete.

The remote listener uses `wss` with TLS 1.3 or later. It presents a server
certificate chained to an operator-managed private trust anchor; the client
validates that chain and the server endpoint name/SAN. The server requires a
client certificate (`CERT_REQUIRED`) chained to its configured client trust
anchor. Certificate, hostname, chain, validity-window, or client-certificate
verification failures terminate before Consumer API message decoding. There is
no plaintext fallback, trust-on-first-use, insecure certificate override, or
token carried inside the JSON request envelope.

### 2. Credential identity and authorization

The authenticated credential is the mutually verified client public-key
certificate. The server maps its exact, versioned credential fingerprint to one
stable `principal_id` and an explicit immutable scope set. A certificate subject
string alone is not authorization. The mapping is server-owned operational
configuration, not a generic identity-provider, account, session, or bearer
token system.

For the only currently implemented remote-capable operation, the sole scope is
`j02.market_data.read`. It authorizes the existing C03 market-data query only;
it does not grant direct storage, catalog, database, filesystem, checkpoint,
backup, publication, Job, or administrative access. G3 may later define
admitted-export or governed-result-import scopes, but this ADR creates neither
such scope nor such endpoint.

Authorization is checked before `decode_transport_query` and before calling the
Application service. A verified certificate without the required scope is denied
with WebSocket policy close `1008`, not with a new or reinterpreted
`ConsumerApiError` JSON payload. After authorization, the frozen J02 request and
response schemas, C02/C03 semantics, result-size behavior, and ordinary Consumer
API error fidelity remain unchanged.

### 3. Rotation, revocation, and bounded security evidence

Every client and server certificate has a finite validity interval and a stable
credential fingerprint. Rotation adds the replacement credential with the same
or narrower scopes for a named, bounded overlap, then atomically removes or
denies the old fingerprint and terminates its remote sessions. Emergency
revocation uses the same atomic deny/remove action; a revoked credential may not
complete a new handshake or retain an authorized session. The implementation
must make the active trust bundle and authorization-policy version observable so
an operator can prove which policy decided a connection.

For each remote connection, the server records bounded security evidence:
timestamp, opaque session correlation id, TLS version/cipher, server trust-bundle
version, client credential fingerprint, resolved `principal_id`, authorization
policy version, requested scope, allow/deny decision, and terminal close reason.
It never records private keys, bearer secrets, certificate bodies, catalog DSNs,
or raw Consumer API payloads as security evidence.

### 4. Ownership and failure boundary

The J02 transport composition in `quant_platform.application` owns TLS/mTLS
configuration validation, principal-to-scope enforcement, and the bounded audit
event. It performs those checks before it delegates a validated request to the
already-owned C03 application service. C03 keeps ownership of request semantics,
resolution, and typed business errors; it never interprets certificates or ACLs.

TLS or client-certificate failure yields no WebSocket application session.
Unmapped, expired, revoked, or insufficiently scoped credentials receive a
generic policy denial and only bounded server-side evidence. A remote client
cannot downgrade to plaintext, retry with a different wire schema, or infer
whether a denied principal or capability exists. Loopback's current local
behavior is not redefined by this remote gate.

## Consequences

- A later J09 implementation has one deployable security shape: TLS 1.3 mTLS,
  exact certificate-fingerprint principal mapping, and per-operation scopes.
- The current non-loopback override remains unsafe until that implementation
  exists; acceptance of this ADR alone does not expose or secure a listener.
- Remote access is initially read-only and cannot become a canonical-write path
  through broad credential scope.
- Existing J02 JSON semantics remain a lossless carrier of C03 outcomes.

## Acceptance evidence

`ApiTransportServerConfig` currently rejects non-loopback binds unless the
operator explicitly sets `allow_non_loopback`, and
`tests/test_api_transport_server_v1.py` proves that guard. The same server
currently calls the C03 executor after WebSocket receipt with no TLS or identity
material, confirming that an implementation must enforce this ADR before message
decode. ADR-0050 Amendment 1 establishes the current plaintext limitation and
ADR-0057 section 4 supplies the server-authority and least-privilege constraints.

`tests/test_remote_j02_security_contract_v1.py` guards the accepted mTLS,
least-privilege, failure-boundary, and source/contract references. It is a
regression guard for the security decision, not evidence that remote J09 is
implemented.

## Out of scope

- TLS/mTLS code, certificate issuance, secret storage, deployment, or a
  non-loopback listener;
- changes to WebSocket/JSON serialization, C02/C03 business semantics, or J02
  result-size limits;
- generic IAM, OAuth/OIDC, bearer tokens, accounts, or a credential service;
- consumer placement, admitted-input export, result import, Job API, or client
  workflow decisions belonging to G3/G4/G6.

## Related

Parent tracking: #281. Closes issue #283.

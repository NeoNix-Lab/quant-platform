# ADR-0041 — Live-ingest runtime identity v1

**Status:** ACCEPTED
**Date:** 2026-09-21

## Context

The first live-ingest vertical eventually runs as a long-lived process on the real server. K02 is activated only when that deployment creates or changes a production service identity, filesystem/database authorization boundary or credential disposition.

The selected Bybit public-trade source requires no provider credential. Therefore the first vertical must not introduce API keys, secret-management infrastructure or elevated privileges merely by symmetry with future private/live-trading capabilities.

## Decision

### 1. Service identity

When the real deployment creates a dedicated runtime identity, A11/K10 run under one non-root, least-privileged live-ingest service identity.

The identity:

- is not `root`;
- does not require interactive login;
- has no `sudo`/general administrative authority;
- is scoped only to the resources required by the live-ingest vertical.

Exact OS username/service-manager syntax is deployment-local and not semantic authority.

### 2. Provider credentials

Bybit public trades v1 uses the public WebSocket/REST surfaces and therefore carries **no Bybit API key or secret**.

If a future provider/private endpoint requires credentials, that is a new concrete credential boundary and must be governed separately; this ADR does not pre-authorize it.

### 3. Filesystem authority

The live-ingest service may receive only the minimum filesystem permissions required to:

- read its resolved runtime configuration;
- write the bounded ingest/staging state required by A11;
- write the canonical publication paths that the existing publication architecture assigns to the producer;
- write its own checkpoint state once K10 is implemented;
- write its own operational logs/health evidence where the existing runtime requires it.

It must not receive broad write authority over unrelated datasets, repository state, backup authority or administrative filesystem locations.

### 4. Backup separation

The live-ingest writer must not be the sole authority capable of destroying or rewriting the independent K08 recovery copy.

A backup process/identity may read the protected primary state required to construct a recovery set and write the backup destination, but A11's normal service identity must not gain general delete/rewrite authority over that backup merely for convenience.

The exact mechanism may be OS ownership/ACL, mount policy, separate process identity or an equivalent deployment primitive; the observable separation is authoritative, not the technology.

### 5. Database/catalog authority

If the deployed catalog/publication path uses a database role, the live-ingest service receives the minimum role required by the existing publication contract and no superuser/admin role.

A11/K10 may not widen catalog/database permissions beyond the accepted publication/checkpoint responsibilities.

### 6. Configuration boundary

Existing C05 authority remains unchanged: CLI/environment acquisition belongs at the executable boundary; Application/runtime owners consume resolved typed configuration. This ADR does not introduce a new secret/config framework.

### 7. Conditional activation

If the first real-server proof can run entirely under an already-governed service identity/ACL boundary without creating or changing production authorization, no additional K02 implementation artifact is required merely to restate this ADR.

If deployment requires new identity, ACL, role or credential mutation, that mutation must conform to this ADR before the service is treated as production-governed.

## Excluded

This ADR does not define or authorize:

- provider trading/private credentials;
- a generic secret manager/IAM system;
- SSH/user-management policy for the whole server;
- J08 live-trading authorization;
- K09 deletion authority;
- broad backup administration by the ingest service.

## Consequences

- K02 semantics needed by the first live-ingest deployment are frozen.
- Public A11 v1 has no provider secret to store.
- The first deployment can remain intentionally small: one least-privileged service identity, existing configuration/publication seams and explicit backup-authority separation.

# Deck/server PKI operations

Status: operational procedure for ADR-0072 section 4 (DG-K2 atom `S04`), within ADR-0063 section 2. It
describes tooling that exists in `infra/pki/`; it does not describe a service. There is **no credential
service, no IAM, no OAuth/OIDC and no bearer token**: the deck and the API authenticate with mutual TLS 1.3, and
the API authorizes by the **exact SHA-256 fingerprint** of the client certificate, looked up in a principals file.

## What exists

| Piece | Where | Lifetime |
| --- | --- | --- |
| Offline CA (EC P-256) | `ca-init.sh` -> `<pki-dir>/ca/ca.{key,crt}` | 3650 days |
| API server certificate | `issue-server.sh` -> `<pki-dir>/servers/<name>/<label>/server.{key,crt}` | 397 days |
| Client certificate, one per principal | `issue-client.sh` -> `<pki-dir>/clients/<principal>/<label>/client.{key,crt}` | 90 days |
| Fingerprint of a certificate | `fingerprint.sh <certificate.pem>` | - |
| Fingerprint-to-principal mapping | the principals file (format below) | until edited |

`<pki-dir>` is a directory you create **outside the repository** (the scripts refuse a Git work tree). The scripts
need only `bash` and `openssl`. Keys are EC P-256; the listener and the deck client speak TLS 1.3 only.

The scripts check the *physical* path (symbolic links resolved) both for a `.git` entry in any ancestor and with Git
itself, and refuse when Git cannot answer; they refuse a symbolic link anywhere under `<pki-dir>` (`clients`, `servers`,
the credential's own directory), and creating a credential directory is atomic, so two concurrent issues with the same
label cannot both write: one wins, the other is refused. `PKI_GIT` names the git executable if it is not on `PATH`.

## Where things live

- **Private keys** are files, mode `0600`, owned by the service user that reads them (`server.key` by the API user,
  `client.key` by whoever runs the deck). They are read at startup. They never go into Git, an environment
  variable, a unit file or a log. The scripts create them with `umask 077`.
- **The CA private key** (`ca.key`) stays **offline**. Issue what you need, then move `<pki-dir>/ca/ca.key` to
  offline storage; it is needed only to issue or rotate a certificate. Only `ca.crt` is published.
- **The principals file** lives with the other operational configuration (for example
  `/etc/quant-platform/principals.json`), not in Git. The repository carries
  `infra/pki/principals.example.json`, which has **no fingerprints** and is not loadable as is.
- **The trust anchors** are `ca.crt`: the API's `--client-trust-anchor`, and the deck's server trust anchor.

## The principals file

```json
{"version": 1, "principals": [
  {"principal_id": "deck-research", "fingerprint": "sha256:<hex>", "scopes": ["j02.strategy.compose"]}
]}
```

Exactly these three fields; a fingerprint is `sha256:` and 64 lowercase hex digits (the `fingerprint=` line of
`issue-client.sh`); scopes are the registered J02 scopes. A `principal_id` may appear under two fingerprints (the
rotation overlap). `authorization_policy_version` is the SHA-256 of this file's bytes and appears in the API's startup
output and in every security evidence event.

## First setup

```bash
mkdir -p /srv/quant-platform-pki && chmod 700 /srv/quant-platform-pki      # outside the repository
infra/pki/ca-init.sh /srv/quant-platform-pki
infra/pki/issue-server.sh /srv/quant-platform-pki api "DNS:api.example.org,IP:192.0.2.10"
infra/pki/issue-client.sh /srv/quant-platform-pki deck-research            # prints fingerprint=sha256:...
```

Then: install `server.crt`, `server.key` and `ca.crt` on the API host (`--tls-certificate`, `--tls-private-key`,
`--client-trust-anchor`); write the principals file with the printed fingerprint and the scopes the principal needs;
give `client.crt`, `client.key` and `ca.crt` to the deck; move `ca.key` offline; restart the API unit.

## Rotation and revocation (restart-based and atomic)

The API reads its configuration **once, at startup**. Every change below is "edit the file, restart the API unit",
which terminates every session (ADR-0063 section 3) and cannot leave a half-applied policy. There is no hot reload.

**Rotate a client certificate** (before its 90 days end):
1. `issue-client.sh <pki-dir> <principal>` (a new label, normally the new date) and note the new fingerprint.
2. Add the new fingerprint to the principals file with the **same or narrower scopes**; restart the API. Both
   certificates now work: this is the **overlap, at most 14 days**.
3. Give the new `client.crt`/`client.key` to the deck and confirm it connects.
4. Remove the old fingerprint from the file; restart the API. The old certificate is now denied.

**Emergency revocation**: remove the fingerprint from the principals file and restart the API. Nothing else is
needed, and nothing short of that is enough: there is **no CRL and no OCSP**. Authorization is by exact fingerprint,
so a certificate the CA signed whose fingerprint is not in the file is denied (its TLS handshake succeeds; the
connection is closed with a policy denial and an evidence row). Revoking a *compromised CA* is a CA rotation.

**Rotate the server certificate** (before 397 days): `issue-server.sh` with a new label, replace `server.crt` and
`server.key` on the API host, restart. The deck needs no change while the CA is the same.

**Rotate the CA** (before 3650 days, or if it is compromised): create a new CA in a new `<pki-dir>`; for the transition
the trust anchor files hold **both** CA certificates (concatenated PEM), so the API trusts clients of either and the
deck trusts servers of either; re-issue the server and every client certificate under the new CA; then drop the old
CA from both anchor files. Each change of the API's anchor file changes `server_trust_bundle_version`.

## Checks

- A generated certificate's fingerprint: `infra/pki/fingerprint.sh client.crt` (equals the `fingerprint=` line).
- A chain: `openssl verify -CAfile ca.crt client.crt`.
- Expiry: `openssl x509 -in client.crt -noout -enddate`; keep a calendar for the 90-day client certificates.
- Which policy decided a connection: the `authorization_policy_version` and `server_trust_bundle_version` in the
  security evidence event.

## What this does not do

No automatic renewal, no revocation list, no hot reload, no key escrow, no secret distribution, no hardware token.
Getting `client.key` to the deck securely is an operator action outside these scripts.

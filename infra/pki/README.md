# infra/pki

Shell tooling around `openssl` for the deck/server mutual TLS (ADR-0072 section 4, ADR-0063). The procedure,
rotation and revocation are in [docs/engineering/DECK_PKI_OPERATIONS.md](../../docs/engineering/DECK_PKI_OPERATIONS.md).

| Script | Purpose |
| --- | --- |
| `ca-init.sh <pki-dir>` | offline CA, EC P-256, 3650 days |
| `issue-server.sh <pki-dir> <name> <SANs>` | API server certificate, 397 days |
| `issue-client.sh <pki-dir> <principal>` | client certificate, 90 days; prints `fingerprint=sha256:...` |
| `fingerprint.sh <certificate.pem>` | the value the principals file holds |
| `principals.example.json` | the principals file format, **without** fingerprints |

`<pki-dir>` is outside the repository (the scripts refuse a Git work tree). Private keys are files with mode `0600`
and never enter Git, an environment variable or a unit file; the CA key stays offline. There is no credential service.

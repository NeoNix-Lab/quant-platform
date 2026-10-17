# Server/deck handoff runbook

Status: operational procedure for ADR-0072 section 6 (DG-K2 atom `S06`). It replaces, as the working procedure, the
five-step outline in [ADR-0057](../decisions/ADR-0057-server-deck-runtime-topology-v1.md) section 5, which predates the
implemented protocol (that section is not edited). It describes tooling that exists in this repository
(`tools/api_server.py`, `tools/job_worker.py`, `infra/systemd/`, `infra/promote-main.sh`, `infra/pki/`, `clients/deck/`
and `quant_platform.application.deck_result_bundle`); it does not describe a service that does not exist.

There is **no credential service, no broker and no remote-execution framework**. The server runs two units, the API and
the worker. The deck is a client and a workload that the operator runs; it has no database login and never opens canonical
storage. Whether a deck can use a server is decided by [the compatibility rule](SERVER_DECK_COMPATIBILITY.md).

## The units

| Unit | Host | Database login | Listens |
| --- | --- | --- | --- |
| `quant-platform-api.service` | `tools/api_server.py` | `platform_api` | WSS with mutual TLS 1.3 (ADR-0063) |
| `quant-platform-worker.service` | `tools/job_worker.py` | `platform_worker` | nothing (a single dispatch loop) |

Both are modeled on `quant-platform-live-ingest.service` and carry its hardening (`NoNewPrivileges`,
`ProtectSystem=strict`, `UMask=0027`, no capabilities, `PrivateTmp`, `RestrictNamespaces`, ...). Each has a
non-secret environment example next to it, and each runs as its own system user (`qp-api`, `qp-worker`). Neither unit
nor example holds a password or a key: the database password is read by libpq from a passfile named in the DSN, and the
TLS private key is a file.

Ordering and exit status:

- The worker is `After=` the API and PostgreSQL, and `PartOf=` the API, so stopping or restarting the API stops or
  restarts the worker.
- The API records its deployment commit in PostgreSQL **before it listens** (ADR-0068 section 8). The worker compares its
  own commit with that record and **exits `3`** when they differ or when none is recorded. `Restart=on-failure` restarts
  it on `3`, and that does not mask the refusal: every attempt is refused and logged, and the pair converges as soon as
  the API of the same commit has recorded it, which is what a restart of the pair always produces.
- Worker exit `2`: another worker holds the lock. Exit `4`: the worker lost its lock and dispatched nothing further. Exit
  `1`: any other fatal error. API exit `2`: no runtime DSN; exit `4`: the composition was refused (a missing or invalid
  certificate, principals file, family or DSN). The API refuses before it binds or records anything, and a configuration
  refusal is repeated every restart until the operator fixes it: read the journal, not the restart counter.
- `SIGTERM` lets the in-flight handler finish and record its outcome. `TimeoutStopSec=90s` bounds that; if systemd has to
  `SIGKILL`, the Job is left for the next worker's restart recovery (ADR-0068 section 6), never re-run by a second worker.

## Install (once per host)

Run on the server as the operator account. `/opt/market-platform` is the deployed checkout.

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin qp-api
sudo useradd --system --no-create-home --shell /usr/sbin/nologin qp-worker

# Both units read the commit with `git rev-parse HEAD` in a checkout the operator owns.
sudo git config --system --add safe.directory /opt/market-platform

sudo install -d -m 0750 -o root -g root /etc/quant-platform
sudo install -m 0640 -o root -g qp-api    infra/systemd/quant-platform-api.env.example    /etc/quant-platform/api-server.env
sudo install -m 0640 -o root -g qp-worker infra/systemd/quant-platform-worker.env.example /etc/quant-platform/worker.env
sudo install -m 0644 -o root -g root infra/systemd/quant-platform-api.service    /etc/systemd/system/
sudo install -m 0644 -o root -g root infra/systemd/quant-platform-worker.service /etc/systemd/system/
```

Then, deployment-local and never in Git:

- Database: create the logins with `db/init/005_runtime_roles.sh` (`platform_api`, `platform_worker`); put each password
  in its passfile (`/etc/quant-platform/api.pgpass`, `worker.pgpass`), mode `0600`, owned by that unit's user. Correct only the
  host, port and database name in the two DSNs. Do not add `PGPASSWORD` or a password to a DSN.
- API: install the server certificate, its key, the CA certificate and the principals file as described in
  [Deck/server PKI operations](DECK_PKI_OPERATIONS.md) (`server.key` mode `0600`, owned by `qp-api`), and set the paths in
  `api-server.env`. A non-loopback bind needs **both** a non-loopback `QP_API_HOST` and `QP_API_ALLOW_NON_LOOPBACK=true`,
  and then refuses to start without complete TLS/mTLS material. `QP_API_FAMILIES` names the families the host serves; the
  deck handoff needs `admitted-input` and `result-import`. Empty (the default) leaves the J02 v1 market-data server.
- Do not set `QP_GIT_COMMIT` on a host with a Git checkout: it would decouple the recorded commit from the code that runs.
  It exists for a deployment without `.git`, which this runbook does not cover, and which the deck cannot use (its
  workload module proves a clean tree through Git).

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/quant-platform-api.service /etc/systemd/system/quant-platform-worker.service
sudo systemctl enable --now quant-platform-api.service
sudo systemctl enable --now quant-platform-worker.service
```

Expected evidence:

```bash
sudo journalctl -u quant-platform-api.service -n 40 --no-pager      # J02 API transport server v1, deployment_commit=<sha>
sudo journalctl -u quant-platform-worker.service -n 40 --no-pager   # commit=<sha>, J03_WORKER: READY
```

The two `<sha>` values are the same, and equal `git -C /opt/market-platform rev-parse HEAD`. The API output also shows
`authorization_policy_version` and `server_trust_bundle_version`, and lists a family whose scope no principal holds
(allowed, but unreachable).

## Promotion

Server code moves through Git history only: `infra/promote-main.sh` is clean-tree and fast-forward-only, validates the
candidate in an isolated worktree before the operational checkout moves, and does not restart anything. Do not stash,
reset, clean, mirror the repository by file copy, or automate any of this.

```bash
cd /opt/market-platform
infra/promote-main.sh --inspect-only        # read-only: branch, sha, clean or dirty
infra/promote-main.sh                       # fetch, validate, fast-forward to the validated origin/main
git rev-parse HEAD                          # the new commit

sudo systemctl restart quant-platform-api.service quant-platform-worker.service
sudo journalctl -u quant-platform-api.service -u quant-platform-worker.service -n 60 --no-pager
```

**The API and the worker restart together, always.** The restart ends every client session (ADR-0063 section 3) and
the in-memory transfer store of the API (see lost response below). A worker that starts before the API has recorded the
new commit exits `3` and is started again after `RestartSec`; that is expected, and it must be `READY` on the new commit
before you finish. The live-ingest unit runs the same commit (ADR-0068 section 8) and follows its own
[runbook](LIVE_INGEST_SERVER_DEPLOYMENT.md).

**The deck checks out the same commit before a run.** A deck result is accepted only for the server's commit
(ADR-0072 section 5). After a promotion, an admission made earlier names the old commit: make a new `admit`.

## A run, step by step

On the deck, with the client certificate the operator issued (`infra/pki/issue-client.sh`) and the platform's CA
certificate as the server trust anchor.

1. **Select a commit.** The commit is the server's. Read it from the API's startup output (`deployment_commit=`) or from
   the `git_identity` of the manifest returned by `admit`. On the deck: `git fetch origin && git checkout --detach <sha>`,
   with a clean work tree. The workload module refuses a dirty tree or any other commit before it does any work.
2. **`admit`.** `DeckHandoffClient.from_credentials(url, client_certificate_path=..., client_private_key_path=...,
   server_trust_anchor_path=...)`, then `admit(operation_identity=..., profile_identity=..., dataset=... or
   artifact_identities=...)`. The reply is a sealed admission (`admission_id`, `manifest_digest`, `manifest`,
   `delivery_attempt`). Keep `admission_id` and the digest with the run record.
3. **`deliver` and verify.** `fetch_inputs(admission)` delivers, verifies every member against the manifest's content
   hashes and identities, and only then acknowledges `delivered` for the `delivery_attempt` it received. Bytes are handed
   over only after that. A failed verification acknowledges `failed` for that attempt and raises.
4. **Run.** The workload runs the platform library in process over the delivered bytes. Nothing is written to canonical
   storage.
5. **Build the result.** `quant_platform.application.deck_result_bundle`: `inspect_workload_tree(root)` and
   `build_deck_result(...)` return the upload bytes of each output and the `submit` document. It refuses a dirty tree,
   a commit other than the manifest's `git_identity`, a role outside the registry, and a value the wire cannot carry
   (a float is one).
6. **`upload_output`.** One call per output, with the bytes the module returned.
7. **`submit`.** The result is registered or refused by the server's governed import (K13). The reply is a registration
   (`bundle_digest`, the run, the outputs). **Record it** with the admission id, the manifest digest and the commit: it is
   the only evidence that the result is accepted. A result that was not submitted, or was refused, stays deck-local.

## Failure procedures (ADR-0064 Amendment 1)

A delivery attempt has a number. A report about an attempt is applied only while the admission is `DELIVERY_PENDING` and its
current attempt is that number; any other report changes nothing and is refused.

**Lost response, or a restart between `deliver` and `acknowledge`.** The admission can be `DELIVERY_PENDING` while the
members of that attempt are no longer retrievable (the transfer store is in memory; a restart of the API loses it). The server
does not end the attempt itself. The deck does:

1. `end_pending_attempt(admission_id)`. It reads the admission and, if it is `DELIVERY_PENDING`, reports **the attempt it
   just read** as failed (`delivery_interrupted`); any other state is returned as read.
2. `fetch_inputs(admission)` again. It is a new attempt with the next number against the same sealed admission.

If the admission is already `DELIVERED`, that attempt completed. If it is `EXPIRED`, that is final: `admit` anew.

**Stale acknowledgement.** An acknowledgement for a number that is not the current attempt is refused with a Consumer API
`invalid_request` and the stable reason `delivery_attempt_mismatch`. It changed nothing, and **it must not be repeated**:
neither retry the same report nor try to apply it to the newer attempt. Read the admission (`manifest(admission_id)`)
and act on the state it returns: `DELIVERY_PENDING` with a number higher than yours means another delivery replaced your
attempt (end that one only if it is also yours and lost), `DELIVERED` means the data was delivered, `DELIVERY_FAILED` means
deliver again.

**A refused `submit`.** The result is not registered and stays deck-local. Do not edit the document by hand. If the server
was promoted since the run, the commit no longer matches: check out the server's commit and run again from `admit`.

**A deck and a server that do not speak the same families.** `WireIncompatible` from the client is a local typed failure,
not a Consumer API error; it is never retried with another wire family. See the [compatibility rule](SERVER_DECK_COMPATIBILITY.md).

## What this does not do

No automatic promotion, restart or rollback; no rotation of credentials (see [PKI operations](DECK_PKI_OPERATIONS.md));
no second worker; no deck on a different commit (deferred by ADR-0072 section 5); no proof on the real host (that is
ADR-0072 section 7, `S07`).

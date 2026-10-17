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

- The worker is `After=` the API and PostgreSQL. That orders the start only: the units are not coupled, so stopping or
  restarting one does not stop or restart the other, and the promotion below restarts them explicitly, together.
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
# The units run /opt/market-platform/.venv/bin/python. The environment holds the runtime dependencies only (pyproject.toml
# is the authority; a test keeps this list in step with it). Do NOT `pip install -e .`: it writes src/quant_platform.egg-info
# into the checkout, and the promotion needs a clean work tree.
python3 -m venv /opt/market-platform/.venv
/opt/market-platform/.venv/bin/pip install 'pyarrow>=15' 'psycopg[binary]>=3.1' 'rfc8785>=0.1.4' 'websockets>=12'

sudo useradd --system --no-create-home --shell /usr/sbin/nologin qp-api
sudo useradd --system --no-create-home --shell /usr/sbin/nologin qp-worker

# The API reads the canonical partitions it delivers (admitted-input opens them by path). infra/provision-marketdata.sh gives
# canonical/ and every subdirectory to mkt-transform:marketdata, mode 2750: the group reads and traverses, others have nothing,
# and its read-only actors are members of that group. The API is one of them. Run the provisioning first (it creates the group);
# the group gives no write, and the unit has no ReadWritePaths=. Do not make the data world-readable.
# The group reads the whole marketdata tree (raw/, imports/, staging/live, features/, ...), not only canonical/. The maintainer
# accepted this for v1 (the deck will also need the features); narrowing the API to ACLs on canonical/ is possible later.
sudo usermod -aG marketdata qp-api

# Both units read the commit with `git rev-parse HEAD` in a checkout the operator owns.
sudo git config --system --add safe.directory /opt/market-platform

# The directory is shared by the API, the worker and (when installed) live-ingest, which run as different users, so each of
# them needs the search bit on it. 0751 gives that and no listing; the files below are protected by their own owner and mode.
sudo install -d -m 0751 -o root -g root /etc/quant-platform
sudo install -d -m 0750 -o root -g qp-api /etc/quant-platform/tls
sudo install -m 0640 -o root -g qp-api    infra/systemd/quant-platform-api.env.example    /etc/quant-platform/api-server.env
sudo install -m 0640 -o root -g qp-worker infra/systemd/quant-platform-worker.env.example /etc/quant-platform/worker.env
sudo install -m 0644 -o root -g root infra/systemd/quant-platform-api.service    /etc/systemd/system/
sudo install -m 0644 -o root -g root infra/systemd/quant-platform-worker.service /etc/systemd/system/
```

Then, deployment-local and never in Git:

- Database: create the logins with `db/init/005_runtime_roles.sh` (`platform_api`, `platform_worker`); put each password
  in its passfile (`/etc/quant-platform/api.pgpass`, `worker.pgpass`), mode `0600`, owned by that unit's user (`qp-api`,
  `qp-worker`). Correct only the
  host, port and database name in the two DSNs. Do not add `PGPASSWORD` or a password to a DSN.
- API: install the server certificate, its key, the CA certificate and the principals file as described in
  [Deck/server PKI operations](DECK_PKI_OPERATIONS.md), and set the paths in `api-server.env`: `server.crt`, `server.key`
  and `ca.crt` in `/etc/quant-platform/tls/` (`server.key` mode `0600` owned by `qp-api`, the others `0644`), and
  `principals.json` in `/etc/quant-platform/` (mode `0640`, `root:qp-api`). A non-loopback bind needs **both** a non-loopback `QP_API_HOST` and `QP_API_ALLOW_NON_LOOPBACK=true`,
  and then refuses to start without complete TLS/mTLS material. `QP_API_FAMILIES` names the families the host serves; the
  deck handoff needs `admitted-input` and `result-import`. Empty (the default) leaves the J02 v1 market-data server.
- Do not set `QP_GIT_COMMIT` on a host with a Git checkout: it would decouple the recorded commit from the code that runs.
  It exists for a deployment without `.git`, which this runbook does not cover, and which the deck cannot use (its
  workload module proves a clean tree through Git).

**Check that every unit can read what its configuration opens, before the first start** (the live-ingest runbook creates
the same directory with the same owner and mode, so running it before or after this one leaves the result unchanged;
repeat the check after any other step that touches `/etc/quant-platform`):

```bash
stat -c '%a %U:%G %n' /etc/quant-platform /etc/quant-platform/tls
for f in /etc/quant-platform/api-server.env /etc/quant-platform/api.pgpass /etc/quant-platform/principals.json \
         /etc/quant-platform/tls/server.crt /etc/quant-platform/tls/server.key /etc/quant-platform/tls/ca.crt; do
  sudo -u qp-api test -r "$f" || echo "qp-api cannot read $f"
done
sudo -u qp-worker test -r /etc/quant-platform/worker.pgpass || echo "qp-worker cannot read worker.pgpass"
# Segregation: neither user reads the other's secret.
sudo -u qp-worker test -r /etc/quant-platform/tls/server.key && echo "qp-worker can read server.key (wrong)"
sudo -u qp-worker test -r /etc/quant-platform/api.pgpass && echo "qp-worker can read api.pgpass (wrong)"
sudo -u qp-api test -r /etc/quant-platform/worker.pgpass && echo "qp-api can read worker.pgpass (wrong)"

# The canonical roots the catalog serves (add the cold-tier dataset roots if the host serves them): the API reads, never writes,
# and the worker has no access.
for root in /srv/marketdata/canonical; do
  sudo -u qp-api test -r "$root" -a -x "$root" || echo "qp-api cannot traverse $root"
  sudo -u qp-api test -w "$root" && echo "qp-api can write $root (wrong)"
  sudo -u qp-worker test -x "$root" && echo "qp-worker can traverse $root (wrong)"
  sample="$(sudo find "$root" -type f -print -quit)"
  if [ -n "$sample" ]; then
    sudo -u qp-api head -c 1 "$sample" >/dev/null || echo "qp-api cannot read $sample"
    sudo -u qp-api test -w "$sample" && echo "qp-api can write $sample (wrong)"
  fi
done
```

The environment files are read by systemd as root; the checks above are for what the processes themselves open. No line
may be printed. Membership of `marketdata` is read when the unit starts, so restart `quant-platform-api.service` after the
`usermod`. The group also lets `qp-api` read `live-ingest-server.env` (`root:marketdata 0640`), which is non-secret by
design (no password: the catalog password is in the `mkt-transform` passfile). If live-ingest is installed on the host, `sudo -u mkt-transform test -r /etc/quant-platform/live-ingest-server.env`
must still succeed as well (the mode `0751` of the directory is what lets all three users reach their own files).

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

## What a delivery costs the API host

A delivery holds the delivered files in the API's memory (read, encoded for the wire, framed) and is limited by the bounds of
[ADR-0072 Amendment 1](../decisions/ADR-0072-server-deck-deployment-composition-v1.md#amendment-1-issue-431--delivery-bounds-raised-to-the-measured-history):
512 MiB per file, 1 GiB and 16 files per admission, 1.5 GiB of frames retained at once, 600 s to fetch them. Measured peaks
of the API process: about 2.6 GiB for one 512 MiB file and about 4 GiB for 1 GiB in several files, plus about 53 MiB when idle;
the deck needs about the same on its side. Plan the host for one such delivery at a time: the retained-bytes cap refuses a
second delivery that does not fit, but concurrent deliveries were not measured. The unit sets no memory limit.

## Promotion

Server code moves through Git history only: `infra/promote-main.sh` is clean-tree and fast-forward-only, validates the
candidate in an isolated worktree before the operational checkout moves, and does not restart anything. Do not stash,
reset, clean, mirror the repository by file copy, or automate any of this.

```bash
cd /opt/market-platform
infra/promote-main.sh --inspect-only        # read-only: branch, sha, clean or dirty
infra/promote-main.sh                       # fetch, validate, fast-forward to the validated origin/main
DEPLOYED_SHA="$(git rev-parse HEAD)"        # the new commit
echo "DEPLOYED_SHA=$DEPLOYED_SHA"

sudo systemctl restart quant-platform-api.service quant-platform-worker.service
sudo journalctl -u quant-platform-api.service -u quant-platform-worker.service -n 60 --no-pager
```

**Every unit of the server runs the same commit** (ADR-0068 section 8: ingest, API and worker of one server deployment),
so a promotion is not finished until all of them do. If live-ingest is installed on this host, bring it to the new commit
in the same promotion: declare the new commit, then restart it, following its
[runbook](LIVE_INGEST_SERVER_DEPLOYMENT.md) for the stop, restart and evidence (it reconciles from its durable checkpoint):

```bash
sudo sed -i "s/^QP_LIVE_INGEST_CODE_REF=.*/QP_LIVE_INGEST_CODE_REF=${DEPLOYED_SHA}/" /etc/quant-platform/live-ingest-server.env
sudo systemctl restart quant-platform-live-ingest.service
sudo journalctl -u quant-platform-live-ingest.service -n 40 --no-pager
```

Do not leave live-ingest running the old process after the checkout has moved, and do not restart it without updating
`QP_LIVE_INGEST_CODE_REF` first: either leaves the declared provenance different from the code that runs. If live-ingest
is not installed on the host, there is nothing further to do.

**The API and the worker restart together, always.** The restart ends every client session (ADR-0063 section 3) and
the in-memory transfer store of the API (see lost response below). A worker that starts before the API has recorded the
new commit exits `3` and is started again after `RestartSec`; that is expected, and it must be `READY` on the new commit
before you finish. Finish with one check that all running units name `$DEPLOYED_SHA`: the `deployment_commit=` of the API,
the `commit=` of the worker, and `QP_LIVE_INGEST_CODE_REF` of live-ingest.

**The deck checks out the same commit before a run.** The server's import accepts a result when its code identity equals
the `git_identity` of **its own admission** (ADR-0072 section 5); it does **not** compare it with the API's current commit,
so an admission made before a promotion stays valid and a result for it would still be accepted. Keeping results off the old
commit is therefore an operator obligation: after a promotion, abandon earlier admissions, make a new `admit` on the
promoted server, and check that the manifest's `git_identity` equals `$DEPLOYED_SHA` before the deck runs.

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

**A refused `submit`.** The result is not registered and stays deck-local. Do not edit the document by hand. A refusal
for a code identity means the bundle's commit is not the admission's `git_identity`: check out that commit on a clean tree
and run again from `admit`. A promotion does not make the server refuse an older admission; the rule above is what keeps a
result off the old commit.

**A deck and a server that do not speak the same families.** `WireIncompatible` from the client is a local typed failure,
not a Consumer API error; it is never retried with another wire family. See the [compatibility rule](SERVER_DECK_COMPATIBILITY.md).

## What this does not do

No automatic promotion, restart or rollback; no rotation of credentials (see [PKI operations](DECK_PKI_OPERATIONS.md));
no second worker; no deck on a different commit (deferred by ADR-0072 section 5); no automatic refusal of an older admission
after a promotion (the import compares with the admission's manifest, and changing that is an application change outside S06); no proof on the real host (that is
ADR-0072 section 7, `S07`).

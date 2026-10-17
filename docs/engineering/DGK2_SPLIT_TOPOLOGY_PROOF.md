# DG-K2 split topology proof (S07)

ADR-0072 section 7 defines when the server/deck topology is ready to split, and it takes two proofs. This page records
the first, says how to repeat it, and records the procedure and the reported result of the second. **The topology may be
called deployable only after both**, and, per the findings of part 2, only for deliveries within the bounds of
ADR-0072 section 1 (see [the findings](#findings-of-part-2)). Both parts have been executed.

| Part | What | State |
| --- | --- | --- |
| 1 | Hermetic proof: loopback TLS 1.3 mutual TLS, a disposable PostgreSQL, the API and the worker as separate processes started from the units' commands, the deck as a third | implemented and recorded below |
| 2 | Supervised proof on the real server with the real deck, on the smallest real partition, recording its size against the bounds | **executed 2026-10-17**, one happy-path run on a 3 KiB partition: see [Part 2](#part-2-supervised-real-environment-proof) |

## Part 1: the hermetic proof

### What it composes

`tests/split_topology_proof_v1.py` (class `SplitTopologyProofV1`), run as `PostgresSplitTopology` by
`tests/integration_runtime_stores_postgres.py`, the script of the CI job *Disposable PostgreSQL 17 runtime store parity
and least privilege*. It is not collected by `tools/run_tests.py`: it needs the disposable database.

- **API and worker** are separate processes. The command is the `ExecStart` of
  [`quant-platform-api.service`](../../infra/systemd/quant-platform-api.service) and
  [`quant-platform-worker.service`](../../infra/systemd/quant-platform-worker.service), with the interpreter and checkout of
  the machine, and the environment is the unit's `.env.example`. Only host-local values are overridden: the port, the
  certificate, key, trust anchor and principals file, the lock directory, and the two DSNs (which name a passfile, as in
  the units). One override is not host-local: the example serves the two families of the deck handoff, and the proof sets
  all six so that `J10`, `J12`, `J13` and `j02-job-v1` run over the same listener.
- **Credentials** are issued by [`infra/pki/`](../../infra/pki/README.md) (the CA, the server certificate for
  `localhost` and `127.0.0.1`, and one client certificate per principal), and the principals file is written from the
  fingerprints the scripts print.
- **Data**: a catalog on the disposable database with two generated `trade-v1` Parquet days (a seeded walk that rises and
  falls, so the proof replay trades). They are registered through the admin login; nothing is read from or written to a
  real market-data tree.
- **The deck** is the test process: `clients/deck/` for the protocol, and `deck_result_bundle` for the result. The
  workload is a real replay (`HistoricalReplayRuntime` over the delivered bytes) and a real training
  (`run_wave5_golden_supervised_proof` over candles computed from the delivered bytes). The deck work tree is a Git clone
  of the server's commit, so the workload module's clean-tree and same-commit rules apply as in production.

### Running it

It needs Linux (or any host where the catalog accepts a POSIX `storage_roots.abs_path`; the CHECK of
`db/init/001_catalog.sql` rejects a Windows path), `bash`, `openssl`, `git`, Python with `tests/requirements.txt`, and the
disposable database bootstrapped as in `.github/workflows/integrity.yml` (`001`–`005`):

```bash
export RUNTIME_TEST_ADMIN_DSN=postgresql://postgres:<password>@127.0.0.1:5432/dgk331_disposable
export RUNTIME_TEST_API_DSN=postgresql://platform_api:<password>@127.0.0.1:5432/dgk331_disposable
export RUNTIME_TEST_WORKER_DSN=postgresql://platform_worker:<password>@127.0.0.1:5432/dgk331_disposable
export RUNTIME_TEST_INGEST_DSN=postgresql://market_catalog_writer:<password>@127.0.0.1:5432/dgk331_disposable
SPLIT_PROOF_EVIDENCE_FILE=/tmp/split-proof-evidence.json python tests/integration_runtime_stores_postgres.py PostgresSplitTopology
```

The database must be named `dgk331_disposable`. The class refuses to run if the catalog already holds a `bybit:BTCUSDT`
canonical trades dataset, and removes what it created.

### What each acceptance property is proved by

| Acceptance (issue #397) | Test | What it observes |
| --- | --- | --- |
| 1. admit, deliver, verify, run, upload, submit; registration in the Experiment tables and the ledger; identical resubmission | `test_admit_deliver_verify_run_upload_submit_and_an_identical_resubmission` | two bundles (replay with provider; training) registered: 2 runs, 6 artifacts, 2 bundles and 6 outputs in the ledger; the same bundle again, with its bytes still staged and again after an API restart (nothing staged), returns the same registration and adds nothing |
| 1. one transaction | `test_a_failed_ledger_write_rolls_back_the_experiment_registration_of_the_same_transaction` | with `INSERT` revoked on the ledger the submit is refused and the Experiment rows written before it are gone; after the grant is restored the same bundle registers from the same staged bytes |
| 2. `J10`, `J12`, `J13` equal in-process | `test_j10_j12_and_j13_over_the_listener_equal_the_in_process_results`, `test_without_the_register_flag_nothing_is_written` | the response of each equals the in-process result; `register: true` writes the run once and a resend adds nothing; without the flag nothing is written |
| 3. `j02-job-v1` composed, registry empty | `test_the_job_family_is_composed_and_refuses_an_unregistered_kind` | `unregistered_operation_kind`, no job persisted |
| 4. lost `deliver` response | `test_a_lost_deliver_response_is_ended_by_the_deck_and_redelivered` | `end_pending_attempt`, redelivery as attempt 2, the stale report of attempt 1 refused with `delivery_attempt_mismatch` |
| 4. corrupted frame, consistently rewritten member | `test_a_corrupted_frame_and_a_consistently_rewritten_member_are_refused_by_the_client` | `DeliveryIntegrityFailure` (`content_hash_mismatch` for the rewrite), the admission is `DELIVERY_FAILED`, then delivered by an honest client |
| 4. wrong output checksum | `test_a_bundle_with_a_wrong_output_checksum_is_refused_and_nothing_is_registered` | refused, Experiment and ledger counts unchanged |
| 4. API restart between upload and submit | `test_an_api_restart_between_upload_and_submit_leaves_nothing_staged_or_registered` | submit refused, nothing registered; the admission survived (it is in PostgreSQL); the result is uploaded and submitted again in full |
| 4. API restart between deliver and acknowledge | `test_an_api_restart_between_deliver_and_acknowledge_is_recovered_by_the_deck` | the admission is `DELIVERY_PENDING` after the restart; the deck ends it and delivers again |
| 4. worker on another commit | `test_a_worker_on_another_commit_exits_3_and_touches_no_job` | exit `3`, the running job and its attempts unchanged |
| 5. security | `test_a_certificate_of_another_ca_an_unmapped_fingerprint_and_a_missing_scope_are_denied` | another CA: no handshake; unmapped fingerprint: denied; a principal with `admitted-input` only cannot use `result-import`; a principal with deck scopes only is closed `1008` on the strategy family |
| 5. rotation drill | `test_rotation_adds_the_new_fingerprint_then_removes_the_old_one` | new fingerprint added and the API restarted: both accepted; old one removed and the API restarted: the old is denied, the new works |
| 6. peak memory | `test_the_peak_memory_of_the_api_during_a_maximum_size_delivery_is_recorded` | see the evidence below |

**Mutation checks.** Each property was weakened in the product code (not in the test) and the test named above had to
fail. Twelve weakenings were killed: the worker ignores a commit mismatch; the API does not record its commit; a stale
acknowledgement is applied; staged bytes are not checked; the Experiment registration is committed before the ledger; a
resubmission is not recognised from the ledger; the client skips the member content hash; the client does not end the
pending attempt; any certificate of the CA is a principal; a job handler ships registered; `J10` returns another result;
`J13` registers without being asked. One weakening survived and is a layered defence, not a gap: removing only the
per-frame digest check of the client still leaves the payload digest, the member document check and the JSON parse that
refuse the same corrupted frame. A no-op mutant used as a control also survived, as it must.

### Evidence

Recorded by the run of 2026-10-17 on Linux 6.6.87.2 (WSL2), Python 3.12.3, PostgreSQL 16, 14 tests passing. The CI job
runs the same class on PostgreSQL 17 and Python 3.11 and prints the same record (`SPLIT_TOPOLOGY_EVIDENCE`).

Peak memory of the API process (VmHWM) for one delivery of two members of 27.4 MiB and 28.1 MiB (55.5 MiB, below the
32 MiB member and 64 MiB total bounds of ADR-0072 section 1):

| | bytes | MiB |
| --- | ---: | ---: |
| peak before the delivery (started, no client connected) | 55 476 224 | 52.9 |
| peak after the delivery | 280 604 672 | 267.6 |
| delivered | 58 153 593 | 55.5 |

The peak is about five times the bytes delivered. The cause was not investigated (a member read into memory, encoded
into a base64 document and framed would account for several copies). It is evidence, not a limit: nothing in ADR-0072
bounds it. An operator sizing the API host should not assume less than this ratio applied to the total bound (64 MiB)
plus the process baseline, until a measurement says otherwise.

### What this does not show

- It runs on one machine over loopback; it says nothing about a network between the deck and the server.
- The data are generated; the real partition sizes are part 2.
- The deck's replay and training are small; they prove that the delivered bytes are usable and the outputs registrable,
  not that a research workload fits the 8 MiB output bound.
- Part 2 below is the real-environment half; it ran one happy path and none of the faults of part 1.

## Part 2: supervised real-environment proof

Executed from the maintainer's account, in an agent-assisted session, on the real server (the API, the worker and
PostgreSQL under the units of the [handoff runbook](DECK_HANDOFF_RUNBOOK.md)) and the real deck, on the smallest real
partition. The values below are **as reported by the maintainer from that session's log**; they were not re-observed on
the server by the author of this page. Nothing here requires market data to enter Git.

1. List the real partitions and pick the smallest in the dataset the deck will request (read-only):

   ```sql
   SELECT d.rel_root, p.partition_key, p.byte_size
   FROM catalog.partitions p JOIN catalog.datasets d USING (dataset_id)
   WHERE d.layer = 'canonical' AND d.kind = 'trades' AND p.state = 'valid'
   ORDER BY p.byte_size LIMIT 10;
   ```

2. Run the procedure of the runbook end to end: promotion to a commit, `admit`, `deliver` with verification, the workload,
   `upload_output`, `submit`, and record the registration.
3. Fill the record below (no secret, no market data, no certificate or key).

| Field | Value |
| --- | --- |
| Field | Value |
| --- | --- |
| Date, operator | 2026-10-17, maintainer's account (agent-assisted session) |
| Server commit (`deployment_commit=`), worker `commit=`, deck checkout | `f6bc82e1d70ff67790ab5b77216176348353488e` (the head of the pull request of part 1, checked out on the server; not a commit of `main`) |
| Dataset and partition(s) delivered | `canonical/trades/bybit/BTCUSDT/trade-v1`, `dt=2026-10-03/part-235938.parquet`, requested as `2026-10-03T23:59:39Z`–`23:59:59Z` |
| Size of each member, in bytes | 3 122 |
| Total delivered, in bytes | 3 122 |
| Largest partition of the requested dataset, in bytes | 265 067 553 (`dt=2026-02-05`) |
| Peak memory of the API during the delivery (`VmHWM` of the API process) | 55 644 160 (53.07 MiB); baseline before the delivery 54 861 824 (52.32 MiB) |
| Outcome of each step (`admit`, `deliver`, verify, `upload_output`, `submit`) | all reported OK; admission `DELIVERED`, attempt 1 |
| Workload | a replay (`replay-result`, `replay-summary`); no training |
| Registration (`bundle_digest`) | `833e700b554fe903bdc8b09652b060d9d8566232eec27c504557c81673e5cbeb` |
| Observed in PostgreSQL afterwards | the run and its 2 artifacts in `experiment`, the bundle and its 2 outputs in `runtime.governed_result_*` |

| Bound (ADR-0072 section 1) | Value | Smallest real partition (3 122 B) | Largest real partition (265 067 553 B, 252.8 MiB) |
| --- | ---: | --- | --- |
| Member | 32 MiB | within | **exceeds** |
| Total of one admission | 64 MiB | within | **exceeds** |
| Retained by the J14 store | 96 MiB | within | **exceeds** |

A real partition above a bound is a **recorded configuration finding about the bounds, not a failure of the mechanism**
(ADR-0072 section 7). The decision to change a bound belongs to a new ADR amendment.

### Findings of part 2

1. **Bounds.** The largest real partition of the dataset is about eight times the member bound. A delivery above the
   bounds was **not exercised** on the real server, so what the API answers there (the refusal at `admit`) is shown only by
   part 1, with generated data. Until a bound is changed by an ADR amendment, the deck can receive only selections whose
   partitions are each at most 32 MiB and together at most 64 MiB. The sizes of the other real partitions were not
   reported: only the smallest and the largest are known.
2. **Install gap.** The server's virtual environment lacked the dependency `rfc8785` (declared in `pyproject.toml`), and it
   was installed by hand. The [handoff runbook](DECK_HANDOFF_RUNBOOK.md) says nothing about creating the environment or
   installing the platform's dependencies.
3. **Departures from the runbook, to be reviewed by the operator of the host:** the server checkout was moved to the
   pull-request commit with `git checkout` instead of `infra/promote-main.sh` (so it did not run a commit of `main`);
   `src/quant_platform.egg-info` was deleted to make the tree clean; and `chmod -R g+rX` followed by
   `chown -R mkt-transform:marketdata` was run on the whole `/srv/marketdata/canonical` tree, which is wider than the
   runbook's `usermod -aG marketdata qp-api`. The owners and modes before the change were not recorded.
4. **Scope.** One happy path was run; none of the faults of part 1 were repeated on the real host, and the delivery was
   3 KiB, so the memory figure says nothing about a delivery near the bounds (part 1 measured about five times the bytes
   delivered).
5. **The commit that ran is not the commit that will be released.** The next promotion to `main` changes the commit, and
   the units, the deck and every admission must follow it (see the runbook, *Promotion*).

#397 is fulfilled by the two parts as ADR-0072 section 7 defines them, with these findings recorded. The topology is
deployable for deliveries within the bounds; whether to release before the bounds are revisited is a decision of the
maintainer.

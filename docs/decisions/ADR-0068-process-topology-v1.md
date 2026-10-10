# ADR-0068 - Process topology v1 (DG-K)

**Status:** ACCEPTED
**Date:** 2026-10-10

## Context

ADR-0024 §6 and its implementation outcome leave open whether executable hosts
move to `apps/`, whether any component becomes an independently deployed
service, and the process topology. Wave 8 (closeout #327) built the remaining
pieces of that question in-process: `J03` records durable Jobs but
`application/durable_jobs.py` "deliberately does not schedule, invoke, or retry
a handler"; `K12` seals admitted-input manifests and `K13` imports result
bundles, but their stores are SQLite (`durable_jobs.py`, `admitted_input.py`,
`governed_result_import.py`) and `GovernedResultImportService.import_result`
writes the Experiment to PostgreSQL before it records the bundle evidence in
SQLite, with no common transaction.

Two code facts constrain the topology. Replay is stateful per tick:
`HistoricalReplayRuntime` holds an injected `feature_provider` and calls it for
every record (`replay/__init__.py`, the `feature_provider` field and its call
inside the record loop), and ADR-0058 §1 forbids temporal sharding of stateful
replay. Identities are hand-serialized at many sites: `src/quant_platform`
holds 66 `json.dumps` call sites (inventory below), several with different
byte profiles, and replay summaries hash a stream of such serializations.

Governance issue #318 recorded the owner's fixed inputs for this gate
(2026-10-08 and 2026-10-10): identity-preserving canonical profiles; executable
hosts stay in `tools/`; exactly one J03 worker in v1; the server stays as free
as possible, with server-side J03 handlers limited to data-local work and heavy
compute (replay, sweeps, training, large PBO) on the deck on the deck's own
initiative; and `J11` not planned. ADR-0069 decides the wire and scope half of
DG-K; this ADR decides units, ownership, storage, identity and dispatch.

## Decision

### 1. A modular monolith deployed as five units

One codebase, one Git commit or release tag per deployment, and the following
units. No unit is created per domain package.

| Unit | Surface | Host | Runs | Never runs |
| --- | --- | --- | --- | --- |
| **Ingest** (one process per venue/stream) | server | `tools/live_ingest_server.py` (ADR-0043, unchanged) | live acquisition, publication, checkpoints | Consumer API, Jobs, research compute |
| **Platform API** | server | `tools/api_server.py` | J02 (ADR-0050/ADR-0063) and the ADR-0069 families; bounded synchronous operations in-process (C03, J10, J12 and J13 within their ADR-0065 budgets, K12 admission and delivery, K13 import, J14 framing); J03 admission and status | J03 handlers; any work outside a synchronous budget |
| **J03 worker** (exactly one) | server | one new host in `tools/` (P04b) | dispatch of the closed, data-local J03 handler registry (§6) | replay, sweeps, training, PBO outside the J12 budget, any non-registered handler |
| **PostgreSQL** | server | operator-managed | `catalog`, `experiment` and `runtime` schemas (§4) | application code |
| **Deck compute** | deck | Omega, notebooks and deck scripts using `quant_platform` in-process, plus the ADR-0069 deck client | replay, sweeps, supervised training, large PBO, Omega, over K12-admitted inputs, on the deck's own initiative | canonical storage, catalog or Experiment writes except through K13 |

Live/paper execution (`J07`/`J08`) is not a unit in v1 and is deferred to
Wave 10.

The server never pushes, schedules or delegates work to the deck or to any
other machine, and no remote worker pulls from the server's J03 queue. The
deck initiates every server/deck interaction: it requests an admission,
fetches admitted bytes and submits result bundles (ADR-0069). This exercises,
and does not weaken, ADR-0057 §1/§6 and ADR-0062 §1/§6.

### 2. Domain packages stay in-process libraries

`representation`, `features`, `research`, `validation`, `strategy`,
`execution`, `portfolio`, `replay`, `learning` and `experiments` remain
libraries imported in-process by whichever unit composes them through
`quant_platform.application`. None becomes a service and no domain call
crosses a process boundary. Evidence: replay calls its `feature_provider` per
tick and owns per-tick ledger state; ADR-0058 §1 forbids splitting it by
time; ADR-0024's owner graph is enforced in-process by
`tests/test_package_boundaries_v1.py`. Process boundaries exist only where
ADR-0050/ADR-0063 transport or the PostgreSQL store already define one.

### 3. Synchronous and asynchronous interaction rules

| Interaction | Mechanism | Rule |
| --- | --- | --- |
| client to server, bounded | J02 request/response (ADR-0050, ADR-0069 families) | executed in the API process within the owning seam's declared budget; refused with the existing reason when outside it |
| client to server, unbounded data-local | J03 admission over J02 (ADR-0069 P05b) | the API persists `ADMITTED`/`QUEUED`; only the worker executes |
| ingest to consumers | publication into canonical storage and catalog (existing A09/A11), optional PostgreSQL outbox later | no broker |
| server and deck | K12 delivery and K13 submission initiated by the deck (ADR-0069 P06) | no job delegation in either direction |

Work that ADR-0065 Amendments 1 and 2 left "for a separate J03 dispatch design
gate" (PBO outside the synchronous budget, training outside the synchronous
budget) is answered here: it is deck work, not server J03 work. Such a request
keeps its existing `invalid_request` refusal over J02, and the deck runs the
in-process library operation and returns results through K13.

### 4. PostgreSQL schemas and roles

| Schema | Owner role (DDL only) | Unit login roles |
| --- | --- | --- |
| `catalog` (existing) | `catalog_owner` | ingest: existing writer login; API: reader; worker: writer, only where a registered handler publishes |
| `experiment` (existing) | `experiment_owner` | API: writer, for J13 registration and K13 import; worker: none in v1 |
| `runtime` (new, P02) | `runtime_owner` | API: reader/writer (Job admission and status, K12 admissions and deliveries, K13 evidence); worker: reader/writer (Job claim, attempts, outcomes) |

Each unit has its own login role; ingest's ADR-0041 identity is unchanged and
gains no runtime or experiment privilege. The deck has no database login.
`db/init/` gains the `runtime` schema and its roles in the existing
owner/writer/reader pattern of `002_roles.sh`.

P02 moves the `J03`, `K12` and `K13` stores to the `runtime` schema behind
their current store interfaces; the SQLite implementations remain only as
hermetic test doubles. P02 also **establishes** atomic K13 registration: the
Experiment write and the bundle evidence record commit in one PostgreSQL
transaction on one connection, which requires the Experiment repository to
accept a caller-owned transaction without changing any Experiment identity or
semantics. The P02 implementation issue must confirm that no production
SQLite runtime state exists; if any does, it must be carried over by a
one-shot import with identity parity, never dropped.

Moving runtime stores into PostgreSQL is not the canonical-storage migration
that ADR-0057 §6 excludes: canonical data and its storage are untouched.

### 5. Canonical identity serialization (P01)

P01 adds one module in the `shared` layer that serializes a value through a
named, versioned byte profile and returns exactly the bytes the current code
produces. Inventory of `json.dumps` sites in `src/quant_platform` on
`main` @ `56f6375` (66 sites):

| Profile | Bytes | Sites | Use |
| --- | --- | --- | --- |
| `sorted-compact-ascii-v1` | `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=True` | 56 | most identity and digest preimages (e.g. `durable_jobs.py`, `admitted_input.py`, `governed_result_import.py`, `replay/__init__.py`, `portfolio/__init__.py`, `validation/robustness.py`) and the J02 v1 wire body |
| `sorted-compact-utf8-v1` | as above with `ensure_ascii=False` | 3 | frozen data-plane identities: `representation/candles.py`, `representation/footprints.py`, `data/models.py` (`l2-book-event-v1`) |
| `ordered-compact-ascii-line-v1` | declared field order (`CANONICAL_FIELD_ORDER`), compact, ASCII, trailing `\n` | 1 | `application/bybit_import.py` canonical trade line, whose bytes are SHA-256 digested |
| `rfc8785-v1` | RFC 8785 via the `rfc8785` library | 2 (J14) | `application/framed_result_transport.py` only |

The remaining sites are not identity preimages and stay outside P01:
sorted with default separators (5 persisted operational documents in
`retention_deletion.py`, `storage_relocation.py`, `live_gap_orchestration.py`)
and one provider protocol message in `application/bybit_live.py`.

Rules:

1. Each identity site declares its profile; P01 replaces the call, never the
   bytes. A site's `allow_nan` setting is part of its profile parameters
   (35 sites refuse NaN today, 21 do not) and is preserved.
2. Value normalization (`Decimal`, enums, timestamps, nested domain values)
   stays with the owning domain; P01 does not merge normalizers, because
   doing so would change bytes.
3. `rfc8785-v1` is used only by J14 and by contracts created after this ADR
   that name it. No existing site moves to it. Changing any existing identity
   bytes requires a separate ADR-0018 contract evolution and is out of scope.
4. The `rfc8785` dependency is the only third-party import the shared layer
   gains; `tests/test_package_boundaries_v1.py` admits exactly that.

Acceptance proof for P01: every identity literal pinned in `tests/`
(including the replay vectors pinned by #313) is unchanged; for each migrated
site a byte-equality test compares the old call and the profile over a fixture
corpus that includes non-ASCII text, floats such as `1e-07` and `2.0`, and
`Decimal`; and the same vectors are recomputed in a separate Python process
under two different `PYTHONHASHSEED` values with byte-identical results.

### 6. J03 dispatch (P04a) and the single worker (P04b)

The J03 handler registry is a closed, declared set inside
`quant_platform.application`: each handler has an operation kind, an immutable
implementation identity and an ADR-0062 effect-safety class, and enters the
registry only through its own implementation issue citing this ADR. Handlers
are data-local only (materialization of representations and feature
artifacts, historical imports, catalog maintenance). Replay, sweeps,
training and PBO outside the J12 budget may not be registered. There is no
generic or caller-supplied handler, callable path or module name.

Dispatch keeps ADR-0062 unchanged: the worker claims a `QUEUED` Job in a
PostgreSQL transaction, persists the attempt as `RUNNING` before invoking the
handler in-process, and records `SUCCEEDED`/`FAILED`/`CANCELLED` with the
declared references. No automatic retry.

Exactly one worker process runs. The worker holds a PostgreSQL session-level
advisory lock on a fixed key for its whole lifetime; a second worker cannot
acquire it and exits at startup. The worker calls
`DurableJobStore.recover_after_restart` once, after acquiring the lock and
before claiming any Job. Because the lock is released only when the previous
worker's session ends, every `RUNNING` Job seen at that moment belongs to a
dead process, so the store's global recovery is correct without per-process
attempt ownership, and ADR-0062 §4's attempt fields are unchanged.

Evidence trigger for more than one worker: recorded Job evidence that a
`QUEUED` data-local Job waited more than 24 hours for dispatch on two separate
occasions within 30 days. Re-opening requires first an ADR-0062 §4 amendment
for per-process attempt ownership.

### 7. Executable hosts stay in `tools/` (P03)

Hosts remain in `tools/` (no `apps/`), so ADR-0043, ADR-0050 and the systemd
unit are unchanged. `tests/test_package_boundaries_v1.py` gains per-host
rules: a server host imports only `quant_platform.application` and its
submodules plus the standard library and declared third-party packages; no
host imports another host or a `tests` module. `clients/*` (including the J15
Omega client and the ADR-0069 deck client) stay outside `tools/` and outside
`src/quant_platform`.

### 8. Code identity between units

The ingest, API and worker units of one server deployment run the same Git
commit or release tag (ADR-0057 §2). The API records its commit in the
`runtime` schema at startup; the worker refuses to start when its commit
differs from the recorded one. The deck may run a different commit; it records
its own immutable commit in every K13 bundle (`deck_code_identity`) and its
compatibility with the server is decided by wire family version (ADR-0069),
never by package version.

### 9. Why no broker, scheduler or remote-execution framework

One queue with one consumer in the same PostgreSQL that already holds the
canonical catalog needs no broker; the deck initiates its own work, so nothing
needs remote scheduling; and every server-side operation is either bounded and
synchronous or a registered data-local handler, so nothing needs a generic
executor. This keeps SCOPE's DG-K exclusions, ADR-0057 §6 and ADR-0062 §6.

## Consequences

- The server runs a small fixed set of processes, and its CPU is spent on
  ingest, bounded API requests and data-local Jobs only.
- Heavy research compute has one home, the deck, and returns to the server
  only as verified K13 evidence.
- Identities stay byte-identical across processes and releases until a
  contract-evolution ADR says otherwise.
- K13 becomes atomic once P02 lands; until then the current non-atomic
  ordering remains a known limit recorded in the Wave 8 closeout.
- Adding a second worker, a remote worker, a server-side replay seam (`J11`)
  or a new unit each requires a new ADR.

## Implementation atoms

Order recorded in `CAPABILITY_DAG.md`: `P01`, `P02` and `P03` are independent;
`P04a` requires `P01` and `P02`; `P04b` requires `P03` and `P04a`. ADR-0069
covers `P05a`, `P05b` and `P06`.

## Out of scope

- wire messages and remote scopes (ADR-0069);
- any change to existing identity bytes, ADR-0062's lifecycle or attempt
  model, ADR-0064's sealed-input identity, ADR-0043's host placement or the
  frozen Consumer API;
- `J11`, server-side replay or training, live/paper execution;
- L2 acquisition semantics (`A13`-`A15`); this ADR fixes only the ingest
  process shell;
- Omega remote use of new capabilities (needs an ADR-0067 amendment).

## Acceptance evidence

The inventory in §5 was produced by an AST scan of every `json.dumps` call in
`src/quant_platform` on `main` @ `56f6375`. `durable_jobs.py`'s module
docstring and `recover_after_restart`, `governed_result_import.py`'s
`import_result`, `api_transport_server.py`'s single `j02.market_data.read`
scope, `db/init/` and the `tools/` hosts were read directly.
`tests/test_process_topology_contract_v1.py` guards the decisions above; it is
a design regression guard, not an implementation proof.

## Related

Governance issue #318; design gate #319. Amends no ADR.

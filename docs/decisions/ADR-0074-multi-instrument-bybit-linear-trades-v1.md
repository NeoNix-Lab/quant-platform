# ADR-0074 - Multi-instrument Bybit linear trades: read, delivery and live ingest v1

**Status:** PROPOSED (design gate #377; evidence input #378)
**Date:** 2026-10-19

Design gate #377; evidence issue #378. Amends ADR-0040, ADR-0043 and the applicability of the Bybit `trade-v1` ordering and
eligibility profile (`conformity` §10) only where section 8 says so, and only when this ADR is `ACCEPTED`. It leaves ADR-0042 and
ADR-0044 as they are.

## Context

The platform ingests, publishes, serves and delivers Bybit linear trades for one instrument, `BTCUSDT`. The maintainer holds
Parquet `trade-v1` files for other Bybit linear USDT instruments (ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT and others) and wants to read them through the
platform, deliver them to the deck and ingest the same instruments live. #377 asks whether that is possible "without weakening the
existing fail-closed identity guarantees". Verified against `implement/fid` @ `ca7c3f3`:

| Where | What is fixed to `BTCUSDT` |
| --- | --- |
| `source_adapters/bybit.py`, `bybit_trade_v1_applies_to` | the ordering provider and the publication eligibility profile apply only to `instrument == "BTCUSDT"` |
| `access/gateway.py`, `DataGateway._validate_support` | refuses a dataset to which the provider does not apply (`UnsupportedDatasetKind`) **before the catalog is reached** |
| `source_adapters/bybit_live.py` | `SUPPORTED_SYMBOL`, `SUPPORTED_TOPIC = "publicTrade.BTCUSDT"`, checks on topic and symbol, the dataset identity |
| `application/live_ingest_server.py` | `subject_identity = f"bybit:BTCUSDT:{storage_root_id}"` |
| `application/live_gap_orchestration.py` | gap remediation bound to `bybit_live_dataset_identity()` |
| `application/bybit_import.py`, `source_adapters/bybit_historical.py` | historical import supports one instrument (`SUPPORTED_INSTRUMENT`, `SUPPORTED_SYMBOL`) |
| `execution/__init__.py` | default fee rates are the BTCUSDT VIP0 rates |
| `tools/live_ingest_server.py`, `infra/systemd/quant-platform-live-ingest.service` | no `--instrument`; one unit with fixed environment names |
| ADR-0040, ADR-0043 §5 | the live loop is "a single bounded process for exactly one venue, instrument and schema" |

Reproduced on this commit: a `DataGateway.scan` for `bybit`/`trades@1` reaches the catalog for `BTCUSDT` and is refused for
`ETHUSDT` and `SOLUSDT` with `UnsupportedDatasetKind` ("ordering provider is not applicable to the requested dataset"). So a
dataset that exists in the catalog cannot be read, admitted or delivered today whatever its content.

What already fits: the ordering key `(exchange_ts, trade_id)`, `TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)`, the
dataset identity (which includes the instrument), the K12 identity texts, the J02 wire and the checkpoint identity
(ADR-0042 §4: a checkpoint carries its `dataset_identity`, so one file per instrument is a distinct recovery domain by
construction).

Reported by the maintainer, **not verified here**: the instruments' Parquet files follow the BTCUSDT structure and are already
registered. A query of the server's catalog reported in the same session lists only `BTCUSDT` (2 118 valid partitions). The two
statements conflict; the ADR does not depend on either (section 5). The storage and resource figures offered for the extra
instruments (daily volume, RSS, free space) are the kind #378 shows to be inconsistent and are not used.

## Decision

### 1. A closed allow-list of instruments

The venue is `bybit`, the market is the **linear USDT-settled perpetual** category, the dataset is canonical `trades` /
`trade-v1`. The admitted instruments are a **closed constant in code**, not a free string:

```text
BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
```

One definition is used by the ordering provider's applicability, the eligibility profile, the historical import and the live
adapter. Spot, inverse, other venues and any other symbol (for example `ADAUSDT`) are not admitted. A new instrument is admitted by an amendment appended to this ADR, a change of the constant and the
capacity rule of section 6. L2 is not covered: its ordering policy is keyed by `(venue, instrument)` and stays as ADR-0070 has it.

### 2. Identity

- Dataset identity: `DatasetIdentity("canonical", "trades", "bybit", <INSTRUMENT>, "trade-v1")` for each admitted instrument.
- Live subject identity: `bybit:<INSTRUMENT>:<storage_root_id>`; checkpoint path, process and health signals are per instrument.
- Ordering applicability: `bybit_trade_v1_applies_to` is true for the allow-list. The ordering key, `TradeKeyV1`, the canonical
  record and every existing `BTCUSDT` identity are **unchanged byte for byte** (pinned by the guard test).
- Reads: nothing in the J02 wire, K12 or the DataGateway contract changes. That a non-BTC dataset is read, admitted and delivered
  end to end is **not verified yet** beyond the ordering gate; the first implementation slice proves it (section 9).

### 3. Process model: one process per instrument

Live ingest runs **one process per instrument** (ADR-0043's loop, unchanged): a systemd template unit
`quant-platform-live-ingest@<INSTRUMENT>.service` with a per-instance environment file (`QP_LIVE_INGEST_CHECKPOINT_PATH`,
subject, instrument). Rejected: one connection with several topics, because a stall or a desynchronization of one instrument would
stop the others and the checkpoint, the subject and the health signals would have to be multiplexed. The processes share one
storage root and one catalog; that concurrent instances with different instruments neither collide on checkpoints, partitions or
catalog rows is **not verified yet** and is part of the live slice's acceptance (a stop condition if they collide).

### 4. Gaps and history

ADR-0044 stands: a gap is explicit and no authoritative repair source is proven; it is not weakened here. For a new instrument:

- The live stream starts when its process starts. The period before is **not acquired**, never "complete" and never inferred.
- History for an instrument comes only from the **historical import** (the one-UTC-day import generalized to the allow-list with an
  `--instrument` argument, its source evidence to be stated by its slice) or from files the maintainer already holds, which enter
  **only through the producer path** (materialize, certify, publish), never by a direct catalog write.
- Junction between history and live carries no overlap guarantee; coverage evidence is the only claim.

### 5. Existing Parquet of the maintainer

Whether the maintainer's files are already registered is the open fact above. The decision is independent of it: an instrument's
partitions are readable only when they are in the catalog as `valid` through the producer path with the eligibility profile of
section 2. The first slice starts with a **read-only check of the catalog** for the allow-list and registers what is missing through
that path; a file that does not pass certification is not published.

### 6. Capacity gate

Enabling live ingest for more than `BTCUSDT` in production requires the Phase A measurements of #378 (disk growth, compaction,
bytes per trade, trades per second, CPU and RSS of the existing service). The rule, not a number from this ADR: the projected time
for the hot storage root to reach the **critical-pressure threshold** of the live-ingest unit at the measured rate of all enabled
instruments must be at least **365 days** (a maintainer-accepted value, to be confirmed at acceptance). Until the measurement
exists nothing beyond a bounded proof is enabled. The proof the maintainer asked for is **one hour of real provider traffic for
`ETHUSDT`** (handshake, deduplication of `trade_id`, healthy signals, durable checkpoint), executed once; `SOLUSDT`, `XRPUSDT` and `DOGEUSDT` are validated by
the same contract tests and enabled only under the capacity rule.

### 7. Fees and execution

Fee parameters are explicit per instrument. The BTCUSDT VIP0 defaults of `execution` are not applied silently to another
instrument: a replay or an execution over a dataset whose instrument is not `BTCUSDT` and that names no fee schedule is refused
(fail-closed). `BTCUSDT` behaviour is unchanged.

### 8. What this amends

| Document | Statement | After this ADR is `ACCEPTED` |
| --- | --- | --- |
| ADR-0043 §5 | "exactly one venue, instrument and schema (`bybit`/`linear`/`BTCUSDT`/`trade-v1`)" | exactly one venue, **one instrument of the allow-list per process** and one schema |
| ADR-0040 | live source `publicTrade.BTCUSDT`, `BTCUSDT` dataset | `publicTrade.<INSTRUMENT>` for each admitted instrument; semantics of `TradeKeyV1`, dedup and ordering unchanged |
| `conformity` §10 profile | applies to `BTCUSDT` | applies to the allow-list; the profile's checks are unchanged |

ADR-0044's disposition, ADR-0042 and the single-venue limit (second venue, `A12`) are untouched.

### 9. Slices this ADR implies (to be authorized by a governance reconciliation after acceptance)

1. **Read and delivery** for the allow-list: the constant, the applicability change, an end-to-end test over a non-BTC dataset
   (`DataGateway`, K12 `admit`/`deliver`), the catalog check and registration of section 5, BTC regression.
2. **Live ingest per instrument**: adapter and server parameterized by instrument, per-instance checkpoint and subject, template
   unit, runbook, concurrency proof, the one-hour `ETHUSDT` proof. Deployment stays a manual operator step.
3. **Explicit fees** per instrument (section 7).
4. **Historical import `--instrument`** over the allow-list.

## Consequences

- A dataset of any admitted instrument that is published `valid` becomes readable and deliverable like `BTCUSDT`, with no change to
  the deck, the wire or the K12/K13 contracts; the deck pins no instrument.
- Every other consumer of the BTC literals (conformity fixtures, golden proofs) is unchanged: they name `BTCUSDT` on purpose.
- Live processes multiply with the instruments (one unit each); the cost is operational and bounded by the capacity rule.
- The allow-list is one place to change, and changing it is an ADR amendment.

## Not claimed

- That the maintainer's files are registered, readable or certifiable (section 5 checks it).
- Any measured capacity, compaction behaviour or per-instrument volume (#378).
- Spot, inverse, other venues, other symbols, L2 for other instruments, a generic provider resolver.
- A repair source for gaps (ADR-0044) or a backfill of live gaps.
- A deployment: enabling the units on the server is a manual, separate operator step.
- That concurrent live processes against one catalog and storage root are safe (to be proved by slice 2).

## Open items to answer before `ACCEPTED`

1. The allow-list members (proposed: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`).
2. The capacity rule's threshold (proposed: 365 days to the critical-pressure threshold) and whether the ADR is accepted before or
   after the Phase A numbers of #378.
3. The location and registration state of the maintainer's existing Parquet (section 5).
4. The historical source for the new instruments (slice 4).

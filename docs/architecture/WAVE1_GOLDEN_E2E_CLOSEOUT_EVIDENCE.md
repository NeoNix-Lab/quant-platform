# Wave 1 Human Golden E2E closeout evidence

**Issue:** #73. **Date:** 2026-09-19. Mechanically derived from the actual
operator run below; not a manual proof workflow.

## Prerequisite integration gate

Per issue #73's strict post-merge execution gate, this run is only valid if
`implement/wave-1` includes #58, #59, #60 and #65. Confirmed by ancestry:

```text
candidate base = 15f6017fd975029eb5ccdb78d71924f984332dd3
                 (merge-base of this branch with origin/implement/wave-1)

Merge pull request #72 from NeoNix-Lab/agent/issue-58-16   -> #58  ancestor: yes
Merge pull request #74 from NeoNix-Lab/agent/issue-59-17   -> #59  ancestor: yes
Merge pull request #76 from NeoNix-Lab/agent/issue-60-19   -> #60  ancestor: yes
Merge pull request #77 from NeoNix-Lab/agent/issue-65-20   -> #65 (== base) ancestor: yes
```

## Exact tested candidate

```text
branch          = agent/issue-73-21
candidate SHA   = d6f9a438310211d4dacc213a3173dd7b76e4bd10
```

## Operator run (verbatim, from the checked-out candidate SHA above)

Run on the Debian catalog host (`neonix@homelab.local`), where the
canonical partition's physical Parquet storage root is a real absolute
path; this Windows checkout can reach the PostgreSQL catalog over an SSH
tunnel but not the physical file (`resolve_partition_path` requires an
absolute storage root, which a Linux path is not under Windows `pathlib`).
Real Bybit BTCUSDT historical SQLite source and PostgreSQL catalog
(`market_catalog`, container `market-catalog`, already-published
2024-01-15 canonical partition from prior first-vertical work) -- no
ingestion, no catalog/storage writes, `tools/conformity_e2e.py verify` is
read-only per `collect_preflight`'s/`verify_vertical`'s own docstrings.

```text
$ git rev-parse HEAD
d6f9a438310211d4dacc213a3173dd7b76e4bd10

$ python tools/conformity_e2e.py verify
GOLDEN EXPECTATION
  venue=bybit instrument=BTCUSDT
  interval=[2024-01-15T00:00:00Z, 2024-01-16T00:00:00Z)
  row_count=1105145 buy=553875 sell=551270
  first_exchange_ts=2024-01-15T00:00:00.492Z
  last_exchange_ts=2024-01-15T23:59:59.931Z
SCAN OBSERVATION
  row_count=1105145 buy=553875 sell=551270
  other_aggressor_side=0
  first_exchange_ts=2024-01-15T00:00:00.492Z
  last_exchange_ts=2024-01-15T23:59:59.931Z
  batches=17 max_batch_size=65536
LIFECYCLE
  initial=OPEN after_first_batch=READING final=COMPLETED
  completed_metadata_present=True
COVERAGE METADATA
  coverage_complete=True
  coverage_gaps=0
PROVENANCE
  result_identity=574aeab50876514bcfc4e7a6df1c3e4d5b0147b15cc8967e3da55a9723fa164b
  catalog_partition_count=1
BOUNDEDNESS TELEMETRY
  unavailable
CANDLE RESULT
  definition_identity=candle-definition-v1:sha256:94b3a5a99c873f0d2805f4d197b772047f8c13e8753ae8fb4314aafebf1440cc
  candle_count=1440
  first_candle={'bucket_start': '2024-01-15T00:00:00Z', 'bucket_end': '2024-01-15T00:01:00Z', 'open': '41731.1', 'high': '41794.1', 'low': '41723.2', 'close': '41759.8', 'volume': '201.935', 'trade_count': '1733'}
  last_candle={'bucket_start': '2024-01-15T23:59:00Z', 'bucket_end': '2024-01-16T00:00:00Z', 'open': '42500', 'high': '42529.6', 'low': '42494.1', 'close': '42508.1', 'volume': '133.328', 'trade_count': '855'}
  result_identity=historical-candle-result-v1:sha256:998dce0c37898a8d165bcd3090d930bc75bed3a4b8066dd9765535d0ff7425e8
GOLDEN E2E: PASS

$ echo $?
0
```

## Focused test / integrity results (local checkout, same candidate SHA)

```text
$ python -m compileall -q src tests
(clean)

$ python -m unittest discover -s tests
Ran 866 tests in 17.026s
OK (skipped=1)

$ python -m unittest tests.test_package_boundaries_v1
Ran 15 tests in 6.857s
OK

$ git diff --check
(clean, exit 0)
```

## Acceptance mapping (issue #73)

1. #58/#59/#60/#65 integrated before the run -- confirmed by ancestry above.
2. Exact tested SHA recorded -- `d6f9a438310211d4dacc213a3173dd7b76e4bd10`.
3. Reused existing harness/production seams (`tools/conformity_e2e.py`,
   `quant_platform.application.conformity`/`golden_conformity`) -- no new
   framework.
4. `tools/` remains an Application-only client (unchanged from prior review).
5. Application owns the DataGateway -> D03 adaptation (`observe_scan_with_candles`,
   unchanged from prior review).
6. Streaming, bounded batch-to-trade adaptation (`_CandleScanBridge`,
   unchanged from prior review).
7. One terminal-operated path traversed source -> canonical publication ->
   catalog -> Application -> DataGateway -> D03 candles: proved by the
   verbatim transcript above, against the already-published canonical
   partition.
8. No new architectural bypass or test-only production capability.
9. Golden assertions are deterministic and PASS against the fixture-authoritative
   expectations on the exact recorded SHA -- see transcript.
10. N/A this run (no mismatch).
11. Focused harness/Application tests pass -- 866/866, see above.
12. Operator verification exit code -- `0` on this exact PASS.
13. No unrelated governance/runtime cleanup mixed into this change.

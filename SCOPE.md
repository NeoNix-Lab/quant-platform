# Scope: Repository Synchronization & Integrity Foundation v1

## Goal

Freeze and establish safe Git/GitHub/server synchronization, deterministic repository integrity checks and lightweight CI before DataGateway runtime implementation.

## In scope

Authority model; branch and promotion policy; review-ready branch pushes; guarded server updates; fast-forward-only promotion; GitHub CI; disposable PostgreSQL validation; repository/document integrity checks; exact Git deployment identity reporting; server integration-test conventions; heavy-test conventions; and governance updates.

## Out of scope

DataGateway implementation; API/client; automatic deployment; Docker/Kubernetes migration; generic configuration management; data replication; backup system; live-trading deployment; remote secret management; CI requiring private or bulk historical datasets; candles; features; research; labels; schema-v2; new L1/L2/L3 schemas; and execution or ML/RL migration.

## Exit criteria

- Authority model is documented: GitHub owns versioned code/history, the
  server owns operational data/runtime state, and desktop is the primary
  development environment.
- Server promotion is explicit, clean-tree guarded and fast-forward-only; no
  repository file-copy mirroring or automatic pull/deploy exists.
- CI runs suitable deterministic tests, documentation integrity checks and the
  disposable PostgreSQL catalog test.
- Heavy and real-data tests are explicitly separated from lightweight CI.
- Exact Git revision, branch, tag and dirty/clean state can be reported.
- `main` is green and suitable for controlled server integration, without
  implying live-trading certification.
- Independent review is completed before DataGateway implementation.

## Next cycle

DataGateway Implementation v1, after this synchronization foundation is
independently reviewed.

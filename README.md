# Quant Platform

## What it is

Quant Platform is a historical-first, live-targeted quantitative research and trading platform. The canonical repository is the server-born market-data repository; higher quantitative layers are built incrementally.

## Current status

The canonical data-plane foundation is real and implemented: `trade-v1`, dataset and partition manifests, PostgreSQL catalog DDL, lineage, storage roots, Bybit historical import, semantic fixtures and tests. A narrow catalog-backed DataGateway v1 implementation is present, CandleDefinition v1 is frozen, and the FeatureDefinition v1 semantic foundation is implemented. Broader representations, feature materialization, research, execution, learning, API and clients remain target layers.

## Architecture

```text
Market Data -> Data Plane -> DataGateway -> Representations / Feature Engine
            -> Research / Outcomes / Labels -> Policy / Strategy -> Execution
            -> Replay / Paper / Live -> Evaluation / Experiments -> API -> App / TUI / CLI
```

Clients will consume the canonical API; quantitative business logic belongs in domain/application services.

## Canonical capabilities

- `schemas/trade-v1.json`
- dataset and partition manifest contracts
- PostgreSQL catalog, storage roots and lineage
- Bybit trade importer
- semantic validators, fixtures and tests

## Repository layout

`db/` contains catalog initialization, `schemas/` contracts, `fixtures/` small reference data, `infra/` provisioning/operations, `tools/` utilities, `tests/` validation, and `docs/` product and architecture documentation.

## Documentation

- [Product](docs/product/PRODUCT.md)
- [Capability map](docs/product/CAPABILITY_MAP.md)
- [Roadmap](docs/product/ROADMAP.md)
- [Architecture](docs/architecture/TARGET_ARCHITECTURE.md)
- [Core contracts](docs/contracts/CORE_CONTRACTS.md)
- [DataGateway contract](docs/contracts/DATA_GATEWAY.md)
- [Market Data Ingest architecture](docs/architecture/MARKET_DATA_INGEST.md)
- [Market Data Ingest contracts](docs/contracts/MARKET_DATA_INGEST_CONTRACTS.md)
- [Storage Lifecycle](docs/architecture/STORAGE_LIFECYCLE.md)
- [Repository synchronization](docs/engineering/REPOSITORY_SYNC.md)
- [Architecture decisions](docs/decisions/README.md)
- [Current scope](SCOPE.md)

## Development and tests

The local Python test suite is made of executable script-style tests. Install `tests/requirements.txt` in an isolated environment, then run the repository-owned gate with `python tools/run_tests.py`. It discovers and runs every `tests/test_*.py` script using the same interpreter. `python tools/check_markdown_links.py` validates local Markdown links. `tests/test_catalog_ddl.sql` and `tests/integration_bybit_trades_2024_01_15.py` are separate database/real-data integration tests and require the infrastructure described in their files; they are not part of the local Python runner.

## Legacy relationship

`ml_core` is read-only reference material. Useful capabilities may be adopted at capability level after semantic and temporal review; legacy APIs, persistence and runtime boundaries are not canonical.

## License

Proprietary — All Rights Reserved.

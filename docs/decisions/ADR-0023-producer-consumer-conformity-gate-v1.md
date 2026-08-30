# ADR-0023 — Producer–Consumer Conformity Gate v1

**Status:** PROPOSED — pending independent review; governance and contract
freeze only, no runtime authorized

**Date:** 2026-08-30

## Context

The Data Plane producer track ([ROADMAP.md](../product/ROADMAP.md) "Parallel
producer track") and the consumer dependency track (DataGateway ->
Representation -> Feature -> ... -> API -> Clients) have so far been
documented as independent parallel tracks meeting only "through the existing
published Data Plane contracts and the DataGateway boundary."

An independent audit of the current baseline — [ADR-0019](ADR-0019-datagateway-boundary.md)
/ [DATA_GATEWAY.md](../contracts/DATA_GATEWAY.md), [ADR-0021](ADR-0021-candle-definition-v1.md)
/ [CANDLE_DEFINITION.md](../contracts/CANDLE_DEFINITION.md),
[ADR-0022](ADR-0022-declared-coverage-contract.md) /
[DECLARED_COVERAGE.md](../contracts/DECLARED_COVERAGE.md), and the narrow
DataGateway v1 implementation in `src/quant_platform/data/` — found that the
seam where the producer track's output becomes the consumer track's input is
not yet precise enough to implement against without inventing semantics. Eight
findings (B1–B8, below) were accepted as gate-blocking, verified against the
actual repository state:

- `tools/import_bybit_trades.py` writes JSONL (`write_jsonl_atomic`), not
  Parquet; no canonical `trade-v1` Parquet producer exists (**B1**).
- Commit `a166179` ("producer/manifest-catalog-bridge-v1") added only the
  `coverage-manifest-v1` contract, schema, fixtures and ADR-0022; no bridge
  code exists in `src/` or `tools/` (**B2**).
- [DATA_GATEWAY.md](../contracts/DATA_GATEWAY.md) §8 states that a `valid`
  partition is trusted without a full content rehash, but no document defines
  the evidence that must exist *before* a partition is marked `valid`
  (**B3**).
- `DataGateway.read()` in `src/quant_platform/data/gateway.py` accumulates
  every partition's rows into one Python `list`, extends it across partitions,
  and performs one global `records.sort(...)`; `read_trade_v1` in
  `src/quant_platform/data/parquet.py` materializes a full `pyarrow.Table` via
  `scanner.to_table()` per file. Both are O(total requested rows) memory
  (**B4**).
- The determinism that global sort currently provides is not backed by any
  documented producer obligation about physical row order, so a future
  streaming rewrite that removes the buffering has nothing to guarantee
  ordering in its place (**B5**).
- `returned_bounds` in `gateway.py` is constructed as a `CoverageInterval` —
  the same type used for declared/eligible coverage — reusing a coverage
  concept for a returned-data-only concept, and no finite-stream lifecycle
  (open/reading/completed/aborted) is defined anywhere (**B6**).
- `_metadata()` in `gateway.py` includes `content_hashes` in the payload
  hashed into `result_identity`, exactly as [DATA_GATEWAY.md](../contracts/DATA_GATEWAY.md)
  §5 specifies. This is not a bug against the current frozen text, but it
  couples `result_identity` to physical artifact bytes in a way that conflicts
  with [ADR-0021](ADR-0021-candle-definition-v1.md)'s materialization-equivalence
  principle ("recomputing the same semantic result ... does not create a new
  semantic materialization merely because the file, compression, catalog row
  or process changed") once a producer can regenerate byte-different Parquet
  for identical canonical records (**B7**).
- `DataRequest.ordering_policy` defaults to `"bybit-trade-v1-exchange-ts-trade-id-v1"`
  (`models.py`) and `gateway.py._validate_support` requires it verbatim, while
  `fixtures/candle-definition-v1/golden-5m.json` freezes
  `source.ordering_policy: "trades@1-canonical-total-order-v1"` as part of the
  CandleDefinition v1 identity hash. No document states how the two relate
  (**B8**).

Left unresolved, a producer-side implementation mandate (Parquet writer,
publication bridge, certifier) and a consumer-side implementation mandate
(streaming `DataGateway`, `RecordTimeBounds`, Candle runtime) would each have
to invent the other side's half of the seam, with no guarantee the two
inventions would agree.

### Second review round

A first candidate of this ADR and
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
was independently reviewed and returned `REQUEST CHANGES`. That review found:

- **B9** (new) — the frozen Bybit deterministic ordering policy,
  `(exchange_ts, trade_id)`, is unenforceable if `trade_id` is null, but
  `trade-v1.json` legitimately allows `trade_id = null` for sources with no
  native trade identity. The first candidate did not separate generic
  record-schema validity from Bybit publication eligibility. (Raised by the
  reviewer under the label "B1"; renumbered **B9** here to avoid colliding
  with the original B1, which remains a distinct, still-valid finding about
  the absent Parquet producer.)
- **B2 refinement needed** — the bridge invariants (BC1–BC8) did not define
  semantic catalog-rebuild equality (PostgreSQL UUIDs are not reproducible
  identities) or where durable certification evidence physically lives.
- **B7 refinement needed** — `CanonicalContentHash` was conceptually defined
  (§9 of the first candidate) but not specified as a reproducible algorithm:
  no domain separation, field order, serialization or framing was frozen.
- **Structural blocker** — the first candidate's single "gate PASS" concept
  was circular: Decision §1 suspended all runtime until gate PASS, while
  Decision §6's exit criteria implicitly required tested runtime slices to
  exist. A gate cannot require its own postcondition as its precondition.
- **Precision defects** — the durable certification-evidence persistence
  target was not named; the ordering proof (§7/OR4 of the first candidate)
  over-attributed cross-partition non-overlap to declared-coverage contiguity
  alone rather than to DataGateway's existing fail-closed `CatalogConflict`
  check; the frozen-ADR test guard omitted ADR-0021; Decision §1 referenced
  "§10 below" for exit criteria that were actually in a different item; and
  the contract's own test suite mixed contract-consistency checks with
  language that could be read as claiming behavioral proof.

This revision resolves all of the above. It does not reopen B1–B8's original
dispositions, which are unchanged.

### Third review round

The revised candidate was independently re-reviewed and returned
`REQUEST CHANGES` once more, with one remaining blocker and two important
corrections, all resolved in this revision:

- **Blocker** — the durable certification evidence model (§13.3 of the
  conformity contract) required a `quality_reports` row with `status='pass'`
  to authorize `state='valid'`, but did not require that row to carry a
  non-null certifier identity. A `status='pass'` report with a null or empty
  `code_ref` could therefore have authorized `valid` with no record of which
  certifier implementation produced the evidence. Resolved by new invariants
  CE6 (mandatory, non-null, non-empty `quality_reports.code_ref` as an
  independent precondition alongside `status='pass'`, using the existing
  column, not a new one) and CE7 (certifier `code_ref` and producer
  `code_ref` are distinct roles, never conflated).
- **Important** — a JSON example in §13.3 used `"revision": 0`, contradicting
  `NaturalPartitionIdentity`'s own positive-revision requirement
  (DATA_GATEWAY.md §3, `partitions.revision integer NOT NULL DEFAULT 1 CHECK
  (revision > 0)`). Corrected to `"revision": 1`.
- **Important** — the frozen `trade-v1.json` guard test computed a SHA-256
  digest and asserted only that it was 64 hexadecimal characters long, which
  passes for any file and proves nothing. Replaced with a canonical-JSON
  digest pinned against an explicit expected value, computed from the schema
  content rather than raw file bytes, so it is not sensitive to line-ending
  conversion.

This revision does not reopen B1–B9's dispositions or the two-gate model,
both unchanged.

## Decision

### 1. Two gates, not one — resolving the circularity

Two distinct, independently exitable gates are declared, so that "runtime is
suspended until the gate passes" and "the gate's exit criteria require tested
runtime" are never the same claim about the same gate:

```text
foundation contracts (ADR-0019, ADR-0020, ADR-0021, ADR-0022)
        |
        v
CONTRACT FREEZE GATE                    <- documentation-level; exit = Decision §6
  exit authorizes: implementing the conformity slices
  (Parquet writer, manifest/coverage emission, certifier, bridge, bounded
  DataGateway read) against this frozen contract
        |
        v
[implementation work happens here — NOT authorized by this ADR]
        |
        v
CONFORMITY IMPLEMENTATION GATE          <- runtime-level; exit = Decision §7
  exit authorizes: resuming producer AND consumer vertical expansion, and
  Candle runtime, per ROADMAP.md
```

**Contract Freeze Gate** — purpose: authorize implementation of the
conformity slices named in
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
§23. Its exit criteria (Decision §6) are entirely documentation-level:
independent review acceptance of this ADR and the conformity contract, with no
unresolved shared semantic blocker. It does **not** require any runtime to
exist. Passing it does **not** reopen broader producer or consumer vertical
expansion — only the conformity slices themselves.

**Conformity Implementation Gate** — purpose: prove the shared seam in
executable reality. Its exit criteria (Decision §7) are entirely
runtime-level: the conformity slices are implemented and pass the required
tests, including the golden vertical end-to-end proof. Only passing *this*
gate reopens broader producer vertical expansion, broader consumer vertical
expansion, and Candle runtime, per `ROADMAP.md`.

No document in this repository may state both "no conformity runtime before
gate PASS" and "gate PASS requires conformity runtime" about the *same* gate.
Where prior drafts used one undifferentiated "gate PASS," every occurrence is
now one of these two named gates.

### 2. The conformity boundary is frozen

As the composition of existing contracts — canonical records -> canonical
physical materialization -> `DatasetManifest`/`PartitionManifest`/`CoverageManifest`
-> certification -> catalog publication -> eligible `DataGateway` source ->
bounded finite historical read -> Representations/application services — not
a new canonical layer. Its normative content lives in
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md).

### 3. Findings disposition

B1–B8 (original) and B9 (new, this revision) are the gate-blocking concerns.
Each has an accepted interpretation; B5, B7 and B9 carry explicit refinements,
recorded in
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md).
No finding is resolved by silently reinterpreting ADR-0019, ADR-0021 or
ADR-0022; where existing frozen meaning needs to change, this ADR states that
explicitly (§4 below) rather than hiding it in prose.

### 4. This gate does not mutate any accepted ADR

ADR-0019 (DataGateway), ADR-0020 (Consumer API), ADR-0021 (CandleDefinition
v1) and ADR-0022 (Declared Coverage) remain accepted and unchanged. Where the
conformity contract needs new semantics beyond what they state, it introduces
them as additive extensions (for example, a `canonical_content_hash_v1` field
alongside the existing `result_identity`, per
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
§12) — never as an in-place reinterpretation. ADR-0021 in particular is
unchanged even though `CanonicalContentHashV1` (§11) and Candle ordering
compatibility (§8) both depend on it: they depend on its *existing* text, they
do not alter it.

### 5. Dependency ordering for the eventual runtime work

Frozen in
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
§23. In summary: shared semantic primitives (`RecordTimeBounds`,
`CanonicalContentHashV1`, ordering compatibility) come first; the physical
Parquet materializer and the bounded DataGateway read path may then proceed in
parallel; certification depends on the materializer; the catalog bridge
depends on certification; the golden end-to-end proof depends on all of the
above.

### 6. Contract Freeze Gate exit criteria (all required)

- the physical `trade-v1` Parquet contract (§9 of the conformity contract) is
  frozen and matches the columns `src/quant_platform/data/parquet.py` already
  reads;
- the Bybit first-vertical eligibility profile (§10, resolving B9) is frozen,
  distinct from generic `trade-v1` record-schema validity;
- the bounded historical read lifecycle and `RecordTimeBounds` contract
  (§§5–6) are frozen and distinct from `CoverageInterval`;
- the ordering contract for bounded reads (§7) and the DataGateway/Candle
  ordering-identity compatibility mapping (§8, resolving B8) are frozen, with
  the cross-partition non-overlap proof correctly attributed to DataGateway's
  fail-closed `CatalogConflict` check, not to declared-coverage contiguity
  alone;
- `CanonicalContentHashV1` (§11, resolving B7) is frozen as a reproducible
  byte-exact algorithm, and its relationship to `PhysicalArtifactHash` and the
  unchanged `result_identity` (§12) is frozen;
- the first-vertical certification rule for `state=valid` (§13, resolving B3),
  including the durable evidence persistence target and the
  certification/publication sequencing, is frozen;
- the manifest+coverage-to-catalog mapping and semantic catalog-rebuild
  equality (§14, resolving B2) are frozen;
- the golden conformity acceptance target (§15) and its required future proof
  obligations are recorded;
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_MAP.md`, `SCOPE.md` and
  `docs/architecture/OPEN_DECISIONS.md` consistently use the two-gate model;
- this ADR and the conformity contract have been independently reviewed and
  accepted, the same review discipline already applied to ADR-0019 through
  ADR-0022.

Passing the Contract Freeze Gate is a documentation-and-governance event. It
authorizes implementation of the conformity slices; it does not itself certify
that any future runtime implementation is correct, and it does not reopen
broader producer or consumer vertical expansion.

### 7. Conformity Implementation Gate exit criteria (all required)

- the canonical `trade-v1` Parquet materializer exists and satisfies §9 and
  §10 (physical contract and Bybit eligibility profile), including the
  canonical -> Parquet -> canonical round-trip test;
- the bounded DataGateway read path exists and satisfies §5–§8 (bounded-read
  property, `RecordTimeBounds`, ordering, ordering-identity compatibility),
  demonstrated with a memory-bounded-growth test;
- `CanonicalContentHashV1` is implemented per §11 and demonstrated
  reproducible across at least two byte-different, semantically identical
  Parquet artifacts;
- the certifier exists and enforces §13, with a negative test per evidence
  category (§13.2) showing the missing-evidence case refuses `valid`;
- the manifest+coverage-to-catalog bridge exists and enforces §14, including a
  demonstrated catalog rebuild that satisfies §14.4's semantic equality
  definition;
- the golden Bybit BTCUSDT 2024-01-15 vertical (§15) passes end-to-end,
  including the memory-bounded-growth proof (§15, GV2);
- the required adversarial conformity tests (§15.3) pass: zero-event coverage,
  gap/non-contiguous refusal, supersession, overlap rejection, relocation,
  catalog rebuild, physical-layout variation, abort-before-completion;
- the CandleDefinition ordering-compatibility acceptance test (§8.3) passes.

### 8. After Contract Freeze Gate PASS

Implementation of the conformity slices — Parquet writer, manifest/coverage
emission, certifier, catalog bridge, bounded DataGateway read path — is
authorized against the frozen contract. Producer vertical expansion and
consumer vertical expansion beyond these conformity slices (a second venue,
L1/L2/L3, live collection, Candle runtime, further representations) remain
suspended.

### 9. After Conformity Implementation Gate PASS

Producer vertical development and consumer vertical development resume as
independent parallel tracks against the proven shared seam, exactly as
[ROADMAP.md](../product/ROADMAP.md) already intends, and Candle runtime
implementation may resume per its roadmap position. This is a temporary
integration gate pair, not a new permanent architecture layer: once both are
passed, no future work needs to re-derive or re-prove the conformity contract
to proceed, only to extend it through the normal ADR-0018 evolution process if
a frozen piece needs to change.

## Consequences

- A subsequent mandate to implement the Parquet writer, the publication
  bridge, the certifier, or the bounded `DataGateway` read path can proceed
  directly from
  [PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
  once the Contract Freeze Gate passes, without re-deriving cross-boundary
  semantics.
- The golden Bybit BTCUSDT 2024-01-15 vertical (rows = 1,105,145; buy =
  553,875; sell = 551,270) already has partial evidence in
  `tests/integration_bybit_trades_2024_01_15.py` (SQLite -> JSONL only); the
  full SQLite -> canonical Parquet -> manifests -> catalog -> bounded
  DataGateway read chain remains a required Conformity Implementation Gate
  proof, not yet available.
- `result_identity` as defined by ADR-0019/DATA_GATEWAY.md §5 is unchanged and
  remains physical-evidence-sensitive; `CanonicalContentHashV1` is additive,
  not a replacement, so no existing provenance chain is invalidated.
- A Bybit `trade-v1` partition with `trade_id = null` remains schema-valid
  under the frozen, unchanged `trade-v1.json`, but can never reach
  `state = valid` under the Bybit first-vertical eligibility profile (§10 of
  the conformity contract) and therefore can never become DataGateway-eligible
  data for this vertical.
- Declaring the Contract Freeze Gate suspends only *vertical expansion beyond
  the conformity slices*. It does not forbid unrelated maintenance,
  documentation or test work elsewhere in the repository, and — once passed —
  it does not forbid the conformity slice implementation work itself.

## Review gate

This ADR must remain `PROPOSED` until an independent reviewer confirms this
ADR and
[PRODUCER_CONSUMER_CONFORMITY.md](../contracts/PRODUCER_CONSUMER_CONFORMITY.md)
— i.e. until the Contract Freeze Gate (Decision §6) passes. A separate
implementation mandate for the conformity slices may begin only after that
review, following the same discipline already applied to ADR-0021 and
ADR-0022; this ADR does not authorize producer or consumer runtime code by
itself, before or after Contract Freeze Gate PASS — only Decision §8's
implementation-slice authorization does, and only once §6 is actually
satisfied. The suspension in Decision §8 is binding on this branch
immediately and independent of the review outcome; review governs whether the
*contract content* is accepted as written or remediated again, not whether
vertical expansion beyond the conformity slices is currently permitted.

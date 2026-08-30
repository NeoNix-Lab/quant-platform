# Declared Coverage and Coverage Evidence Contract v1

**Status:** Contract v1 — semantics frozen; the manifest-to-catalog bridge is
not implemented

**Scope:** producer-side declaration of what a dataset covers, and the durable
evidence that supports it

This contract closes the open decision recorded as *"the exact population rule
for declared partition coverage versus observed first/last event bounds"* in
[MARKET_DATA_INGEST_CONTRACTS.md](MARKET_DATA_INGEST_CONTRACTS.md) §6 and
[OPEN_DECISIONS.md](../architecture/OPEN_DECISIONS.md). It does not implement
the bridge, a collector, or storage tiering.

The decision and its alternatives are in
[ADR-0022](../decisions/ADR-0022-declared-coverage-contract.md).

**Related:** [PRODUCER_CONSUMER_CONFORMITY.md](PRODUCER_CONSUMER_CONFORMITY.md)
§14 freezes the manifest+coverage-to-catalog bridge's obligations
(idempotency, natural identity keying, revision, storage-root mapping,
hashes, fail-closed failure behavior) and semantic catalog-rebuild equality
that consume this contract's `reconstruct_catalog_coverage()` fold; it does
not change §4's reconstruction rule or any invariant below.

## 1. The distinction being frozen

```text
OBSERVED EVENT BOUNDS                DECLARED COVERAGE
partition-manifest-v1                coverage-manifest-v1
first_exchange_ts                    assertion.start
last_exchange_ts                     assertion.end

"when did records actually arrive"   "for which interval was the source
                                      observed, and with what evidence"

null when row_count == 0             exists even when row_count == 0
a fact about a file                  a claim about an interval
```

`catalog.partitions.ts_start` / `ts_end` are **declared coverage**. DataGateway
already reads them that way — it prunes with `ts_end > start AND ts_start < end`
and computes `coverage_gaps` as the requested interval minus that union. This
contract does not change that reading; it supplies the durable source the values
must come from.

### The concrete failure this prevents

Populating `ts_start`/`ts_end` from `first_exchange_ts`/`last_exchange_ts` is
not merely imprecise, it makes reads non-deterministic. DataGateway selects
partitions by declared coverage but filters records by the **requested**
interval. A record lying outside its partition's declared coverage is therefore
visible or invisible depending on how the caller frames the request:

```text
declared coverage:  [00:00, 23:59:59.931)     <- copied from last_exchange_ts
record at:          23:59:59.931

request [23:59:59.931, 24:00:00)  -> partition not selected -> record invisible
request [00:00,       24:00:00)   -> partition selected     -> record returned
```

Same record, same catalog, two answers. Invariant **I8** below rejects this
mechanically, and it rejects the naive copy every time, because half-open
coverage always places `last_exchange_ts == ts_end` outside the interval.

## 2. The durable artifact

`coverage-manifest-v1` records **one acquisition or reconciliation operation**
and the coverage it asserts. Its schema is
[coverage-manifest-v1.json](../../schemas/coverage-manifest-v1.json).

```text
coverage-manifest-v1
  natural dataset identity        same tuple as the other two manifests
  coverage_id / supersedes        document identity and revision chain
  acquisition                     INTENT: basis, [intent_start, intent_end),
                                  versioned source_semantics, versioned mapping
  assertions[]                    CERTIFICATION, interval by interval:
      start / end                 half-open declared interval
      status                      complete | uncertain | known_gap
      partitions[]                natural partition identities
      evidence[]                  kind + detail
  producer / code_ref             who and with which exact code
```

It is keyed to the **dataset**, not to a file, because three required cases are
otherwise unrepresentable:

- a known gap for an interval where **nothing was ever materialized** — there is
  no partition to hang a sidecar on;
- coverage that changes while sealed content does not — a backfill proving an
  interval held zero events must not force a re-materialization to be recorded;
- an operation spanning several partitions, which must record its intent, source
  semantics and evidence once rather than duplicating them per file.

## 3. Invariants

**I1 — Observed bounds are not coverage.** `first_exchange_ts` and
`last_exchange_ts` are the exchange timestamps of the first and last records
present in the partition. They are null when `row_count == 0`. They must never
be populated with interval boundaries and are never an input to declared
coverage. This is now mechanically enforced in the direction the frozen JSON
Schema does not cover: the schema forces the fields to a real timestamp when
`row_count > 0` but says nothing about `row_count == 0`, so a document
declaring zero rows with a non-null bound would pass schema validation while
asserting a contradiction. `check_partition_manifest()` rejects it with
`ZERO_ROW_OBSERVED_BOUNDS`.

**I2 — Declared coverage is asserted, never derived.** Only a coverage-manifest
assertion with `status: complete` declares coverage. Nothing derives coverage
from event spacing, observed bounds, `partition_key`, `rel_path`, file names,
row counts, or acquisition intent.

**I3 — Half-open.** Every declared interval is `[start, end)` with
`start < end`. `start == end` asserts nothing and is rejected. Adjacency where
`A.end == B.start` is contiguous and is not an overlap. Comparison uses parsed
temporal values, never strings.

Declared coverage boundaries (`acquisition.intent_start`/`intent_end`,
`coverage_assertion.start`/`end`) carry **at most microsecond precision** — the
`coverage_timestamp` schema type caps them at 6 fractional digits. This is a
policy ceiling for this contract only: `catalog.partitions.ts_start`/`ts_end`
are PostgreSQL `timestamptz`, which round-trips microseconds exactly and would
silently truncate anything finer, and this contract will not ask a future
bridge to redesign that column type. Observed event timestamps (`trade-v1`
`exchange_ts`/`receive_ts`, `partition-manifest-v1`
`first_exchange_ts`/`last_exchange_ts`) are untouched by this ceiling and keep
their existing nanosecond-capable precision; the reference validator's
internal comparison remains exact to the nanosecond throughout; only what a
*document* is allowed to *declare* as a coverage boundary is capped.

**I4 — Acquisition intent is not certification.** The union of a document's
assertions must lie inside `[intent_start, intent_end)`. Certifying **less**
than the intent is normal and expected; certifying more is rejected. Requesting,
downloading or subscribing to an interval proves nothing about it.

**I5 — Zero events is not absent coverage.** A `complete` assertion referencing
a partition with `row_count == 0` and null observed bounds is complete coverage.
This is the case DataGateway must distinguish from `NoCoverage`.

**I6 — Absence of assertion means no coverage.** An interval with no `complete`
assertion is uncovered. `known_gap` is a stronger, positively evidenced
statement — it is not a prerequisite for a gap to exist. `uncertain` and
`known_gap` never contribute coverage.

**I7 — Completeness must be materialized and must not contradict its evidence.**
A `complete` assertion references **exactly one** natural partition identity, so
per-partition coverage is determined without circularity. Evidence of
`transport_interruption` or `sequence_discontinuity` forbids `complete`, and
adding favourable evidence alongside does not cancel it.

**I8 — Observed bounds lie inside declared coverage.** For a partition with
`row_count > 0`:

```text
first_exchange_ts >= ts_start    and    last_exchange_ts < ts_end
```

The right bound is strict because coverage is half-open. **A record outside the
declared coverage of its partition is a loud failure, not a silent
classification.** The producer must either declare — with evidence — coverage
that contains the record, or the record does not belong to that partition. It
must not widen coverage on its own to make the check pass, and it must not
quietly accept the record: §1 shows the result is otherwise non-deterministic.

**I9 — One catalog row carries one contiguous complete interval.** A partition
whose attributed `complete` coverage is empty, or is more than one interval
after half-open merging, is **not publishable** to `catalog.partitions`.

Three independent reasons, in increasing order of severity:

1. The catalog holds a single `(ts_start, ts_end)` pair per partition.
2. The `partitions_one_live` unique index admits one live revision per
   `partition_key`, so the gap cannot be pushed into a sibling row.
3. **No lifecycle state narrows coverage.** `state` decides *which* partitions
   are selected, never *how much of their span counts*. `catalog.py` selects on
   `p.state = ANY(states)` and then `gateway.py` takes
   `CoverageInterval(ts_start, ts_end)` whole. Marking such a partition
   `degraded` therefore does **not** make the span honest — it only postpones
   the lie until someone opts in. Section 4 of
   [test_coverage_boundary_audit.py](../../tests/test_coverage_boundary_audit.py)
   proves this against the real `DataGateway`: under
   `VALID_CLOSED_AND_DEGRADED` the gateway reports `coverage_complete = True`
   over a known gap, and a request aimed at the gap itself returns a successful
   empty result indistinguishable from a genuinely quiet interval.

So publishing the outer span is forbidden **in every lifecycle state**, not
merely discouraged for `valid`. What the producer does instead is §7.1.

**I10 — Supersession is wholesale, never narrows, and its graph is validated
before anything is published.** A superseding document **replaces the
superseded one entirely**: it must restate the full semantic responsibility of
the document it supersedes, and its acquisition interval must contain the
superseded one's. It may change assertion status, evidence, or partition
attribution/revision — `known_gap` or `uncertain` becoming `complete` is
exactly the repair case I5 relies on — but it must not silently drop part of
what the superseded document covered.

Narrowing (`COVERAGE_SUPERSESSION_NARROWS`) is **fail-closed, scoped to the
lineage it names**: neither the superseded document nor the narrower
superseding one contributes any coverage. Publishing the superseding
document's own (narrower) interval would be exactly the silent loss this
invariant exists to prevent — a "safe subset" is not safe, because nothing
downstream can tell it apart from a document that legitimately never claimed
the rest. Only that lineage's two endpoints are affected; an unrelated
document elsewhere in the same call is untouched. Partial or interval-level
supersession — restating only the part that changed — is explicitly not
implemented; a producer that needs to repair one interval must restate the
superseded document's full domain.

The graph formed by `coverage_id` and `supersedes` must additionally be valid,
or **no document in the input set is publishable**:

- no self-supersession (`COVERAGE_SUPERSESSION_SELF`);
- no cycle of any length (`COVERAGE_SUPERSESSION_CYCLE`) — a cycle would
  silently mark every member of the cycle as superseded, eliminating them all
  with zero visible cause;
- no branching, i.e. two or more documents naming the same `supersedes`
  target (`COVERAGE_SUPERSESSION_CONFLICT`);
- no dangling reference to a `coverage_id` outside the supplied set
  (`COVERAGE_SUPERSEDES_UNKNOWN`);
- no duplicate `coverage_id` (`COVERAGE_ID_DUPLICATE`) — the documents sharing
  it are excluded from the graph entirely, so which one happens to be kept
  cannot silently decide the result.

This graph-validity gate is call-wide (any one of the five defects above
blocks every document in the call); narrowing is lineage-scoped (only the
two endpoints of the offending edge are blocked). The difference is
deliberate: the five graph defects above make it impossible to know who is
even alive, while a narrowing edge names its live document unambiguously —
the document is simply not trusted to speak for the domain it claims to
replace.

Precedence comes **only** from the explicit `supersedes` edge. Nothing else —
not `created_at`, not array order, not filesystem or processing order — may
ever decide which document is live. The five checks above depend only on the
*set* of `coverage_id`/`supersedes` pairs, never on the order documents are
supplied in, which is why the same input always yields the same
publish/refuse decision.

**I11 — Venue semantics stay behind versioned identities.** Source-specific
coverage reasoning is named by `source_semantics` and `mapping`, both of which
must carry an explicit `-v<N>` suffix. The canonical assertion representation is
venue-independent: an archive, a REST pagination run, a deterministic SQL
extraction and a live sequence-continuity session all produce the same shape.
Evidence strength is preserved rather than flattened —
`deterministic_source_extract` proves the **local** source was read completely
and is never promoted to `archive_completeness`.

**`complete` is always relative to the declared `source_semantics`, never an
absolute claim about the venue.** It means: *under these named, versioned source
semantics, the canonical Data Plane has materialized this interval and holds no
evidence that anything is missing.* It does not mean the venue published nothing
more. This is why `source_semantics` is mandatory and versioned: it is the scope
qualifier that stops "the source artifact was read completely" from silently
becoming "venue history is complete". A legacy SQLite extraction and an
authoritative venue archive can both be `complete`; they differ in the source
semantics they are complete *with respect to*, and that difference stays legible
in the durable record. No evidence kind in this contract ever asserts venue
completeness, and none may be added that does without a source-specific
guarantee stated in its own versioned source semantics.

**I12 — Durable, not catalog-only.** `catalog.partitions.ts_start`/`ts_end` must
be reconstructible from coverage manifests plus partition manifests alone. The
catalog remains a runtime index, as `db/init/001_catalog.sql` already claims.

**I13 — A `complete` assertion must resolve to exactly one existing, eligible
partition manifest, and each `partition_key` may have at most one live
revision.** Referencing a `(partition_key, revision)` with no matching
partition manifest is `PARTITION_UNKNOWN`. Two or more partition manifests
sharing the same `(partition_key, revision)` are an unresolvable ambiguity —
which file is the real one? — reported as `PARTITION_MANIFEST_DUPLICATE`, and
no revision of that key is published. Two or more non-`superseded` partition
manifests sharing the same `partition_key` are `PARTITION_LIVE_REVISION_CONFLICT`
for the same reason `partitions_one_live` exists in the catalog DDL: which
revision is the live one must already be unambiguous in the partition
manifests themselves, before coverage is even considered. A referenced
manifest whose state is `writing`, `invalid` or `superseded` is
`PARTITION_NOT_ELIGIBLE` — none of those states is a safe target for a
completeness claim. All four checks are scoped to the specific
`partition_key` (or `(partition_key, revision)`) they concern; they do not
block unrelated partitions in the same reconstruction call.

**I14 — Assertion identity is explicit and document-scoped.** Every assertion
carries `assertion_id`, a stable identity distinct from its position in the
`assertions` array — reordering, inserting or removing a sibling must never
change what an existing `assertion_id` refers to. Uniqueness is scoped to one
coverage-manifest document; the same value in two different documents does not
collide (`ASSERTION_ID_DUPLICATE` fires only within one document). It is not a
distributed identity: no runtime, path or catalog UUID is admitted.

**I15 — Reconstruction is scoped to exactly one `DatasetIdentity` per call.**
`reconstruct_catalog_coverage()` refuses to run — `coverage = {}`,
`RECONSTRUCTION_DATASET_MIXED` — if the coverage manifests, the partition
manifests, or the two families against each other, span more than one natural
dataset identity. Two unrelated datasets may coincidentally reuse the same
`coverage_id` or `partition_key`; without this gate, a caller who accidentally
merges input from two datasets into one call could see them interact. The gate
makes that impossible rather than relying on callers to keep inputs separate.

## 4. Reconstruction rule

The reference fold is `reconstruct_catalog_coverage()` in
[semantic_validator.py](../../tools/semantic_validator.py). It is pure, takes
parsed documents, and does no I/O.

```text
0. refuse outright if the input spans more than one DatasetIdentity  (I15)
1. validate the supersession graph; if invalid, publish nothing at all (I10)
2. drop every document named by another live document's `supersedes`, and
   drop both endpoints of any narrowing edge — neither the superseded nor
   the narrower superseding document contributes (I10, lineage-scoped)
3. validate partition-manifest existence and live-revision uniqueness (I13)
4. keep assertions with status == complete, from documents surviving step 2
5. attribute each interval to its single referenced natural partition identity,
   skipping any key already excluded by step 3
6. per partition, merge half-open (adjacency merges):
     exactly one interval  -> ts_start, ts_end
     zero or more than one -> not publishable, reported
7. a non-complete assertion intersecting a published interval is a
   contradiction: that key publishes nothing, not even the `complete`
   document's own interval
8. verify I8 against the partition manifest's observed bounds
```

The result depends only on the document set, not on document order, filesystem
order, or catalog state — steps 0–2 are pure functions of the *set* of
identities, `coverage_id`/`supersedes` pairs, and `(partition_key, revision)`
references, never of the order anything was supplied in. Violations are
returned, never repaired.

### Overlap between live documents

Two live (non-superseded) documents may overlap only when they agree: the
same status, attributed to the same partition, is a compatible restatement and
their intervals merge (§I7's "exactly one interval" requirement is evaluated
*after* this merge). Any overlap where one document's `complete` interval
intersects another live document's `known_gap` or `uncertain` interval is
`COVERAGE_CONTRADICTION`, checked symmetrically regardless of which document
was written or supplied first. **Fail-closed**: the contradicted key publishes
no coverage at all — not the `complete` document's interval, not a "safe"
trimmed remainder. There is no way to choose which of two opposed claims is
right, so neither is used; a real resolution requires an explicit supersession
that restates the whole domain (I10), not a majority vote between contradictory
documents.

Overlap between two `complete` attributions naming **different** partition
keys over the same wall-clock interval is deliberately **not** checked here.
That is the same ambiguity `DataGateway`'s existing `CatalogConflict` already
detects at read time over `catalog.partitions` (see
[DATA_GATEWAY.md](DATA_GATEWAY.md) §7) — re-implementing it in the producer
fold would duplicate a check that already lives at the boundary meant to catch
it, for no additional safety.

Stable violation codes: `COVERAGE_INTENT_INVALID`, `COVERAGE_INTERVAL_INVALID`,
`COVERAGE_OUTSIDE_INTENT`, `COVERAGE_STATUS_CONTRADICTS_EVIDENCE`,
`COVERAGE_ASSERTIONS_OVERLAP`, `COVERAGE_IDENTITY_MISMATCH`,
`ASSERTION_ID_DUPLICATE`, `COVERAGE_ID_DUPLICATE`,
`COVERAGE_SUPERSESSION_SELF`, `COVERAGE_SUPERSESSION_CYCLE`,
`COVERAGE_SUPERSEDES_UNKNOWN`, `COVERAGE_SUPERSESSION_CONFLICT`,
`COVERAGE_SUPERSESSION_NARROWS`, `RECONSTRUCTION_DATASET_MIXED`,
`PARTITION_UNKNOWN`, `PARTITION_MANIFEST_DUPLICATE`,
`PARTITION_LIVE_REVISION_CONFLICT`, `PARTITION_NOT_ELIGIBLE`,
`PARTITION_NOT_PUBLISHABLE`, `COVERAGE_NOT_CONTIGUOUS`,
`COVERAGE_CONTRADICTION`, `COVERAGE_MISSING_FOR_PARTITION`,
`OBSERVED_OUTSIDE_DECLARED`, `ZERO_ROW_OBSERVED_BOUNDS` (the last on
`partition-manifest-v1` documents directly, via `check_partition_manifest()`,
independent of any reconstruction call).

## 5. Placement

A coverage manifest lives under the dataset's `rel_root`, in a reserved
`_coverage/` directory, one file per document named `<coverage_id>.json`.
`_coverage` cannot collide with a partition directory: `partition_key` must
match `^[a-z_]+=...`, so it always contains `=`, and `_coverage` does not.

Which storage root holds `_coverage/` when one dataset's partitions span hot,
cold and deep-cold roots is **not** frozen here — it belongs to
[STORAGE_LIFECYCLE.md](../architecture/STORAGE_LIFECYCLE.md). The reconstruction
rule is deterministic given the document set; enumerating that set across tiers
is a storage concern.

## 6. Non-goals

This contract does **not**:

- implement the manifest-to-catalog publication bridge, a collector, backfill,
  repair, or storage tiering;
- change `trade-v1`, `dataset-manifest-v1` or `partition-manifest-v1`;
- change DataGateway behavior, its request/result model, or its STRICT v1
  coverage policy;
- define quality-report to `valid`/`degraded`/`invalid` transitions;
- define live/backfill source precedence, overlap resolution, deduplication, or
  repair triggering;
- assert any universal venue completeness guarantee — no evidence kind claims a
  venue published everything it should have;
- freeze L1/L2/L3/MBO evidence formats, a session model, the physical Parquet
  schema, checkpoint storage, container topology, retention, or capacity
  thresholds;
- introduce a generic evidence, workflow or DAG framework. The evidence
  vocabulary is a flat enum plus free-text detail, and covers only the cases the
  frozen semantics require.

## 7. Migration and evolution

### 7.1 What a producer does with non-contiguous coverage

The durable record always keeps the truth: the coverage manifest states
`complete / known_gap / complete` and the canonical file is untouched. Only
*catalog publication* is blocked. Four options were evaluated; v1 permits two.

| Option | Verdict |
|---|---|
| **A.** publish the outer span with `state = degraded` | **Forbidden.** Encodes a lie in `ts_start`/`ts_end`; proven unsafe against the real gateway (I9.3) |
| **B.** re-key so each published partition has contiguous coverage | **Permitted, not mandated.** Mechanically valid — `partition_key` grammar allows `dt=…/seg=…` and distinct keys satisfy `partitions_one_live`. But letting transport accidents dictate partition keys is an implementation leak, so it is a producer's deliberate choice, never an automatic reaction to a disconnect |
| **C.** outer span plus gap evidence in a separate catalog relation | **Not available in v1.** DataGateway reads coverage only from `partitions.ts_start`/`ts_end`; C requires a new catalog relation *and* gateway changes. This is the natural future evolution, not a v1 option |
| **D.** leave the partition unpublished until repaired | **Default.** Safest minimal rule, and it blocks only exceptional cases |

D is the default because it costs little: a repair that later proves the
interval — including one returning zero records — makes the partition
publishable without re-keying, which is exactly the CASE 6 fold. Normal
acquisition produces one contiguous interval and is unaffected.

### partition-manifest-v1 stays unchanged

`schemas/partition-manifest-v1.json` is **not modified** by this contract, in
either its rules or its bytes. ADR-0018 freezes it, and its `first_exchange_ts`
/ `last_exchange_ts` semantics were already correct. Existing partition
manifests and fixtures remain valid, and no producer output has to be rewritten.

### Errata for stale wording in partition-manifest-v1

The frozen schema's *descriptive text* uses "copertura" (coverage) for what this
contract calls observed event bounds. The wording is stale; the rules are not.
Rather than edit a frozen contract in place, the corrections are recorded here.
Where the two disagree, this errata governs.

| Location in `partition-manifest-v1.json` | Stale wording | Correct reading |
|---|---|---|
| root `description` | "la sua copertura temporale e di sequence" | observed event bounds and observed sequence bounds |
| `partition_key.description` | "La copertura temporale REALE resta descritta da first/last_exchange_ts" | the declared interval is **not** described by the event bounds; it lives in `coverage-manifest-v1`. `partition_key` identifies the partition and does not imply a UTC day |
| `last_exchange_ts.description` | "definisce la copertura da cui si rilevano gap e overlap fra partizioni" | defines the observed event bounds. Gaps and overlaps are determined from declared coverage; distance between events is never a gap |
| `allOf` "se ci sono record, la copertura temporale deve esistere" | — | if there are records, the observed event bounds must exist |

`db/init/001_catalog.sql` carries the same phrase on its `rows_imply_coverage`
constraint. There the columns genuinely **are** declared coverage, so the
constraint is correct as written; only note that it permits a zero-row partition
to have null `ts_start`/`ts_end`, which under I5 means such a partition declares
no coverage and is not eligible.

This errata is sufficient under current repository governance, for three
reasons that were re-checked against the merged Consumer API baseline:

1. A JSON Schema `description` is a **non-normative annotation** — it constrains
   no instance. The stale sentences therefore misdescribe rules they cannot
   change, which is exactly the class of defect an errata can govern without
   touching a byte of the frozen file.
2. [ADR-0018](../decisions/ADR-0018-frozen-market-data-contract-evolution.md)
   freezes the file, and
   [ADR-0020](../decisions/ADR-0020-consumer-api-boundary.md) §8 states the
   repository's stance directly: a decision record "may document or authorize
   the evolution but cannot mutate frozen meaning in place."
3. The correction is mechanically enforced, not merely written down. Invariant
   I8 makes the wrong reading *fail* rather than be discouraged.

A versioned `partition-manifest-v2` is not warranted: v1's rules are correct and
nothing in this contract needs a new field there. Amending the prose in place
remains possible later if the project decides annotation text should be
maintainable, but it would be a documentation change governed by this errata,
never a semantic one.

### The `row_count == 0` gap is structural, not stale wording

One more inconsistency was found in the frozen schema, of a different kind
than the errata table above: not stale prose, but a genuine one-directional
gap. `partition-manifest-v1.json`'s conditional forces `first_exchange_ts` and
`last_exchange_ts` to a real timestamp when `row_count > 0`, but says nothing
about `row_count == 0` — a document declaring zero rows with a non-null bound
passes schema validation while asserting a contradiction (a "first event" in a
partition with no events).

This is not corrected by editing the frozen file, for the same governance
reason as the errata above, and it does not need a `v2`: it is closed entirely
by the semantic validator, which is exactly the layer this repository already
designates for cross-field invariants JSON Schema cannot express (see the
module docstring of
[semantic_validator.py](../../tools/semantic_validator.py)). See I1.

### Evolving this contract

Adding an evidence `kind`, an acquisition `basis`, or a status is additive
contract evolution under
[ADR-0018](../decisions/ADR-0018-frozen-market-data-contract-evolution.md).
Changing the meaning of an existing field, the reconstruction rule, or an
invariant requires `coverage-manifest-v2`.

## 8. Position relative to the Consumer API boundary

[ADR-0020](../decisions/ADR-0020-consumer-api-boundary.md) and
[CONSUMER_API.md](CONSUMER_API.md) freeze a *representation/application-level*
coverage triple — `covered_intervals`, `gaps`, `complete`. That is a different
layer from this contract and neither derives from the other:

```text
coverage-manifest assertions     producer evidence, source-scoped
        ↓  fold (§4)
catalog.partitions ts_start/ts_end   data-plane declared coverage
        ↓  DataGateway
eligible_coverage / coverage_gaps    internal, per DATA_GATEWAY.md
        ↓  application service
covered_intervals / gaps / complete  representation-level, per CONSUMER_API.md
```

Three consequences, all consistent with CONSUMER_API.md §5:

- **Producer coverage is not the consumer shape.** A derived representation may
  legitimately have different coverage from its source because of warmup, late
  events or its own definition. This contract must not be reshaped to mirror the
  consumer envelope, and the consumer envelope must not be read as the producer
  storage model.
- **Evidence and gap reasons stay below the boundary.** `basis`,
  `source_semantics`, `mapping`, evidence kinds and `known_gap` versus
  `uncertain` are producer provenance. They may surface as diagnostics; they are
  not consumer coverage semantics.
- **Evolving this contract does not version the Consumer API.** Adding an
  evidence kind or a basis changes no consumer-visible meaning. Only a change
  that alters what a consumer observes — for instance if complete-with-zero-
  records stopped being a success — would require an API version.

The one semantic that must hold identically at every layer is the distinction
this contract exists to protect:

```text
complete coverage + zero records  -> success, empty
no / partial coverage             -> NoCoverage -> API no_coverage
```

## 9. Executable evidence

| Requirement | Where it is proved |
|---|---|
| Schema shape, 9 accepted and 45 rejected fixtures | [test_coverage_manifest_v1.py](../../tests/test_coverage_manifest_v1.py) |
| Semantic and temporal cases 1–12 | [test_declared_coverage_semantics.py](../../tests/test_declared_coverage_semantics.py) |
| Cross-boundary cases 13–16, against the real `DataGateway` | [test_coverage_boundary_audit.py](../../tests/test_coverage_boundary_audit.py) |
| Supersession lineage (I10), partition existence/uniqueness (I13), precision (I3), zero-row bounds (I1), assertion identity (I14), dataset isolation (I15), overlap (§4) | [test_coverage_lineage_and_reconstruction.py](../../tests/test_coverage_lineage_and_reconstruction.py) |
| Fixtures for cases 1–7 | `fixtures/coverage-manifest-v1/valid-*.json` |

Three pairings carry most of the proof.

Cases 3 and 4 of the semantics suite share identical events, identical observed
bounds and identical partitions, and reach opposite verdicts — coverage is
decided by evidence, not by event spacing.

Case 15 of the boundary suite reads the same catalog row twice, once as `valid`
and once as `degraded`, and gets byte-identical `eligible_coverage`. That is the
proof behind I9.3: no lifecycle state can express partial coverage, so the outer
span may never be published.

The lineage suite's I5 case reads the same partition manifest's observed event
bounds before and after an authoritative zero-record repair and finds them
byte-identical, while the declared coverage goes from non-contiguous
(unpublishable) to complete — the proof that repairing knowledge and adding
records are different operations, with real events on both sides of the
repaired gap rather than an entirely empty day.

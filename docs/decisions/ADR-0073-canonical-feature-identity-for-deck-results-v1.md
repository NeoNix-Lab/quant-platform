# ADR-0073 - Canonical feature identity for deck results v1 (DG-M)

**Status:** ACCEPTED (design gate #454, PR #456; accepted by the maintainer 2026-10-18, issue #457)
**Date:** 2026-10-18

Governance issues #453, #454; acceptance #457. Amends ADR-0072 and ADR-0069 only where section 10 says so, and only when this ADR is
`ACCEPTED`.

## Context

The DG-K2 closeout (#409) recorded, as trigger 1, that the server passes the deck no feature identity, and that a design
gate opens when the maintainer names feature traceability as a requirement. The maintainer did so on 2026-10-18, with
**failed aggression** (Hypothesis 001 of `alpha-research`) as the first canonical definition. Verified against `main`
@ `ef14279`:

- `DatasetAdmittedInputAuthority.resolve` builds the sealed manifest with `definition_identities=()`, and ADR-0072 §1 records
  that choice ("raw trades have no representation definition"). The field is already part of the manifest's canonical
  payload (stored sorted), so the manifest digest and the `admission_id` already cover it.
- `GovernedResultImportService._validate_run_compatibility` already requires every manifest definition identity to appear in the
  run's references (`feature_identities`, research labels, strategy/policy/execution, validation, environment). With an empty set
  it checks nothing. The deck-side builder (`build_deck_result`) goes further and *adds* a reference for every manifest definition
  the workload did not name (ADR-0072 §5 item 2: "covers `definition_identities`"), so even a non-empty set could not fail.
- The failed-aggression observable needs open, high, low, close and the **taker buy and sell volume of the same bar**. `CandleRecord`
  carries OHLC, total `volume` and `trade_count`; `FootprintBucket` carries taker volume per price level but not OHLC, and requires a
  tick grid that every trade price must satisfy exactly (`FootprintGridError` otherwise). No governed representation provides both.
- The platform has the generic pieces: `FeatureDefinition v1` (E02, ADR-0026), `EventSpec` and detection (F02), the footprint-based
  H01 imbalance definitions (ADR-0035) and their exact-decimal representation discipline. None defines the failed-aggression observable.
- `trade-v1` carries `aggressor_side`, and the server delivers verified `trades@1` partitions only (ADR-0072 §1); the deck recomputes
  bars and features locally with the platform library at the server's commit (ADR-0072 §5).
- Reported by the research worker session, not re-verified here: `alpha-research` holds three divergent definitions of Hypothesis 001
  and builds its own bars with taker volumes. The rule it now freezes as v1 is quoted in section 4.

The gate decides what is canonical, what the server carries, and what the K13 check then means. It builds nothing.

## Decision

### 1. Decomposition: one canonical feature; the event is not server-known

Failed aggression is **one `FeatureDefinition`**: the per-bar observable `order_flow.failed_aggression@1`, with the signed value
described in section 4. The **event** ("a bar whose signal is non-zero") is *not* a second server-known definition:

- an `EventSpec` is identity-bound to a `hypothesis_id` (F02), which is a research object the platform does not own, so an event
  definition cannot be canonical independently of a hypothesis;
- the server's carriage (section 5) is of feature and representation definitions, which K13 already compares; an `EventSpec`
  identity is not among the references K13 inspects.

The canonical way to detect events over the feature is the existing `EventSpec`/`ThresholdPredicate` over this observable:
`equal 1` (trapped buyers), `equal -1` (trapped sellers), `not_equal 0` (either). Those predicates are *reference usage*,
exercised by a test, not a new identity domain.

What bears identity and what does not:

| Part | Identity-bearing in | Notes |
| --- | --- | --- |
| Bar duration, taker-volume aggregation | `bar-flow-definition-v1` (section 2) | Representation parameter, as in ADR-0035 §3 |
| Imbalance threshold, wick ratio, the rule | `feature-definition-v1` of `order_flow.failed_aggression@1` | Section 4; defaults `0.3`, `0.5` |
| Hypothesis, forward horizons, costs, sessions, sample sizes, falsification criteria | the research objects (`HypothesisSpec`, `OutcomeSpec`, study) | Study parameters; never in a definition |

Changing a threshold yields a **different definition identity** under the same key and version (as `imbalance_ratio` does for
H01, ADR-0035 §4). That is intended: a result computed with `0.25` is a different attribution than one with `0.3`.

### 2. Input: a new additive representation, `bar-flow-v1`

Options considered, and why the third is chosen:

| Option | Verdict |
| --- | --- |
| Add taker volumes to `CandleRecord` | Rejected: changes `candle-v1` bytes and `CandleDefinitionV1` identity (fixed input 5). |
| Compose a candle with the sum of footprint levels | Rejected: needs a tick size that every price in the history satisfies exactly, so an unrelated grid assumption can refuse a bucket; two passes over the trades with two evidence trails that must be proven to align. |
| **A sibling representation of the candle with two more aggregates** | **Chosen:** one pass over the same trades, the candle's alignment, ordering, availability and exact-decimal rules, no grid. |

`BarFlowDefinitionV1(duration_ns)` has the candle's payload with these differences, all stated:

```text
representation_kind  = "bar_flow"
output_record_schema = "bar-flow-v1"
aggregation          = the candle's six aggregates, plus
                       taker_buy_volume  = sum_exact_decimal_size_where_aggressor_side_buy
                       taker_sell_volume = sum_exact_decimal_size_where_aggressor_side_sell
aggressor_side       = accepted [buy, sell]; missing -> refuse_bucket; unknown -> refuse_bucket   (ADR-0027 "Aggressor side")
identity             = "bar-flow-definition-v1:sha256:<hex>" over the sorted payload, profile sorted-compact-utf8-v1
```

`bar-flow-v1` record: `bucket_start`, `bucket_end`, `open`, `high`, `low`, `close`, `volume`, `trade_count`, `taker_buy_volume`,
`taker_sell_volume` (all strings, exact decimal). Invariant, enforced when a record is built: `taker_buy_volume + taker_sell_volume ==
volume` and both are non-negative. Everything else (boundary `[start,end)`, `utc_epoch` alignment offset 0, `trade-v1`
`exchange_ts` as the event time, `TRADES_CANONICAL_TOTAL_ORDER_V1`, `empty_bucket_policy: omit`, `late_event_policy:
revisioned_rebuild`, `numerical_semantics: exact_decimal_no_rounding_v1`) is the candle's.

**No existing identity byte changes.** The new domain `bar-flow-definition-v1` is disjoint from every existing one. The guard test
pins the identities that must stay as they are:

| Identity | Value (must not change) |
| --- | --- |
| `CandleDefinitionV1("1m")` | `candle-definition-v1:sha256:94b3a5a99c873f0d2805f4d197b772047f8c13e8753ae8fb4314aafebf1440cc` |
| `FootprintDefinitionV1("1m", "0.1")` | `footprint-definition-v1:sha256:3f551c3e5676985a942acda5d398843ae0145cae4b3212cd1b4817fed7d1fca5` |
| H01 diagonal imbalance, defaults | `feature-definition-v1:sha256:53e24318c041359d9ce5f52f82718b8b85e3aee305856619cb5cd265cd996de7` |
| H01 stacked imbalance, defaults | `feature-definition-v1:sha256:7666e798a7db6d4bf7cc7c76c952c175a05b9a5e712be00916bde7d4c4f64167` |

The duration is bounded to **1 second to 1 day** inclusive for `bar-flow-v1` (a bar longer than a UTC day would need an unbounded
number of partitions). Any spelling `parse_duration_ns` accepts is valid; identity uses the nanosecond integer, so `"1m"` and
`"60s"` are the same definition.

### 3. Availability and finality

Per ADR-0026 and the candle/footprint precedent: a `bar-flow-v1` bar is `CLOSED` only with source finalization evidence; its
**causal floor is `bucket_end`**; partial consumption is forbidden; the feature consumes **FINAL (closed) bars only**
(`input_maturity = FINAL_ONLY`). The feature has no warm-up: `support = current()`, `minimum_history = 0`, `initialization.rule =
none`. An omitted bucket (no trades) yields **no observation**; absence is never a `0` signal, and a consumer must not treat it as
one. The feature's `causal_available_at` is the bar's `bucket_end`; `observed_available_at` and `observed_finalized_at` are the bar's
source evidence, never fabricated (ADR-0026).

### 4. The feature: `order_flow.failed_aggression@1`

```text
feature_key      = "order_flow.failed_aggression"
semantic_version = "1"
input_contract   = bar_flow.bar @ 1, atomic,
                   required_observables = close, high, low, open, taker_buy_volume, taker_sell_volume
support          = current()            input_maturity = FINAL_ONLY
availability     = default (after_required_support_available)    finality = default (all_required_support_final)
initialization   = none, minimum_history 0
output_contract  = NUMERIC, shape "failed_aggression_signal_v1", dimension DIMENSIONLESS, numerical equivalence EXACT v1
parameters       = imbalance_threshold  decimal, default "0.3", domain 0 < x <= 1
                   wick_ratio           decimal, default "0.5", domain 0 <= x < 1
```

The two domains are enforced by the canonical constructor, as ADR-0035 §4 does for `imbalance_ratio > 1` where the generic E02
parameter bound is inclusive only. The defaults are the rule `alpha-research` freezes as Hypothesis 001 v1 (`min_imbalance_ratio
0.30`, `min_wick_ratio 0.50`).

For a bar with open `O`, high `H`, low `L`, close `C`, taker buy volume `B`, taker sell volume `S`, with `T = B + S` and `D = B - S`,
`i = imbalance_threshold`, `w = wick_ratio`, the value is the integer:

```text
+1  (taker buyers trapped)   if  D >= i * T   and   (H - max(O, C)) >  w * (H - L)   and   C < O
-1  (taker sellers trapped)  if -D >= i * T   and   (min(O, C) - L) >  w * (H - L)   and   C > O
 0  otherwise
```

The two conditions are exclusive because `i > 0` makes the signs of `D` differ. The sign convention is the direction of the
*failed aggression* (`+1` = the aggressive side was buying). It is not a trading direction; the hypothesis decides what to do with it.

**Numerics (exact, division-free).** Every operand is an exact decimal taken from the `bar-flow-v1` record; the products
`i * T` and `w * (H - L)` and the differences are computed in exact decimal arithmetic with no rounding, no float and no division
(`exact_decimal_no_rounding_v1`). The imbalance ratio `I = D / T` of the research notation is equivalent to `D >= i * T` because
`T > 0` in every emitted bar. Comparisons are `>=` for the imbalance and `>` for the wick, as the frozen research rule has them. A
bar with `H == L` has a zero range, so the wick comparison is false and the value is `0`. This differs deliberately from ADR-0035 §9
(exact decimal converted to float for the E05 kernel): a new feature gets exact arithmetic and no float seam.

**Evaluation seam.** As in ADR-0035 §1, the Feature package owns the definition constructor and a pure evaluator over a
caller-supplied bar shape; Application adapts the `bar-flow-v1` record into that shape. The evaluator requires the definition it is
given to be the canonical one for its parameters (`definition_id` equality) and refuses otherwise.

### 5. Server carriage: the deck names, the server canonicalizes and seals

**Where the server's known definitions live.** A **closed registry in code** (`application`), not a catalog table. A
`FeatureDefinition` is code plus parameters (ADR-0016); the catalog's `feature_set_definitions` table holds *feature-set*
definitions (a bundle/materialization definition), a different object, and nothing persists single definitions. v1 has one entry,
`order_flow.failed_aggression` version `1`, which declares the representation it needs (`bar_flow` version `1`), its two parameter
names and its dataset requirement (`trades`, `trade-v1`). The H01 definitions are **not** in the registry (they need footprint
buckets); adding an entry later is a code change at the deck-pinned commit and needs no wire change.

**The request.** `admit` gains one **optional** field, `definitions` (array, at most **8** entries, default absent = none):

```json
{"feature_key": "order_flow.failed_aggression", "semantic_version": "1",
 "bar_duration": "1m",
 "parameters": {"imbalance_threshold": "0.3", "wick_ratio": "0.5"}}
```

`bar_duration` is required for an entry whose registry row needs a `bar_flow` input, forbidden otherwise. The requested
interval must be aligned to it (see `definition_interval_misaligned`), so that every bar the deck builds over the delivered
partitions is complete and none is a partial bucket. `parameters` values are
decimal strings (or integers for integer parameters); an omitted parameter takes the definition's default. Nothing locator-shaped is
accepted, as before. `definitions` is allowed only with a `dataset` selector.

Optional fields are additive under ADR-0066 §1 ("New families and optional fields are additive only; a change to a required field or
to the meaning of an accepted family/version requires a new family version"). `j02-admitted-input-v2` and its request version stay as
they are: a request without `definitions` is handled exactly as today, and a server that predates this field refuses a request that
carries it as `invalid_request` (the closed field set), which is the intended fail-closed behaviour for a deck pinned to the
server's commit (ADR-0072 §5). A parallel `v3` family with identical semantics was rejected as cost without benefit.

**Resolution.** For each entry the server builds, with its own library at its own commit, the `BarFlowDefinitionV1(bar_duration)` and the
`FeatureDefinition` for the key, version and parameters, and takes their identities. The manifest's `definition_identities` is the
**sorted, deduplicated list of those identity strings**: for each entry the `bar-flow-definition-v1:sha256:…` and the
`feature-definition-v1:sha256:…`. Two entries that canonicalize to the same pair are `definition_duplicate`. Nothing else in the
manifest changes: `logical_input_identities`, partitions, hashes, `git_identity` and the rest are as ADR-0072 §1 has them.

**Effect on identities.** `definition_identities` is already in the manifest's canonical payload, so:

- a request **without** `definitions` seals a manifest byte-identical to today's (empty list), hence the same `manifest_digest` and
  `admission_id`, and every existing admission stays valid; M04 must prove this with the digest of a pre-change manifest;
- the same dataset and interval admitted with and without definitions are **different admissions**, and with different definitions
  or parameters are different admissions again. That is intended: the admission is what a result bundle names, so the
  attribution is part of the evidence the bundle is bound to (ADR-0072 §11: a bundle names one admission).

**Refusals.** All are `invalid_request` with a stable `reason` of letters and underscores (the public projection of ADR-0069
Amendment 1), and nothing is sealed:

| `reason` | When |
| --- | --- |
| `definitions_exceed_bound` | more than 8 entries |
| `definition_unknown` | `feature_key` and `semantic_version` are not a registry entry |
| `definition_parameters_invalid` | an unknown parameter name, a non-decimal value, or a value outside the declared domain |
| `definition_bar_duration_invalid` | missing where required, present where forbidden, unparsable, or outside 1 second to 1 day |
| `definition_interval_misaligned` | an entry with a `bar_duration` and a requested interval whose start or end is not a multiple of that duration since the UTC epoch |
| `definition_duplicate` | two entries canonicalize to the same identities |
| `definition_input_unavailable` | the dataset is not the registry row's required dataset, or `definitions` accompanies `artifact_identities` |

**What the server attests.** It attests that it *admitted a request that named these definitions*, in a manifest it sealed. It does
not evaluate, import or execute the deck's feature code (ADR-0069 §7: attribution evidence only); the binding between the name and
the computation is the deck's clean tree pinned to the server's commit (the canonical constructor and evaluator are the library code
of that commit), the explicit declaration of section 6, and the replay provider's code identity. The definition's *identity*
covers its semantic payload, not the evaluator's behaviour; the evaluator's behaviour is held by the golden vectors of M02 and by
the commit pin.

### 6. K13 and the deck builder

`_validate_run_compatibility` is **unchanged**: the manifest's definition identities must be a subset of the run's references,
compared by identity string. With a non-empty manifest set the check now has content.

The deck builder's behaviour changes (an amendment, section 10): for definition identities it **no longer adds** a reference the
workload did not name. A manifest definition the workload did not declare is a `DeckResultError` with reason
`definition_not_declared`, so the workload must state, in `feature_identities`, the identities of the definitions it evaluated. The
reference kind is `feature-definition` for a `feature-definition-v1` identity and `representation-definition` for a
`bar-flow-definition-v1` identity (the kind is not compared by K13; it is for readers and tooling). Data identities keep their
auto-coverage rule (`LOGICAL_INPUT_REFERENCE_KIND`), which this ADR does not touch.

Behaviour for runs and manifests that name no definition: a manifest with an empty set and a run that declares none is exactly
today's flow, so every existing deck workload keeps working unchanged. A run that declares **more** definitions than the manifest
admitted is not refused (K13 verifies coverage, not exclusivity); only the admitted ones are server-attested, and that is stated as
a limit, not hidden.

### 7. Research alignment

`alpha-research` consumes the canonical definitions from `quant_platform` at the server's commit and **retires its local copies**:
its bar construction with taker volumes, `detect_failed_aggression`, and the divergent Hypothesis 001 texts, keeping a single
equivalence test against the canonical evaluator until they are removed. What stays research-local: `HypothesisSpec` (including the id
"001"), the `EventSpec` bound to it (section 1), `OutcomeSpec` (horizons, cost allowance, path extrema), session windows, the
falsification criteria and the notebooks. The research repository states which platform commit it uses; the existing S05 rule that a
deck result names one server commit applies (issue #441 covers a provider living in a repository of its own and is not decided here).

### 8. Proof: what "the server passes feature identity" means

End to end, four observable facts, each with its proof:

1. `admit` with `definitions` returns and re-serves (`manifest`) a sealed manifest whose `definition_identities` equal the
   identities the library computes for the same names and parameters (hermetic: transport and authority tests).
2. `deliver` and `acknowledge` are unchanged for that admission (hermetic).
3. `submit` registers a bundle whose run declares the identities, and **refuses with zero writes** a bundle whose run omits any of
   them (`PostgresSplitTopology`, the real topology: the deck-side build of that bundle fails first with `definition_not_declared`,
   and a hand-built bundle that omits them is refused by K13).
4. The registered run record carries the identities (read back from the experiment store).

M04 and M05 reuse `tests/split_topology_proof_v1.py` for 3 and 4. A real-host golden step (a deck on the real server computing the
feature over real delivered trades and registering the result) is **recorded evidence to be produced by the operator**, not claimed
by this ADR.

### 9. What this does not need

- **No generic provider extension (`E07`) and no plugin framework:** the registry is a closed in-code table; evaluation runs in the
  deck with the platform library, as all deck work does.
- **No server-side feature materialization and no precomputed features as deck inputs:** the server still delivers verified trades
  only (ADR-0072 §1); accepted-artifact inputs stay `artifact_inputs_unavailable`.
- **No change to any existing identity:** section 2's pinned table; the new identity domains are `bar-flow-definition-v1` and new
  `feature-definition-v1` content under a new key.
- **No new transport family and no credential change.**

### 10. Amendments to accepted ADRs (effective only on acceptance of this ADR)

Under the ADR README's amendment policy these are **dated, append-only amendments**; the original prose is not rewritten.

| ADR | Original text | Amendment |
| --- | --- | --- |
| ADR-0072 §1 table (Amendment 2) | `definition_identities` — "empty (raw trades have no representation definition)" | empty unless the `admit` request names definitions (ADR-0073 §5); then the sorted identities of the canonicalized representation and feature definitions |
| ADR-0072 §5 item 2 (Amendment 2) | the builder "covers `definition_identities`" | the builder requires the workload to *declare* each manifest definition and refuses with `definition_not_declared` (ADR-0073 §6); data identities are unchanged |
| ADR-0069 §5 `admit` (Amendment 3; its Amendment 2 is J13's, issue #339) | a semantic input request: a dataset selector, or accepted artifact identities, plus the operation/profile identity | additionally an optional `definitions` array (ADR-0073 §5), additive under ADR-0066 §1 |

Each amendment section is appended by the implementation issue that changes the behaviour (M04 for the request and the manifest
row, M05 for the builder), not by the acceptance of this ADR, so that no accepted text is amended ahead of its code. Until then the
ADR-0072 and ADR-0069 texts stay as written and this ADR is the pending decision that supersedes those two sentences.

## Atom disposition (for the reconciliation that follows acceptance)

| Registered | Disposition |
| --- | --- |
| `M01` Bar representation with taker volumes | stands: `bar-flow-v1` and `BarFlowDefinitionV1` (section 2), with the pinned-identity guard |
| `M02` Taker-imbalance FeatureDefinition | **renamed** `order_flow.failed_aggression@1`: definition, constructor, exact evaluator, golden vectors, and the reference `EventSpec` test of section 1 |
| `M03` Failed-aggression event definition | **superseded**: not a server-known definition (section 1); its reference-usage test moves into `M02` |
| `M04` Server registry and carriage | stands: registry, `definitions` field, refusals, manifest population (sections 5 and 8 facts 1-2) |
| `M05` Deck cross-check and proof | stands: builder declaration rule, K13 now non-vacuous, topology proof (sections 6 and 8 facts 3-4) |

Order: `M01` → `M02` → `M04` → `M05`. The atom table and counts change only through a governance reconciliation; this ADR does not edit them.

## Consequences

- A deck result is attributable to server-admitted, canonical definitions, including the bar duration, by identity, and a result
  that does not declare them cannot be built or registered.
- Parameter sweeps are different definitions. Each is a distinct identity and a distinct admission; there is no shared "family"
  identity in v1. Trigger to revisit: a study that needs to relate many parameterizations under one name.
- The server code gains a registry and a request field; the deck pin to the server's commit (ADR-0072 §5) is what keeps both sides'
  constructors identical.
- The attestation is of *name*, not of *computation* (section 5); a stronger claim needs the server to execute or re-derive the
  feature, which this ADR excludes.

## Not claimed

- Nothing here is implemented; the atoms are authorized only by a later reconciliation after this ADR is `ACCEPTED`.
- The feature is not shown to be profitable or even correct against real data beyond the golden vectors M02 defines; Hypothesis 001's
  empirical results are research evidence, not platform evidence.
- The 1 second to 1 day duration bound, the bound of 8 definitions per `admit` and the two parameter domains are design choices for
  the maintainer to accept; none was derived from a measurement.
- Throughput of bar-flow construction over the full delivered history, and the cost of a full-history feature run on the deck, are
  not measured.

## Acceptance evidence required of the implementation

Per atom, in its own issue: `M01` the pinned identities unchanged and `taker_buy + taker_sell == volume` over a mutation check;
`M02` golden vectors for both signs, the exclusivity, `H == L`, the exact boundary cases (`D == i*T`, wick `== w*range`), and the
parameter-domain refusals; `M04` a pre-change manifest digest unchanged, every refusal reason (including a misaligned interval), and the sorted/deduplicated
population; `M05` the four facts of section 8 on the real topology, with the omission case refused and zero writes asserted.

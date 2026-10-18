# Scope: DG-M — Canonical feature identity for deck results v1

Status: **OPEN — design gate CLOSED, implementation of `M01`, `M02`, `M04` and `M05` AUTHORIZED (governance issue #458, 2026-10-18).** The design gate #454 produced ADR-0073 (PR #456), `ACCEPTED` by the maintainer on 2026-10-18 (issue #457, PR #459). The scope id `DG-M`, the integration branch `implement/fid` (cut from `main` @ `ef14279`) and the atom ids are the maintainer's confirmed choices. `M03` is superseded by the ADR. No atom is implemented yet and **the server still passes no feature identity** until `M04` and `M05` are merged and proven.

Scope kind: a design-gate sequencing scope that, after the gate closed, carries the Part B authorized inventory below.

---

## Historical scope archive

This file previously recorded **DG-C / A14 — L2 contract, continuity and publication v1** (closeout #442 on `implement/l2`, promoted to `main` by PR #446; two-parent reconciliation #444) and, inherited in it, the **DG-K2 closeout (#409)**. Both are preserved in git history (`git log -- SCOPE.md`) and are not duplicated here, except for the facts this scope depends on. `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Why this scope exists

The DG-K2 closeout (#409) recorded, as trigger 1, that the server passes the deck no feature identity, and that a design gate opens when the maintainer names feature traceability as a requirement. The maintainer did so on 2026-10-18: a deck result must be attributable to a server-known, canonical feature definition, the first being **failed aggression** (Hypothesis 001 of `alpha-research`). On `main` @ `ef14279` the sealed manifest carries `definition_identities=()`, K13's coverage check therefore has nothing to compare, the deck builder adds definition references itself, and no governed representation carries taker volume together with OHLC. ADR-0073 is the answer; this scope sequences its implementation.

---

## Fixed inputs (already decided; not reopened)

1. **ADR-0057 and ADR-0064:** the server is the sole authority for canonical data; the deck has no path or storage mount.
2. **ADR-0069, ADR-0072 and Amendment 1:** the deck handoff families, the deck pinned to the server's commit, the sealed manifest whose digest is the admission identity.
3. **ADR-0066 §1:** optional fields are additive; a change to a required field needs a new family version.
4. **ADR-0026, ADR-0027 and ADR-0035:** availability, causal floor and exact-decimal discipline; definitions are separate from sets and artifacts.
5. **ADR-0073** (accepted): the decisions in the Part B acceptance below.
6. No existing identity bytes change (candle, footprint, H01 and feature identities are pinned in `tests/test_canonical_feature_identity_contract_v1.py`).
7. **Out of this scope:** the generic provider extension `E07` and the technical-feature line (DG-L / FEI, ADR-0071 on `implement/fei`, independent); precomputed feature artifacts as deck inputs (accepted-artifact inputs, ADR-0072 §1); Omega; `J11`.

---

## Design gate (closed)

| Gate | Issue | Output | Result |
|---|---|---|---|
| **DG-M** | #454, #457 | ADR-0073 (`docs/decisions/ADR-0073-canonical-feature-identity-for-deck-results-v1.md`) with its guard test | `ACCEPTED` 2026-10-18 (PR #456 proposed it, PR #459 accepted it); all eight questions answered |

Maintainer-accepted values that were not derived from a measurement (ADR-0073 "Not claimed"): the defaults `imbalance_threshold` 0.3 and `wick_ratio` 0.5 as parameters of the definition; the bar duration range 1 second to 1 day; at most 8 definitions per `admit`; the requested interval aligned to the bar duration; no family identity across parameterizations.

## Part B — Authorized implementation inventory

Each atom is its own implementation issue (`[implementation]` template), opened after this reconciliation merges, citing its row here. Every one targets the integration branch below, never `main`.

**Integration branch:** `implement/fid` (exists; atom branches are `agent/issue-<n>-<slug>`).

| Atom | Result | Requires (besides ADR-0073) | Start gate |
|---|---|---|---|
| `M01` | Bar-flow representation: `BarFlowDefinitionV1`, `bar-flow-v1` | `D03`, `D06` | startable |
| `M02` | `order_flow.failed_aggression@1`: definition, constructor, evaluator, vectors, reference `EventSpec` test | `M01`, `E02`, `E06` | after `M01` is merged |
| `M04` | Server registry, optional `definitions` field of `admit`, populated `definition_identities` | `M01`, `M02`, `S01`, `P06` | after `M01` and `M02` are merged |
| `M05` | Deck builder declaration rule, K13 made non-vacuous, four-fact topology proof | `M04`, `S05`, `S07` | after `M04` is merged |
| `M03` | Failed-aggression event definition | — | **superseded** (ADR-0073 §1); not an atom of the inventory |

```text
M01 ──> M02 ──> M04 ──> M05        (M03 superseded)
```

Order: strictly `M01`, `M02`, `M04`, `M05`, one bounded mutation slice at a time; each atom depends on the previous one.

### Acceptance per atom (from ADR-0073; an implementation issue adds no semantic decision)

- **`M01`** (ADR-0073 §2, §3). `BarFlowDefinitionV1(duration_ns)` with the candle's alignment, ordering, availability and exact-decimal rules and the two taker aggregates; the payload and the identity `bar-flow-definition-v1:sha256:<hex>`; `aggressor_side` missing or unknown refuses the bucket; duration 1 second to 1 day (spelling-independent identity); the record `bar-flow-v1` with `taker_buy_volume + taker_sell_volume == volume`; empty buckets omitted; closure needs source finalization evidence, causal floor `bucket_end`. The four pinned identities (candle, footprint, H01 diagonal, H01 stacked) unchanged, proven by the guard test and a mutation check of the invariant. Reuse of candle internals is the implementation's choice; no candle or footprint byte may change.
- **`M02`** (§1, §3, §4). The feature `order_flow.failed_aggression@1` with input contract `bar_flow.bar@1` (`close, high, low, open, taker_buy_volume, taker_sell_volume`), `FINAL_ONLY`, `support = current()`, no warm-up, output `NUMERIC` shape `failed_aggression_signal_v1` with exact equivalence; parameters `imbalance_threshold` (default `0.3`, domain `0 < x <= 1`) and `wick_ratio` (default `0.5`, domain `0 <= x < 1`) enforced by the canonical constructor; the exact division-free evaluator with the boundary cases of the ADR (`D == i*T` counts, wick `== w*range` does not, `H == L` gives 0, directions exclusive) and golden vectors for both signs; the evaluator refuses a non-canonical definition; an omitted bar yields no observation; a test running `EventSpec`/`ThresholdPredicate` (`equal 1`, `equal -1`, `not_equal 0`) over the observations. No float, no division.
- **`M04`** (§5, §8 facts 1-2). A closed in-code registry with one entry; the optional `definitions` field of `admit` (at most 8 entries; `feature_key`, `semantic_version`, `bar_duration`, `parameters`); resolution with the library at the server's commit; `definition_identities` = sorted, deduplicated bar-flow and feature identities; a request without `definitions` seals a manifest with the digest and `admission_id` it has today (proven against a pre-change manifest); the eight refusal reasons `definitions_exceed_bound`, `definition_unknown`, `definition_parameters_invalid`, `definition_bar_duration_invalid`, `definition_interval_misaligned`, `definition_bar_duration_mixed`, `definition_duplicate`, `definition_input_unavailable` as `invalid_request` with nothing sealed, and the rule that all entries with a `bar_duration` share it, so the manifest holds at most one bar-flow identity (ADR-0073 Amendment 1, #469); `j02-admitted-input-v2` and its request version unchanged; `manifest` re-serves the identities and `deliver`/`acknowledge` are unchanged. The atom appends Amendment 2 to ADR-0072 (§1 table row) and Amendment 3 to ADR-0069 (§5 `admit`; its Amendment 2 is J13's).
- **`M05`** (§6, §8 facts 3-4). The deck builder stops adding definition references and refuses `definition_not_declared`; reference kinds `feature-definition` and `representation-definition`; data identities keep their auto-coverage; K13 `_validate_run_compatibility` is untouched; on `PostgresSplitTopology` the declared bundle registers and the registered run carries the identities, and the omission case is refused with zero writes asserted; a manifest and a run without definitions behave exactly as before. The atom appends Amendment 2 to ADR-0072 (§5 item 2). The result of a real-host run is operator evidence, not part of this atom's acceptance.

---

## Authority

- `AGENTS.md`
- governance issues #381, #409, #453, #458; design-gate issues #454, #457; the DG-K2 closeout trigger 1
- `docs/decisions/ADR-0073-canonical-feature-identity-for-deck-results-v1.md`
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`M` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-M)
- ADR-0016, ADR-0026, ADR-0027, ADR-0034, ADR-0035, ADR-0057, ADR-0064, ADR-0066, ADR-0069, ADR-0072 (with Amendment 1)
- `tests/test_package_boundaries_v1.py`, `tests/test_canonical_feature_identity_contract_v1.py`

Accepted ADRs and frozen contracts remain normative. This reconciliation weakens none of them.

---

## Baseline and Credited State

```text
main @ ef14279 (DG-K P01-P06 and DG-K2 S01-S07 COMPLETE within recorded bounds; DG-C/A14 v1 promoted from implement/l2)
implement/fid: main + #455 (registration) + #456 (ADR-0073 proposed) + #459 (ADR-0073 accepted) + this reconciliation
DG-M: M01, M02, M04, M05 FROZEN / MISSING (authorized by #458); M03 superseded
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Explicit non-claims

- No `M` atom is implemented; the server passes no feature identity, no governed representation carries taker volume with OHLC, and the failed-aggression observable has no code.
- Nothing shows the feature is correct on real data beyond the golden vectors `M02` defines, or profitable: Hypothesis 001's empirical results are research evidence.
- The throughput of bar-flow construction over the full delivered history and the cost of a full-history feature run on the deck are not measured.
- The server attests the admitted *name* of a definition, not the deck's computation.
- The generic provider `E07`, the FEI line, precomputed feature artifacts as deck inputs, Omega and `J11` are untouched.

---

## Mutation policy

- Only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`. Implementation issues leave them untouched and report stale governance in the PR.
- Each atom changes only what its acceptance names; `M04` and `M05` may append Amendment 2 to ADR-0072 and Amendment 3 to ADR-0069, as listed above, and change no other accepted ADR text.
- No atom changes an existing identity byte, gives the deck a storage mount, requires the generic provider framework or serves precomputed features.

---

## Exit criteria for this scope

- `M01`, `M02`, `M04`, `M05` implemented and merged into `implement/fid` within the acceptance above, each with its mutation-checked tests.
- A governance closeout issue credits each atom with its PR and bound, and records what is not claimed.
- The operator's promotion PR of `implement/fid` into `main`.
- A real-host run (a deck computing the feature over real delivered trades and registering the result) is operator evidence recorded separately; it is not claimed by this scope.

---

## Out of Scope

- the generic provider extension `E07`, a plugin framework, and the FEI technical-feature line;
- server-side feature materialization and execution, and precomputed features as deck inputs;
- changing any existing identity bytes;
- a credential service, a binary transport family, a host outside `tools/`;
- Omega remote use of ADR-0069 families; `J11`; L2 acquisition beyond A14 v1; `J07`/`J08`, `I06`/`I07`, second venue;
- a family identity across parameterizations, other registry entries (for example the H01 definitions), and retiring `alpha-research`'s local copies (that repository's own work, ADR-0073 §7).

---

## Stop / Escalation Conditions

Stop and report rather than implement or reconcile if:

- an atom needs a decision ADR-0073 does not state, or contradicts an accepted ADR;
- an atom would change an existing identity byte, give the deck a storage mount, require the generic provider framework or serve precomputed features;
- the atom order is not respected (a start gate above is not met);
- a measured value contradicts one of the maintainer-accepted values and the atom cannot proceed without choosing another.

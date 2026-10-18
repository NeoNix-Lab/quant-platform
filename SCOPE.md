# Scope: DG-M — Canonical feature identity for deck results v1

Status: **CLOSED — governance closeout, issue #475 (2026-10-18). Design gate closed by #454 and #457 (ADR-0073 `ACCEPTED` 2026-10-18; Amendment 1, issue #469). Part B: `M01`, `M02`, `M04` and `M05` implemented and merged into `implement/fid` within the bounds recorded under "Closeout result"; `M03` is superseded.** The server now seals the identities of the definitions a deck names in `admit`, and the deck builder must declare them; K13 is unchanged. **No real-host run is claimed**, and promotion of `implement/fid` into `main` is the operator's separate PR.

Scope kind: a design-gate sequencing scope that, after the gate closed, carried the Part B authorized inventory `M01`, `M02`, `M04`, `M05`. Every atom had its own implementation issue citing the governing ADR section and targeting the integration branch `implement/fid`; all are merged (see "Closeout result"). `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Historical scope archive

This file previously recorded **this same DG-M scope** while it was registered (governance #453), while its design gate was open (#454, #457), while its implementation was authorized (reconciliation #458, with the alignments #465 and #470) and, before it, **DG-C / A14** (closeout #442) and **DG-K2** (closeout #409). All are preserved in git history (`git log -- SCOPE.md`) and are not duplicated here, except for the facts this scope depends on.

---

## Closeout result (issue #475)

| Atom | Merged evidence (into `implement/fid`) | Bound of the credit |
|---|---|---|
| `M01` | PR #466 (issue #461) | `representation/bar_flow.py`: `BarFlowDefinitionV1(duration_ns)`, the `bar-flow-v1` record, `schemas/bar-flow-v1.json`, golden fixture `fixtures/bar-flow-definition-v1/golden-1m.json`. The candle, footprint and H01 identities are unchanged. |
| `M02` | PR #468 (issue #462) | `features/failed_aggression.py`: `order_flow.failed_aggression@1`, canonical constructor, exact division-free evaluator, golden vectors, reference `EventSpec` test. Review findings R1-R3 were fixed; R1 (observation identity across durations) is held at admission level by Amendment 1. |
| `M04` | PR #473 (issue #463) | `application/admitted_input_definitions.py` (closed registry, one entry), the optional `definitions` field of `admit` (at most 8), populated `definition_identities`, the eight refusal reasons, a transport check that the manifest carries exactly the requested identities. A request without `definitions` seals the pre-change digest (`eb2a55f5…3e1c`). Review finding R1 (a 40-digit wire ceiling) was fixed. |
| `M05` | PR #474 (issue #464) | `application/deck_result_bundle.py`: `definition_not_declared`, reference kinds `feature-definition` / `representation-definition`, data coverage unchanged, K13 unchanged. The four facts of ADR-0073 §8 on `PostgresSplitTopology` (CI; also run by the implementer, 18 of 18, in WSL on PostgreSQL 16). |
| Follow-on | PR #478 (issue #477) | `clients/deck/deck_handoff_client.py`: `admit(..., definitions=())` sends the entries as given (the body is unchanged without them), refuses a sealed admission without definition identities when definitions were requested, snapshots the entries so the check is about what was sent; runbook and compatibility rule updated; the split topology proof uses the client method. Closes trigger 3. Not an atom. |
| Amendments | ADR-0073 Amendment 1 (issue #469, PR #471); ADR-0072 Amendment 2 (`M04` §1, `M05` §5 item 2); ADR-0069 Amendment 3 (`M04`, §5 `admit`; its Amendment 2 is J13's) | One bar duration per admission and the eighth refusal reason; the manifest field and the builder rule; the optional `definitions` field. Corrections and alignments: #465 / PR #467, #470 / PR #472. |

Note: the body of issue #463 still says seven refusal reasons and "Amendment 2" for ADR-0069; this file governed and was followed.

## Not claimed

- No real-host run: no deck has computed the feature over real delivered trades and registered the result on the real server. That run is operator evidence (ADR-0073 §8).
- Nothing shows the feature is correct on real data beyond the golden vectors, or profitable: Hypothesis 001's empirical results are research evidence.
- The server attests the admitted *name* of a definition, not the deck's computation.
- Observation identity does not include the bar duration: observations from bars of different durations that start at the same instant, produced by separate library calls, have the same identity. Amendment 1 holds this at the admission level, not in the library (H01 has the same property).
- The kind of a declared definition reference is not enforced by the builder or by K13.
- The registry has one entry; the H01 definitions are not in it; there is no family identity across parameterizations.
- The throughput of bar-flow construction over the full delivered history and the cost of a full-history feature run on the deck are not measured; the defaults, the duration range and the bound of 8 are maintainer-accepted, not measured.
- `PostgresSplitTopology` runs on the CI's PostgreSQL 17 and cannot run on Windows (the catalog requires an absolute POSIX `abs_path`).
- The generic provider `E07`, the FEI line, precomputed feature artifacts as deck inputs, Omega and `J11` are untouched. Retiring `alpha-research`'s local copies is that repository's own work (ADR-0073 §7).

## Triggers recorded at closeout (each names a condition that reopens a decision; none is authorized by this closeout)

The eight triggers recorded, with their conditions, are in [`OPEN_DECISIONS.md`](docs/architecture/OPEN_DECISIONS.md#dg-m-v1-reopening-triggers-475); trigger 3 (the protocol client carrying `definitions`) was exercised and closed by #477 / PR #478 (governance #479), so seven remain: the real-host run; observation identity across durations; the reference kind; more registry entries and a family identity; naming versus computing; unmeasured values and costs; the proof environment.

---

## Authority

- `AGENTS.md`
- governance issues #381, #409, #453, #458, #465, #470, #475, #479; implementation follow-on #477; design-gate issues #454, #457; amendment issue #469; the DG-K2 closeout trigger 1
- `docs/decisions/ADR-0073-canonical-feature-identity-for-deck-results-v1.md` (`ACCEPTED`, with Amendment 1)
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`M` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-M)
- ADR-0016, ADR-0026, ADR-0027, ADR-0034, ADR-0035, ADR-0057, ADR-0064, ADR-0066, ADR-0069 (with Amendment 3), ADR-0072 (with Amendments 1 and 2)
- `tests/test_package_boundaries_v1.py`, `tests/test_canonical_feature_identity_contract_v1.py`

Accepted ADRs and frozen contracts remain normative. This closeout weakens none of them.

---

## Baseline and Credited State

```text
main @ ef14279 (DG-K P01-P06 and DG-K2 S01-S07 COMPLETE within recorded bounds; DG-C/A14 v1 promoted from implement/l2)
implement/fid: main + #455 (registration) + #456/#459 (ADR-0073) + #460 (reconciliation) + M01 #466 + M02 #468 + M04 #473 + M05 #474 + alignments #467, #471, #472 + this closeout
DG-M: M01, M02, M04, M05 FROZEN / COMPLETE within recorded bounds (closeout #475); M03 superseded
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Mutation policy

- Only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`. Implementation issues leave them untouched and report stale governance in the PR.
- No accepted ADR text is rewritten; later changes to ADR-0073, ADR-0072 or ADR-0069 are appended amendments.
- No change gives the deck a storage mount, changes an existing identity byte, requires the generic provider framework or serves precomputed features.

---

## Out of Scope

- the generic provider extension `E07`, a plugin framework, and the FEI technical-feature line;
- server-side feature materialization and execution, and precomputed features as deck inputs;
- changing any existing identity bytes;
- a credential service, a binary transport family, a host outside `tools/`;
- Omega remote use of ADR-0069 families; `J11`; L2 acquisition beyond A14 v1; `J07`/`J08`, `I06`/`I07`, second venue;
- a family identity across parameterizations, other registry entries (for example the H01 definitions), and retiring `alpha-research`'s local copies.

---

## Stop / Escalation Conditions

Stop and report rather than implement or reconcile if:

- a change needs a decision ADR-0073 does not state, or contradicts an accepted ADR;
- a change would alter an existing identity byte, give the deck a storage mount, require the generic provider framework or serve precomputed features;
- a trigger above is exercised without its own issue and, where it names one, its own design gate.

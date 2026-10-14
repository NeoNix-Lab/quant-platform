# Scope: DG-L — FEI Technical Feature Provider v1

Status: **Part A (design gate) OPEN — registered by governance issue #362; design gate #363 is HOLD until this registration is merged. Part B is a CANDIDATE inventory: nothing in it is authorized for implementation until the design-gate ADR is `ACCEPTED` and a later reconciliation authorizes it.**

Scope kind: this document has two parts. Part A is the DG-L design gate that decides how a composite technical feature (the FEI) is bound to representations, how its recursive quantities are bounded, and where its interpretation layer ends. Part B is the candidate implementation inventory that gate would make implementable. No implementation may start until its own implementation issue exists, cites the governing ADR section, and targets the DG-L integration branch.

---

## Historical scope archive

This file previously recorded **DG-K — Process Topology v1** (design gate closed by ADR-0068 and ADR-0069, #319; Part B `P01`-`P06` authorized). DG-K is a distinct line: it continues on `implement/dgk`, where its `SCOPE.md` is intact, and it is preserved in git history (`git log -- SCOPE.md`). `SCOPE.md` is overwritten in place for each governance reconciliation (repository convention); on `implement/fei` it records the FEI line only. Wave 8 and earlier scopes are likewise in history.

---

## Why this scope exists

The maintainer proposes the FEI: a multi-scale, Ichimoku-based composite observable (three cloud scales, static distance/thickness features, recursive dynamics, twist rhythm and polarity, and categorical axes). `PRODUCT.md` §5 names "Technical indicators" as a Feature Engine provider family (ADR-0003), but no technical provider exists and `E07` ("Generic provider extension") waits for "a real second provider". The FEI is that second provider. A validation of the proposal against accepted contracts (ADR-0003, -0006, -0009, -0013, -0021, -0026, -0034, -0037, -0051 and `CANDLE_DEFINITION.md`) found that it cannot be adopted as specified: index-based scale grouping versus UTC-epoch candles, unbounded recursive memory versus the `current/point/window` support model, floating-point arithmetic versus exact equivalence, value sentinels versus non-observation, a length-dependent tail clamp (replay ≠ live), and decision outputs (posture, urgency, corridor direction) inside a feature.

---

## Owner decisions (inputs the design gate must honor)

1. **FEI is a feature.** Like `h01_imbalance`, but composite: an explicit composite InputContract over per-scale cloud features. The gate decides only whether it is delivered as one composite `FeatureDefinition` or a `FeatureSetDefinition` of component definitions (ADR-0016, ADR-0035 precedent).
2. **Finite-window support.** Every recursive quantity has a declared window length, seed rule and warm-up as identity-bearing parameters. No new support primitive; ADR-0026 is not amended for this.
3. **Generic provider.** The FEI is delivered through the generic provider extension `E07`, without a plugin framework: the minimum seam the second provider needs.
4. **Distinct line.** DG-L is distinct work from DG-K. `SCOPE.md` is overwritten on `implement/fei`; DG-K stays intact on `implement/dgk`.
5. **Anchoring.** `implement/fei` was cut from `implement/dgk` @ `9db9637`. It is re-anchored on `main` immediately after DG-K is promoted to `main` and **before** `implement/fei` is promoted. No DG-L slice depends on DG-K code.

---

## Part A — Design gate (OPEN)

One design-gate issue, **#363**, produces one ADR (number assigned at acceptance) that states, without `[TBD]`:

1. **Binding.** Scales are epoch-aligned CandleDefinition representations at `D·m_s`, bound through a composite InputContract with `FINAL_ONLY`; index grouping is rejected; behavior on a missing/omitted bucket is declared.
2. **Boundary.** The provider emits observables and optionally categorical axes; posture, urgency and corridor direction/shift/target are not feature outputs.
3. **Finite window.** Window length, seed and warm-up per recursive quantity; results before warm-up are non-observations.
4. **Numerics.** The `NumericalEquivalence` kind, its tolerance and the minimum-history rule bounding the truncation residual for `m ∈ {1, 5, 30}`.
5. **Non-observation.** Mapping of WARMUP and evidence states (`REAL/SEEDED/NONE`) to non-observation or explicit enum values, and the `eqScore`/`direction` rule when a scale is unavailable.
6. **Parity.** Replay and incremental evaluation are identical for the same declared support; length-dependent tail clamps are forbidden.
7. **Calibration.** Threshold values and auto-tune are not part of the family contract; auto-tune is fold-contained (F06/F07) and any predictive claim requires ADR-0037 evidence.

Valid closes: an `ACCEPTED` ADR, or an explicit disposition keeping the FEI outside the platform with a recorded evidence trigger.

---

## Part B — Candidate DG-L implementation inventory

Provisional atoms, `OPEN_BLOCKING`/`MISSING` until the Part A ADR is `ACCEPTED`. Each later receives its own implementation issue; **this reconciliation opens none and authorizes none**.

| Atom | Capability | Governing authority | Requires | Sequencing note |
|---|---|---|---|---|
| `T01` | `ichimoku-cloud-v1`: single-scale causal cloud | Part A ADR; ADR-0026, ADR-0021 | `E02`, `E04`, `D02`, `D03`, `E07` | Start gate: HOLD until the Part A ADR is `ACCEPTED`. |
| `T02` | `fei-static-v1`: multi-scale static observables | Part A ADR; ADR-0026 | `T01` | HOLD until `T01` is merged. |
| `T03` | `fei-dynamics-v1`: finite-memory derivatives | Part A ADR | `T02` | HOLD until `T02` is merged. |
| `T04` | `fei-rhythm-v1`: twist rhythm, polarity, evidence state | Part A ADR | `T02` | HOLD until `T02` is merged; independent of `T03`. |
| `T05` | `fei-axes-v1`: categorical ZONE, TRAVEL, STATE, thinness | Part A ADR; ADR-0009, ADR-0051 | `T02`, `T03`, `T04` | HOLD until all three are merged. No decisions in outputs. |
| `T06` | FEI conformity proof (replay = incremental, range independence, reference vectors) | Part A ADR; ADR-0013 | `T05` | HOLD until `T05` is merged; reference vectors `[DA CONFERMARE]`. |

```text
E07 ─┐
T01 ─> T02 ─┬─> T03 ─┐
            └─> T04 ─┴─> T05 ─> T06
```

`E07` is re-statused by this reconciliation from `OPEN_DEFERABLE` to `OPEN_BLOCKING` under DG-L (its evidence trigger, a real second provider, is met by the FEI); its minimum seam is decided by the Part A ADR.

### Integration branch

`implement/fei` (exists; cut from `implement/dgk` @ `9db9637`). Atomic slices use `agent/issue-<N>-<slug>` from it and target it (`AGENTS.md`, Branching). Re-anchoring on `main` is an exit criterion (below).

---

## Authority

- `AGENTS.md`
- governance issue #362; design-gate issue #363
- ADR-0003, ADR-0006, ADR-0009, ADR-0013, ADR-0016, ADR-0021, ADR-0026, ADR-0034, ADR-0035, ADR-0037, ADR-0051
- `docs/contracts/CANDLE_DEFINITION.md`, `docs/contracts/CORE_CONTRACTS.md`
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`DG-L` section; `T` atoms; `E07`), `docs/architecture/OPEN_DECISIONS.md` (`DG-L`)

Accepted ADRs and frozen contracts remain normative. No DG-L issue may change an existing identity, CandleDefinition v1, FeatureDefinition v1 identity bytes, FeatureArtifact (E04) identity, or ADR-0026's support model.

---

## Baseline and Credited State

```text
implement/fei @ 9db9637 (cut from implement/dgk; Merge of PR #359)
main @ 731ae97
E02, E04, E06, D02, D03, D04: COMPLETE
DG-K: ADR-0068, ADR-0069 ACCEPTED; P01-P06 FROZEN (own line, implement/dgk)
DG-L: Part A OPEN (#363); T01-T06 OPEN_BLOCKING / MISSING; E07 OPEN_BLOCKING / MISSING
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records. `E02` FeatureDefinition v1, `E04` FeatureArtifact v1 and the `h01_imbalance` composition (`E06`) are reused as evidence and precedent, not rebuilt.

---

## Explicit non-claims

- `T01`-`T06` and the re-statused `E07` are decided nowhere yet: no ADR exists and no implementation issue is authorized.
- The FEI's predictive value is not claimed; the source specification carries no out-of-sample evidence, and its thresholds are tuned on one instrument and timeframe (BTC, 30 minutes).
- No new support primitive, plugin framework or provider registry is introduced by this reconciliation.
- DG-K, Wave 9 (`I06`/`I07`), Wave 10 (`J07`/`J08`), `A13`-`A15` and second venue are untouched.

---

## Mutation policy

- Each DG-L implementation issue (once authorized) produces code and tests for exactly one atom.
- Branch `agent/issue-<N>-<slug>` (or `codex/issue-<N>-<slug>`) from `implement/fei` and target it, not `main`.
- Do not mutate `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md` as part of implementation work (`AGENTS.md` Governance boundary). Governance PRs use a `governance/` or `admin/` head branch.
- Start gates in the Part B table are binding.

---

## Exit criteria for this scope

- Part A: the design-gate ADR is `ACCEPTED` (or closed with the recorded alternative disposition).
- Part B: each authorized atom is implemented and merged into `implement/fei`, within the acceptance its ADR states, followed by a governance closeout.
- **Re-anchoring:** after DG-K is promoted to `main` and before `implement/fei` is promoted, `implement/fei` is re-anchored on `main`, and the promotion diff contains only DG-L changes.
- Promotion of `implement/fei` to `main` is a closeout PR after governance reconciliation.

---

## Out of Scope

- POSTURE, urgency and the corridor's LONG/SHORT direction, shift (`corrShift`) and target: research label or `execution_policy` (ADR-0009, ADR-0051), not feature outputs;
- threshold auto-tune and any validation of the FEI's predictive value (a later validation issue after `T06`, under F06/F07 and ADR-0037);
- a plugin/provider framework beyond the minimum seam `E07` needs;
- changing CandleDefinition v1, FeatureDefinition v1 identity bytes or FeatureArtifact identity;
- a new support primitive (ADR-0026 is unchanged);
- DG-K implementation work, which stays on `implement/dgk`;
- live/paper execution, RL, second venue, `L1`/`L2`/`L3` depth.

---

## Stop / Escalation Conditions

Stop and report rather than implement or decide unilaterally if:

- a DG-L issue cannot satisfy the Part A ADR without weakening an accepted ADR or changing an existing identity;
- a DG-L issue starts before its start gate is met;
- a feature output would encode a trading decision (posture, urgency, direction) or a threshold-dependent action;
- a computation depends on array length, request-range start or seed state beyond its declared window and warm-up;
- implementing an atom would require answering a design question the Part A ADR leaves open.

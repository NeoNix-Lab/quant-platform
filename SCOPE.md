# Scope: DG-M — Canonical feature identity for deck results v1 (design gate)

Status: **OPEN — design gate only (governance issue #453, 2026-10-18). No implementation is authorized by this scope.** The single design-gate issue is #454 (one ADR, ADR-0073, for the decisions below). The scope id `DG-M`, the atom names `M01`-`M05` and the integration branch `implement/fid` (cut from `main` @ `ef14279`) are the maintainer's confirmed choices.

Scope kind: a Part A-style design-gate sequencing scope. It registers the open decisions, fixes the decisions already taken as inputs, and names the candidate atoms the gate must make implementable. A Part B implementation inventory is added only by a later governance reconciliation, after #454's ADR is `ACCEPTED` (or #454 closes with an explicit disposition and evidence trigger).

---

## Historical scope archive

This file previously recorded **DG-C / A14 — L2 contract, continuity and publication v1** (closeout #442 on `implement/l2`, promoted to `main` by PR #446; two-parent reconciliation #444) and, inherited in it, the **DG-K2 closeout (#409)**. Both are preserved in git history (`git log -- SCOPE.md`) and are not duplicated here, except for the facts this scope depends on. `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Why this scope exists

The DG-K2 closeout (#409) recorded, as trigger 1, that the server passes the deck no feature identity, and that a design gate opens when the maintainer names feature traceability as a requirement. The maintainer did so on 2026-10-18: a deck result must be attributable to a server-known, canonical feature definition, the first being **failed aggression** (Hypothesis 001 of `alpha-research`). Verified against `main` @ `ef14279`:

- `DatasetAdmittedInputAuthority.resolve` builds the sealed manifest with `definition_identities=()`. The deck-declared `feature-provider-v1` identity is attribution evidence only (ADR-0069 §7). `GovernedResultImportService._validate_run_compatibility` already requires the manifest's definitions to be covered by the run's references, but with an empty set it has nothing to check.
- `CandleRecord` carries OHLC, total `volume` and `trade_count`: no taker buy/sell volumes. `trade-v1` carries `aggressor_side`. A failed-aggression observation (imbalance ratio plus intra-bar retracement) needs buy and sell volume per bar, which no governed representation provides.
- The platform has the generic pieces: `FeatureDefinition v1` (E02, ADR-0026), artifact identity (E04, ADR-0034), the H01 imbalance precedent (E05/E06, ADR-0035) and `EventSpec`/detection (F02). None defines the failed-aggression observable.
- Reported by the research worker session, not re-verified here: `alpha-research` holds three divergent definitions of Hypothesis 001 and builds its own bars with taker volumes.

---

## Fixed inputs (already decided; the gate does not reopen them)

1. **ADR-0057 and ADR-0064:** the server is the sole authority for canonical data; the deck has no path or storage mount.
2. **ADR-0069, ADR-0072 and Amendment 1:** the deck handoff families, the deck pinned to the server's commit, the sealed manifest whose digest is the admission identity.
3. **ADR-0066 §1:** a change to a required field or to the meaning of an accepted family needs a new family version.
4. **ADR-0026 and ADR-0035:** availability and causal floor; definitions are separate from sets and artifacts; the H01 imbalance feature is the precedent.
5. No existing identity bytes change (candle, footprint and feature identities).
6. **Out of this scope:** the generic provider extension `E07` and the technical-feature line (DG-L / FEI, ADR-0071 on `implement/fei`, independent); precomputed feature artifacts as deck inputs (accepted-artifact inputs, ADR-0072 §1); Omega; `J11`.

---

## Design gate

| Gate | Issue | Output | Atoms it must make implementable |
|---|---|---|---|
| **DG-M** | #454 | One ADR (ADR-0073) for the decisions below, with its guard test | `M01`-`M05` |

Valid closes: an `ACCEPTED` ADR in which every closed set, name, bound and default is stated (no `[TBD]`, no `[DA CONFERMARE]`), or an explicit, recorded deferral with an evidence trigger. The ADR may not change existing identity bytes, give the deck a storage mount, require the generic provider framework or serve precomputed features.

### Candidate atoms (names provisional; final names are the design gate's call)

| Atom | Proposition | Requires |
|---|---|---|
| `M01` | Bar representation with taker volumes (additive; no existing identity changes) | DG-M, `D03`, `D06` |
| `M02` | Taker-imbalance `FeatureDefinition` v1 | `M01`, `E02`, `E06` |
| `M03` | Failed-aggression event definition v1 (`EventSpec`/detection) | `M02`, `F02` |
| `M04` | Server definition registry and admitted-input carriage: `admit` names definitions, the manifest carries `definition_identities`, family version | `M02`, `M03`, `S01`, `P06` |
| `M05` | Deck cross-check and proof: K13 coverage with real definitions, the deck workload declares them, real-topology proof | `M04`, `S05`, `S07` |

```text
M01 ──> M02 ──> M03 ──┐
          M02 ────────┼─> M04 ─> M05
```

### Question set (see `docs/architecture/OPEN_DECISIONS.md`, DG-M)

1. **Decomposition.** Failed aggression as a feature, an event or both; what bears identity and what is a study parameter.
2. **Input.** A new additive bar representation with taker volumes, a composition of candles and footprints, or a derivation from `trade-v1`.
3. **Availability.** Final bars only, the causal floor, coverage and warm-up.
4. **Numerics.** Exact arithmetic and rounding for the ratio and the retracement test.
5. **Server carriage.** How `admit` names definitions; the closed set the server knows and where it lives; the manifest's `definition_identities`, its effect on the manifest digest and the admission identity; the family version.
6. **K13.** The coverage rule, the refusal and its reason, runs that name no definition.
7. **Research alignment.** How `alpha-research` consumes the canonical definition; what stays research-local.
8. **Proof.** What shows the server passing feature identity end to end, hermetic and real topology.

---

## Authority

- `AGENTS.md`
- governance issues #381, #409, #453; design-gate issue #454; the DG-K2 closeout trigger 1
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`M` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-M)
- ADR-0016, ADR-0026, ADR-0027, ADR-0034, ADR-0035, ADR-0057, ADR-0064, ADR-0066, ADR-0069, ADR-0072 (with Amendment 1)
- `tests/test_package_boundaries_v1.py`

Accepted ADRs and frozen contracts remain normative. Registering this gate weakens none of them.

---

## Baseline and Credited State

```text
main @ ef14279 (DG-K P01-P06 and DG-K2 S01-S07 COMPLETE within recorded bounds; DG-C/A14 v1 promoted from implement/l2)
implement/fid cut from main @ ef14279
DG-M: M01-M05 OPEN_BLOCKING / MISSING (registered by #453); none authorized
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Explicit non-claims

- DG-M and `M01`-`M05` are decided nowhere and built nowhere; nothing here is authorized for implementation.
- The server passes no feature identity today, and no governed representation carries taker volumes.
- The generic provider `E07`, the FEI line, precomputed feature artifacts as deck inputs, Omega and `J11` are untouched.

---

## Mutation policy

- This scope is governance-only until the design gate closes: only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`.
- The design gate (#454) produces one ADR and its guard test and no code.
- Implementation issues are opened only after the gate's ADR is `ACCEPTED` and a later reconciliation authorizes the atoms; each then targets `implement/fid`, never `main`.

---

## Exit criteria for this scope

- Part A: #454's ADR `ACCEPTED`, reconciled by a later governance issue.
- Part B (after that reconciliation): `M01`-`M05` implemented and merged into `implement/fid` within the acceptance the ADR states, followed by a governance closeout and the operator's promotion PR.

---

## Out of Scope

- the generic provider extension `E07`, a plugin framework, and the FEI technical-feature line;
- server-side feature materialization and precomputed features as deck inputs;
- changing any existing identity bytes;
- a credential service, a binary transport family, a host outside `tools/`;
- Omega remote use of ADR-0069 families; `J11`; L2 acquisition beyond A14 v1; `J07`/`J08`, `I06`/`I07`, second venue.

---

## Stop / Escalation Conditions

Stop and report rather than register or decide unilaterally if:

- a candidate atom cannot be stated without a semantic decision the design gate has not taken;
- the scope would change existing identity bytes, give the deck a storage mount, require the generic provider framework, or serve precomputed features;
- an implementation issue is proposed before #454's ADR is `ACCEPTED` and a reconciliation authorizes its atom.

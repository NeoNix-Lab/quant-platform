# Scope: DG-K2 — Server/deck deployment composition v1

Status: **CLOSED — operator closeout, issue #409 (2026-10-17). Design gate closed by #383 and #387 (ADR-0072 `ACCEPTED` 2026-10-16; Amendment 1, issue #431). Part B: `S01`-`S07` implemented and merged into `implement/dgk2` within the bounds recorded under "Closeout result". The hermetic proof ran (CI) and the supervised proof ran once on the real server (one happy path on the smallest partition); the topology is deployable only within the bounds of ADR-0072 Amendment 1 and with the findings recorded in `docs/engineering/DGK2_SPLIT_TOPOLOGY_PROOF.md`. Promotion of `implement/dgk2` into `main` is the operator's separate PR; `S08` is not authorized.**

Scope kind: a design-gate sequencing scope that, after the gate closed, carried the Part B authorized inventory `S01`-`S07`. Every atom had its own implementation issue citing the governing ADR section and targeting the integration branch `implement/dgk2`; all are merged (see "Closeout result"). `S08` stays deferred on its trigger.

---

## Historical scope archive

This file previously recorded **DG-K — Process Topology v1** (CLOSED — operator closeout, issue #381, 2026-10-16), and then **this same DG-K2 scope** while its design gate was open (governance issue #382) and while its implementation was authorized (reconciliation #389). All are preserved in git history (`git log -- SCOPE.md`). `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Closeout result (issue #409)

| Atom | Merged evidence (into `implement/dgk2`) | Bound of the credit |
|---|---|---|
| `S01` | PR #398 (issue #391) | `application/admitted_input_authority.py`: dataset selector only; manifest from the open scan metadata; whole-file in-memory delivery. |
| `S02` | PR #400 (issue #392) | `application/result_import_composition.py`: the six-role registry and the service factory; output and staging bounds not measured on a real output. |
| `S03` | PR #405 (issue #395) | `application/api_host_composition.py`, `tools/api_server.py`: opt-in families, mTLS, principals file; `jobs` composed without a result resolver. |
| `S04` | PR #414 (issue #393) | `infra/pki/`, `docs/engineering/DECK_PKI_OPERATIONS.md`; no CRL or OCSP. |
| `S05` | PR #418 (issue #394) | `application/deck_result_bundle.py`; the provider identity is attribution evidence; a bundle names one admission. |
| `S06` | PR #421 (issue #396) | `infra/systemd/` units, `docs/engineering/DECK_HANDOFF_RUNBOOK.md`, `docs/engineering/SERVER_DECK_COMPATIBILITY.md`; no memory limit on the API unit. |
| `S07` | PR #427 (issue #397) part 1, PR #428 the record of part 2 | Part 1: `PostgresSplitTopology`, 14 tests in the CI job of the runtime stores. Part 2: one happy path on the smallest real partition (3 122 bytes), executed by the maintainer and reported from the session log, on a pull-request commit. |
| Amendment | ADR-0072 Amendment 1 (issue #431, PR #432) | Delivery bounds raised to 512 MiB per file, 1 GiB and 16 files per admission, 1.5 GiB retained, 600 s; measured cost of a delivery recorded. |

Findings recorded by the proof (see `docs/engineering/DGK2_SPLIT_TOPOLOGY_PROOF.md`): 719 of the 2 116 daily partitions of the real dataset exceeded the original member bound (the largest is 252.8 MiB); the server environment lacked a declared dependency; the server checkout was moved by hand to a pull-request commit and the ownership and mode of the canonical data tree were changed recursively, wider than the runbook states, with the previous owners and modes not recorded.

## Not claimed

- No first data-local J03 handler (`S08` is not authorized; the registry ships empty).
- No deck user interface. For this wave the deck's access point is a notebook on the deck machine that uses `clients/deck/deck_handoff_client.py` and the `S05` module; a deck TUI, CLI or local web UI is not built.
- Omega is not authorized for any family (ADR-0067 amendment needed).
- The feature identity is not server-defined: the manifest's `definition_identities` is empty, and the deck-declared feature-provider identity is attribution evidence that K13 cannot cross-check.
- Accepted-artifact inputs are not available; the input is a dataset selector.
- The deck is pinned to the server's commit, but the server does not refuse an admission made before a promotion: that admission stays valid and its result is accepted (an operator obligation, in the runbook).
- Delivery is whole-file and in memory: at most 512 MiB per file, 1 GiB and 16 files per admission, about 4 GiB of API memory at the total bound. There is no delivery by member or by range, no deck-side stream of days, and a result bundle names one admission, so a period longer than one admission is several results.
- The supervised proof is one happy path. No fault of the hermetic proof was repeated on the real host; the largest real day (252.8 MiB) was never delivered there; concurrent deliveries and a real network between deck and server were not measured; the server ran a pull-request commit, not a commit of `main`.
- The API runs with read access to the whole `marketdata` tree through the group (accepted for v1 by the maintainer) and its unit has no memory limit.
- No Golden proof is claimed for this wave.

The statement that the topology is deployable must carry the bounds of ADR-0072 Amendment 1 and the findings above.

## Triggers recorded at closeout (each names a condition that reopens a decision; none is authorized by this closeout)

1. **Feature identity.** In v1 the server passes the deck no feature identity. Trigger to open a design gate: the FEI line (#363, composite technical feature provider) delivers a server-side feature definition identity that can populate `definition_identities`, or the maintainer names feature traceability as a requirement. Related, not scheduled: accepted-artifact inputs (ADR-0072 section 1) and `J11` (not planned, owner decision 2026-10-10).
2. **First data-local handler (`S08`).** Its issue must also add, for `j02-job-v1`, a result-body resolver together with the shared J14 store, and add `j02.jobs.read` to `framed_resume_scopes` (the API host composes jobs without framing in v1, ADR-0072 section 3 as implemented by #395).
3. **ADR-0072 triggers:** accepted-artifact inputs (a server-side store with a resolver); a deck on a commit other than the server's; time-based expiry (a timestamp column and a sweep); a binary or incremental transport family for partitions beyond the whole-file framing (delivery by member or by range); a closed registry of operation and profile identities.
4. **Measured bounds.** Measured and recorded: the real dataset has 2 116 daily partitions (median 21.5 MiB, p99 117.3 MiB, maximum 252.8 MiB), 719 exceeded 32 MiB and 161 exceeded 64 MiB, which led to ADR-0072 Amendment 1. Not measured: the 8 MiB output bound on a real output, concurrent deliveries, a real network, and the largest real day on the real server. Trigger: the first deployment after the amendment records the delivery of the largest real day (peak API memory and time), or a real partition above the new bounds appears.
5. **Deck access point.** Any later deck-hosted interface must listen on loopback only, because whoever reaches it inherits the deck credential's scopes (the server sees one fingerprint). Triggers, none authorized: (a) a deck TUI or CLI that wraps the facade; (b) a local web UI served from the deck, which is also the only way to use the existing App UI against the mTLS server (a page cannot present the deck's client certificate itself); (c) UI-initiated heavy compute, with three options for the maintainer: an interface near the deck, a queue the deck reads on its own initiative (needs an ADR-0057 amendment: server-to-deck delegation and remote workers pulling from the server queue are out of scope of DG-K2), or leaving runs manual. Condition to reopen: the App UI must start real runs.

---

## Authority

- `AGENTS.md`
- governance issues #318, #381, #382, #389, #409; design-gate issues #383, #387; amendment issue #431; DG-K design gate #319
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`S` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-K2)
- ADR-0043, ADR-0050, ADR-0057, ADR-0062 through ADR-0069 (with Amendments), **ADR-0072** (`ACCEPTED`, with Amendment 1)
- `docs/engineering/DGK2_SPLIT_TOPOLOGY_PROOF.md`, `tests/test_package_boundaries_v1.py`

Accepted ADRs and frozen contracts remain normative.

---

## Baseline and Credited State

```text
implement/dgk2 @ the closeout PR for issue #409 (ADR-0072 ACCEPTED with Amendment 1; S01-S07 merged; not yet promoted into main)
DG-K: P01-P06 FROZEN / COMPLETE within recorded bounds (#381)
DG-K2: S01-S07 FROZEN / COMPLETE within recorded bounds (#409); S08 OPEN_DEFERABLE / MISSING (not authorized)
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Explicit non-claims

- `S01`-`S07` are `COMPLETE` only within the bounds under "Closeout result"; the list under "Not claimed" applies.
- `S08` and any data-local J03 handler are not authorized; the registry stays empty.
- Omega remote use of ADR-0069 families is not authorized.
- `J11` is not planned; `J07`/`J08`, `I06`/`I07`, `A13`-`A15` and second venue are untouched.

---

## Mutation policy

- `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` and `OPEN_DECISIONS.md` change only on `governance/` or `admin/` branches under an issue designated governance-only.
- An implementation issue edits no governance file and reports staleness findings instead.

---

## Exit criteria for this scope

- Part A: **met** — ADR-0072 `ACCEPTED`, reconciled by #389.
- Part B: `S01`-`S07` implemented and merged into the integration branch within the acceptance of ADR-0072, `S07` run in both parts: **met within the bounds recorded** (closeout #409). Promotion of `implement/dgk2` into `main` remains the operator's separate PR.

---

## Out of Scope

- a message broker, a distributed scheduler, a generic executor or a remote-execution framework;
- server-to-deck job delegation, and remote workers pulling from the server queue;
- a credential service, IAM, OAuth/OIDC or bearer tokens;
- a second transport stack;
- a host outside `tools/`, or one deployed service per domain package;
- a canonical-storage migration to the deck (ADR-0057 §6);
- changing any existing identity bytes;
- Omega remote use of ADR-0069 families (ADR-0067 amendment);
- more than one J03 worker, and a durable J14 transfer store;
- `J11`, L2 acquisition, `J07`/`J08`, `I06`/`I07`, second venue.

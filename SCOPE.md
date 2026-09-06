# Scope: Application Service Seam v1 — ASS-01 COMPLETE

## Goal

Close the first implementation atom of the Application Service Seam v1 after independent review, exact-head CI and merge, while preserving the separation between the in-process application-service boundary and later API/runtime/client work.

## Integrated state

```text
pre-ASS-01 baseline        = e2991bbd3dcd5f25d35148add76abfbc1e0b72b3
ASS-01 candidate           = 6d71f68d9a322487840688e13fe89387842e0029
PR                         = #27
CI                         = Quant Platform integrity #73 — SUCCESS
merge                      = 066e7cd577104fb2c8f657430402b79cd58ba9aa
scope state                = COMPLETE
```

ASS-01 introduced no quantitative runtime behavior.

## Architecture outcome

The canonical in-process application-service owner is now:

```text
quant_platform.application
owner = application
```

Its role is composition of application use cases. It does not own quantitative semantics.

Current enforced owner direction permits:

```text
application -> application
application -> access
application -> producer
application -> source
application -> physical
application -> shared
```

and no existing runtime owner is permitted to depend on `application`.

The owner graph remains acyclic.

## Executable orchestration governance

`tools/` is now governed as executable orchestration source code rather than only appearing as a possible dependency target.

New executable orchestration must not bypass the application seam to depend directly on runtime/domain owners.

The verified composition rule is:

```text
runtime !-> tools/tests
tools   !-> tests
tests   -> tools/runtime/test-support is allowed
```

Existing pre-ASS-01 violations are retained only as exact-edge, self-cleaning debt for later convergence:

```text
tool -> domain debt = 13 exact edges across conformity_e2e and import_bybit_trades
tool -> tests debt  = 1 exact edge: conformity_e2e -> golden_conformity_support
```

New dependencies inside those tools receive no wildcard exemption. Stale debt entries fail architecture verification. Dynamic import/code-execution bypasses are fail-closed consistently with the runtime analyzer.

## Evidence

Credited final evidence:

```text
python tests/test_package_boundaries_v1.py = 15/15 PASS
python tools/run_tests.py                  = 29/29 PASS
git diff --check                           = PASS
independent re-review                      = APPROVE
BLOCKERS / IMPORTANT / MINOR               = NONE / NONE / NONE
exact-head CI                              = SUCCESS
```

No Golden E2E, PostgreSQL integration or DataGateway re-proof was required for this architecture-only atom.

## Application Service Seam maturity

```text
ASS-01 — ownership + architecture enforcement     = COMPLETE
ASS-02 — first canonical application service      = NOT ACTIVE
ASS-03 — executable orchestration/config convergence = NOT ACTIVE
```

ASS-01 does **not** implement:

- `MarketDataService` or another application use case;
- semantic-selector resolution;
- Consumer API result-envelope/error translation runtime;
- canonical configuration resolution;
- migration of existing executable orchestration behind the seam;
- API transport or server runtime;
- Job runtime;
- CLI/TUI/App clients;
- H01 / Feature runtime.

## Deferred runtime decisions

The following remain intentionally open and are not implied by ASS-01:

```text
HTTP / gRPC / Arrow Flight / WebSocket
wire serialization
streaming / pagination / cursors
remote reachability
authentication / TLS
runtime host / process topology / deployment
Job runtime
product CLI / TUI / App
client SDK / service supervision
multi-venue capability-resolution mechanism
```

The later roadmap capability `Canonical API and job runtime` remains a separate product/runtime milestone.

## Legacy harvest state

Legacy Capability Harvest Audit v1 remains COMPLETE.

H01 — Diagonal / Stacked Imbalance Core remains the selected **first harvest candidate**, with the same narrow ADOPT boundary and unresolved production integration proposition. ASS-01 does not activate or implement H01.

## Next planning gate

This closeout intentionally selects **no next implementation slice**.

The next project decision must evaluate the dependency/value ordering among at least:

```text
ASS-02 — first canonical application-service vertical
ASS-03 — orchestration/configuration convergence
H01    — first legacy quantitative harvest implementation
roadmap/capability atomization of the broader architecture spine
```

Any selected implementation requires its own explicit bounded scope.

## Out of scope for this closeout

- further runtime implementation;
- ASS-02 or ASS-03 implementation;
- H01 implementation;
- transport/deployment decisions;
- Feature/Representation redesign;
- broad roadmap rewrite or phase renumbering;
- changes to accepted ADR/contract semantics.

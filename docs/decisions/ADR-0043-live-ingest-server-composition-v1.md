# ADR-0043 — Live-ingest server v1 composition

**Status:** ACCEPTED
**Date:** 2026-09-25

## Context

`SCOPE.md` ("Live Ingest Server Production Readiness v1") Active Path step 2
requires a design gate that closes finding **P1 — Persistent Ingest Server
Ownership** before any implementation slice may build a bounded, long-running
live-ingest process (issue #124, blocking issue #126).

A11 (ADR-0040), K02 (ADR-0041) and K10 (ADR-0042) are frozen and implemented,
and their real-server restart/reconcile/checkpoint-advance path is proven by
PR #122. But every existing entrypoint that exercises them --
`tools/bybit_live_proof.py`, `tools/live_real_publish_proof.py`,
`tools/live_real_restart_proof.py` -- is a one-shot, bounded CLI invocation
that runs to completion and exits. There is no persistent-loop, daemon or
service scaffolding anywhere in the repository. PR #122's own "Process A" /
"Process B" real-server evidence is realized purely operationally: the same
restart-proof script is launched twice as two independent OS processes,
coupled only through the durable on-disk checkpoint file and durable catalog
state -- there is no in-process loop and no IPC.

`docs/architecture/MARKET_DATA_INGEST.md` already anticipated this gap ("A
live collector is not required to use the same implementation as a finite
job... The deployment topology remains open... does not prescribe a
container count, process topology, broker, scheduler, async framework, or
checkpoint database") and left it as an open decision. This ADR closes that
decision for the first bounded vertical, without freezing anything beyond it.

K03/K04/K05 observability, capacity and pressure primitives
(`quant_platform.operations.observability`, `.capacity`, `.pressure`) are
already implemented and credited `COMPLETE`, but are not wired into
`bybit_live.py` or `operations/checkpoint.py` anywhere -- today's only
operator-visible signal is `print()` output from the one-shot proof CLIs.

## Decision

### 1. Composition: reuse the existing restart/reconcile cycle as the loop's steady state

The key structural insight is that PR #122's "Process A publishes; Process B
restarts, reconciles and advances the checkpoint" sequence is not just proof
scaffolding -- it is structurally identical to two consecutive iterations of
a continuous server. The server loop is defined as:

```text
on start:
    load checkpoint (if any)
    resume_live_ingest(...)            # exact ADR-0042 S5 restart procedure,
                                        # reused unchanged (existing function)
    -> RestartOutcome: NO_CHECKPOINT | GAP_DETECTED | RESUMED

repeat until stop requested:
    acquire one bounded batch           # existing acquisition seam
    canonicalize/publish/certify/catalog # existing S13/S14 composition
    advance checkpoint                  # only after durable publication
    emit K03 operational signal(s)
    if the session dropped:
        loop back to resume_live_ingest # same reconcile path as `on start`,
                                        # not a new disconnect model
```

No new acquisition, canonicalization, publication, certification, cataloging
or checkpoint-binding logic is introduced. The loop is a thin, unbounded
outer `while` composing the exact functions already implemented in
`quant_platform.application.bybit_live`:

- startup/gap recovery: `resume_live_ingest`, `load_current_durable_publication_state`;
- each cycle's acquire+publish: the same composition `run_real_server_publish_proof`
  already performs (bounded acquisition -> canonical materialization -> S13
  certification -> S14 publication/catalog -> eligibility), reused with
  production-sized bounds instead of proof-sized bounds (`max_messages`/
  `max_seconds` become operator-configured, not test constants);
- checkpoint advance: `next_checkpoint` / `_persist_checkpoint_from_publication`,
  called only when the preceding publish reached a durable/eligible status.

A disconnect between cycles is not a new failure mode to design: it is
exactly the disconnect/reconnect case ADR-0040 §7 and ADR-0042 §5 already
freeze, and it is exercised the same way whether the previous process exited
(as in the two-OS-process proof) or the same process is simply starting its
next cycle.

### 2. Owning module and executable boundary

Per the existing, already-frozen DG-D/C05 rule (`OPEN_DECISIONS.md`:
"executable tooling owns CLI/environment acquisition and parsing...
`quant_platform.application` owns concrete composition and does not read
process arguments/environment directly"), the loop follows the same split
already used by every other live-ingest entrypoint:

- `quant_platform.application.live_ingest_server` (new module) owns the loop
  composition described in §1: a typed, immutable `LiveIngestServerConfigV1`
  (storage root/id, dsn, checkpoint path, producer/code-ref, per-cycle
  acquisition bounds, stop-check interval) and a `run_live_ingest_server(config,
  *, stop_requested)` entrypoint, where `stop_requested` is a caller-supplied
  zero-argument predicate (defaults to "never"), so the loop itself never
  touches `signal`/OS process control -- that stays at the executable
  boundary, consistent with C05.
- `tools/live_ingest_server.py` (new script) owns argv/env parsing and OS
  signal wiring only: it builds `LiveIngestServerConfigV1` from CLI
  arguments (mirroring `tools/live_real_restart_proof.py`'s existing flags:
  `--dsn`, `--storage-root`, `--storage-root-id`, `--checkpoint-path`,
  `--producer`, `--code-ref`, plus new `--max-messages-per-cycle`,
  `--max-seconds-per-cycle`), installs `SIGTERM`/`SIGINT` handlers that flip
  a stop flag (never a hard kill of an in-flight publish), prints the same
  `_identity_evidence()` block `live_real_restart_proof.py` already prints,
  and calls `run_live_ingest_server`.

No `__main__.py`, `console_scripts` entry point or process manager is added
by this ADR; how the operator supervises/restarts the `tools/live_ingest_server.py`
process (systemd unit, `supervisord`, container restart policy, or a plain
foreground process during first rollout) is deployment-local, exactly as
ADR-0041 §1/§4 already treats "exact OS/service-manager primitives."

### 3. Publication-before-checkpoint stays enforced by existing code, not new loop logic

The loop must never call checkpoint-advance except immediately after a
publish call returns a durable/eligible status, using the same
`_persist_checkpoint_from_publication` / `next_checkpoint` functions that
already enforce ADR-0042 §2's ordering and §4's monotonicity/domain rules. A
clean stop between cycles (stop flag observed after a cycle completes) is
already covered by ADR-0042 §3's "crash after checkpoint advance" case; the
loop introduces no new crash-state.

### 4. Operator/runbook evidence (closes P6 for this design gate)

The loop must wire the already-implemented, currently-unused K03 seam
(`quant_platform.operations.observability`) rather than continue with bare
`print()`:

- emit one `OperationalSignalV1` with `SignalKind.LIFECYCLE_TRANSITION` on
  every loop-state change (`STARTING -> RESUMING -> RUNNING -> STOPPING ->
  STOPPED`, plus entry into a reconcile/gap-recovery cycle);
- emit one `OperationalSignalV1` with `SignalKind.HEALTH_SNAPSHOT` per
  completed cycle, mapping existing return values to `HealthState`:
  `SessionState.ACQUIRING`/durable publish PASS -> `HEALTHY`; explicit
  non-complete gap (`RestartOutcome.status == GAP_DETECTED`) -> `DEGRADED`;
  publish/certification failure -> `FAILED`; a cycle that could not observe
  its own outcome -> `UNKNOWN` (never silently `HEALTHY`, per the seam's own
  frozen rule);
- emit `SignalKind.FAILURE` on any unhandled cycle exception before the
  process exits;
- reuse `observe_capacity`/`evaluate_pressure` (K04/K05, already implemented)
  once per cycle and fold their `PressureDecision` into the same
  `HEALTH_SNAPSHOT` signal's `diagnostics`, rather than inventing a parallel
  pressure check.

At minimum, the resulting signal stream must let an operator read: current
session/subscription state; last durable checkpoint identity and generation;
last durable canonical `TradeKeyV1`; publication/certification/catalog
failures; reconnect/reconciliation outcome; explicit gap intervals; pressure
and capacity decisions; and confirmation the process is running under the
K02 `mkt-transform` identity (the startup `_identity_evidence()` print,
reused unchanged).

Where the signal stream is sent (stdout, file, log aggregator) is
deployment-local and not decided here, matching P6's own text ("exact
logging, service manager and alerting technology remain deployment-local
unless a future ADR freezes them"); only the in-repo emission call sites are
decided by this ADR.

### 5. Why this does not require a generic scheduler, broker, workflow engine or J03

The loop is a single bounded process for exactly one venue, instrument and
schema (`bybit`/`linear`/`BTCUSDT`/`trade-v1`), with no submission identity,
no independent job queue or result store, no retry/backoff policy beyond the
reconnect/reconciliation semantics ADR-0040 already freezes, and no
multi-tenant or multi-job scheduling concern. If the process itself dies, the
deployment-local service manager restarts it, and the existing checkpoint
resumes it -- this is process supervision, not job orchestration. J03 (job
runtime: submission identity, retry/idempotency, result identity, failure
semantics for arbitrary durable operations) is a different, general-purpose
capability this ADR does not need and does not touch.

### 6. Why this does not imply J08 live product mode

J08 requires J07 (paper/shadow mode) and K09 (retention/deletion authority)
in addition to K02/K03/K05/K06/K08/K10, none of which this ADR touches or
requires. The server has no order-placement, execution, portfolio or
trading authority; it only continuously performs what A11/K10 already do as
bounded one-shot proofs today. Reaching this ADR's design does not authorize
or imply any J08 proposition.

### 7. K02 conformance

The process runs under the same least-privilege `mkt-transform` identity
already proven for the one-shot proofs (PR #113): the same checkpoint-path
filesystem authority, the same minimum catalog/database role, and no new
provider secret (Bybit public trades v1 needs none, ADR-0041 §2). Running
continuously does not widen filesystem, database, backup or administrative
authority beyond what ADR-0041 already freezes.

## Excluded

This ADR does not define or authorize:

- a generic scheduler, broker, workflow engine or J03 job runtime;
- J08 live product mode or any execution/trading authority;
- B06 live-consumer/DataGateway cursor semantics;
- K07 tier relocation or K09 retention/deletion authority;
- second venue, second instrument or generic provider resolution;
- any new canonicalization, publication, certification, cataloging or
  checkpoint-binding semantics beyond ADR-0040/ADR-0041/ADR-0042;
- long-gap repair/state-machine design (Active Path step 3, ADR to follow
  separately per issue #125);
- the exact OS/service-manager unit, container image or log sink used to run
  `tools/live_ingest_server.py` in production (deployment-local).

## Consequences

- Active Path step 4 (issue #126) can implement `LiveIngestServerConfigV1`,
  `run_live_ingest_server` and `tools/live_ingest_server.py` by composing the
  functions named in §1 into the loop shape defined here, without further
  semantic decisions about acquisition, publication, checkpoint ordering or
  observability wiring.
- Hermetic tests can exercise multiple loop iterations with a fake/bounded
  stop predicate and the existing checkpoint/publish test doubles, verifying
  cycle ordering (resume -> acquire -> publish -> checkpoint -> signal) and
  clean-stop behavior, the same way `tests/test_bybit_live_checkpoint_v1.py`
  already exercises the two-phase restart composition.
- Real-server continuous-run, stop/restart and signal-emission evidence
  remains the responsibility of Active Path step 6 (issue #128), not this
  design gate.
- `docs/architecture/MARKET_DATA_INGEST.md` §12/§14's "deployment topology
  remains open" / "runtime checkpoint and deployment choices" open items are
  now closed for this first vertical by this ADR; the document's broader
  statement that a future second collector need not share this
  implementation is unaffected.

# ADR-0032 - RAW / source protection v1

**Status:** ACCEPTED

**Date:** 2026-09-17

## Context

DG-H operational safety needed a way to answer one question before any
tier-relocation, backup/restore or retention/deletion authority (K07-K09)
could be built on top of it: is the bounded set of source evidence required
to reproduce one declared canonical source-acquired result present,
content-verified and complete right now? K05 already supplies pressure
observation; K06 needed to turn that into a protection decision without
becoming a second identity system or a filesystem crawler.

Credited compatibility evidence:

- K05 pressure policy already reports thresholds/time-to-full/restrictions
  from observed capacity, independently of protection semantics.
- A03/A08 already establish source-acquired lineage and S13 certification
  evidence for canonical results.
- Package-boundary rule (`quant_platform.operations` may depend only on
  `operations`/`shared` owners): `quant_platform.operations.protection` must
  not import `quant_platform.source_adapters` or reach into a filesystem.

## Decision

Adopt K06 RAW/source protection v1 as a pure protection-identity and
assessment seam under `quant_platform.operations.protection`.

Protection is a **reconstruction claim, not a path claim**: K06 proves
whether caller-supplied evidence is sufficient to reconstruct one declared
result, never crawls storage, watches for changes, copies bytes, schedules
work or authorizes deletion itself.

Canonical fail-closed states (`ProtectionState`):

```text
PROTECTED
AT_RISK
UNAVAILABLE
CORRUPT
LOST
```

`UNAVAILABLE`, `CORRUPT` and `LOST` never normalize to `PROTECTED` or
`AT_RISK`; `AT_RISK` only ever applies when every artifact independently
verified. Every derived/computed field is recomputed by its owning
`__post_init__` from already-validated inputs and cross-bound to the exact
object it describes (`ProtectionUnitIdentity`, `PressureDecision`) rather
than trusted from a caller-supplied bare state/decision string: a frozen
dataclass keeps a value from being *mutated*, not from being wrong at
construction, so validity comes from recomputation and cross-binding.

### Attributable-evidence principle (`SafetyRelevanceAssertion`)

K06 cannot independently derive "is this write protecting unique,
non-reconstructible source evidence" from pressure evidence alone -- that
question is inherent to what the write is *for*, not to available bytes,
and `quant_platform.operations` has no source/authority registry to answer
it from. Rather than accept a bare unattributed boolean, `SafetyRelevanceAssertion`
binds the claim to an explicit accountable authority id, an explicit
instant, a rationale, and -- critically -- to the exact
`ProtectionWriteAction` (unit *and* bounded write size) it is about, so a
safety claim for one action can never be reused, whole or understated, to
authorize a differently sized or unrelated write. This is a general pattern,
not a K06-specific trick: a bounded package that cannot independently
verify a claim about external state must not implement a self-referential
check comparing caller-supplied data against other caller-supplied data (a
tautology), and must instead durably attribute the claim to an accountable
party so the claim's *truth* remains an explicit, auditable downstream
responsibility. The same principle was later reapplied to E04 (ADR-0034).

## Runtime materialization

The accepted runtime foundation is implemented under
`quant_platform.operations.protection` as: `ProtectionUnitIdentity`,
`AcceptedReconstructionContract`, `ArtifactProtectionIdentity`,
`ArtifactVerificationEvidence`, `ArtifactAssessmentResult`,
`ProtectionObligationEvidence`, `ProtectionAssessment`, `assess_protection()`,
`ProtectionWriteAction`, `SafetyRelevanceAssertion`,
`ProtectionWriteAuthorization`, `authorize_protection_write()`.

Deterministic multi-failure precedence (most severe first) resolves mixed
per-artifact outcomes into one `ProtectionState` without silently picking an
optimistic state.

The implementation intentionally introduces no filesystem crawler, source
registry, scheduler, deletion authority or generic plugin/provider
framework.

## Consequences

- K06 is frozen and complete for the bounded v1 protection-identity/
  assessment runtime; K07 (tier relocation) and K08 (backup/restore) can
  depend on its `ProtectionState`/`ProtectionAssessment` output.
- K09 (retention/deletion) still requires its own restore-proof authority
  before any deletion decision; K06 grants no deletion authority itself.
- **Accepted known limitation:** `ProtectionObligationEvidence`'s obligation
  kind is not yet type-restricted to K06-owned concerns (accepted during
  issue #59 review, 2026-09-17) -- tracked as a documented limitation, not
  an unresolved bug, and left for a future narrowing pass once a second
  obligation kind actually exists to disambiguate against.

## Acceptance evidence

Executable proof is in `tests/test_operations_protection_v1.py`.

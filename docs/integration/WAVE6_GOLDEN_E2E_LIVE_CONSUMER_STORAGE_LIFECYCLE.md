# Wave 6 Golden E2E — Live Consumer Data Plane & Storage Lifecycle Proof

- **Status:** PASS
- **Milestone:** Wave 6 Active Path step 9
- **Issue:** [#200](https://github.com/NeoNix-Lab/quant-platform/issues/200)
- **Date:** 2026-09-29
- **Dataset Evidence:** `canonical/trades/bybit/BTCUSDT/trade-v1`
- **Proof Type:** bounded, hermetic application proof over deterministic fixture evidence and temporary storage

This document records the Wave 6 Golden E2E proof for the live consumer data
plane and storage lifecycle path. The proof composes already-implemented Wave
6 capabilities without adding new semantics:

- `B06` live consumer cursor and resume through `DataGateway.live_stream()`;
- `D04` incremental/live candle computation through the B06-to-D04 application
  composer;
- `K07` storage-tier relocation over real temporary bytes;
- `K09` retention/deletion authority, refusal, audit and exact-byte deletion.

The proof is deliberately bounded and reproducible in the repository. It does
not claim target-host deployment, client/API behavior, Strategy consumption,
paper/live trading, second venue, or L1/L2/L3 market depth.

---

## Path Exercised

```text
Bounded canonical Bybit BTCUSDT trade fixture
        ↓
B06 DataGateway.live_stream() fresh cursor
        ↓
D04 GapSafeLiveCandleComposer partial/CLOSED updates
        ↓
K07 relocate_storage_tier() over temporary source/target roots
        ↓
K09 retention/deletion refusal and permitted exact-byte deletion
        ↓
B06 resume from prior cursor
        ↓
D04 CLOSED candle equivalence against D03 historical aggregation
```

---

## Execution Evidence

Executed locally with:

```powershell
python tools\wave6_golden_e2e.py --json
```

Focused verification:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests\test_wave6_golden_e2e_v1.py tests\test_live_candle_stream_composition_v1.py tests\test_application_storage_relocation_v1.py tests\test_application_retention_deletion_v1.py tests\test_package_boundaries_v1.py -q
```

Result:

```text
29 passed, 45 subtests passed
PASS: Wave 6 Golden E2E bounded proof satisfied B06/D04/K07/K09 acceptance.
```

---

## Stable Identities

- **Proof identity:** `wave6-golden-e2e-proof-v1:sha256:a5085e9eb44e66f45e92638611e209513cd4efdd2f573db01a6ac45b59ea32a8`
- **First cursor anchor:** `2026-01-01T10:09:59Z`, trade `t-4`, sequence `4`
- **Final cursor anchor:** `2026-01-01T10:15:00Z`, trade `t-7`, sequence `7`
- **Coverage segment:** `b413e19483396f47f6eee7687e6f8d142bda1e8ffaab6969dc7934a7e5387e6a`
- **K07 relocation id:** `relocation-v1:sha256:a54c030ac7b3fa45b0f614bca385b6d069cafd5d6654ab1db3ea57f946a33e8e`
- **K07 target content SHA-256:** `a152613e2ea4da323eb0c4d5e33b156ce33beebca0852a1bbcd40f32824f913b`
- **K09 protected refusal id:** `retention-deletion-decision-v1:sha256:9914283d134af6a6ec1cde630abc2c08deefe8d714472d7ce7db963ad7855bd4`
- **K09 permitted deletion id:** `retention-deletion-decision-v1:sha256:9bfcf4af1c3aecc4b69ada9ccd69a6412422634b5d0c52568cec8995cddcfd5a`
- **K09 restore proof ref:** `verified-restore-proof-ref-v1:sha256:27eb36f523bb7ed81d988a4bed616519cbe3dfca6e9ec35cfc57f71d50a46f80`

---

## Candle Equivalence

`D04` live CLOSED candles matched `D03` historical aggregation exactly:

```text
2026-01-01T10:00:00Z/2026-01-01T10:05:00Z open=100 high=101.5 low=100 close=101.5 volume=0.3 trades=2
2026-01-01T10:05:00Z/2026-01-01T10:10:00Z open=99 high=102 low=99 close=102 volume=0.7 trades=2
2026-01-01T10:10:00Z/2026-01-01T10:15:00Z open=103 high=103 low=98.5 close=98.5 volume=1.1 trades=2
```

The proof executes two independent runs with the same input and obtains the
same proof identity and candle/cursor identities.

---

## Storage Lifecycle Evidence

`K07` relocation reached `CLEANED_UP`: the target bytes matched the expected
content identity, the authoritative catalog root moved from `hot` to `cold`
inside the proof catalog adapter, and the old source copy was removed only
after the target had been verified and switched.

`K09` exercised both sides of the authority:

- a `PROTECTED_EVIDENCE` candidate was refused with
  `k06_protected_evidence`, no verified restore proof, and permanent
  preservation-class refusal; its bytes remained present;
- a `CANONICAL_RESTORABLE` candidate with an independent verified restore
  proof was permitted and the exact matching bytes were deleted after the
  decision was audited.

---

## Acceptance Verification

1. **No invented continuity:** the proof consumes B06 live stream events and
   resumes from the previous cursor; it does not synthesize missing trades.
2. **Candle equivalence:** D04 closed live candles are byte-for-byte equal to
   D03 historical candles for the same ordered trade input.
3. **No consumer-visible disappearance:** the closed candle set remains stable
   across the interleaved K07 and K09 storage actions.
4. **Crash-safe relocation foundation:** K07 reaches terminal `CLEANED_UP` only
   after verified target identity and catalog switch, reusing ADR-0048's
   application seam.
5. **Governed deletion:** K09 refuses protected/unverified evidence and deletes
   only the independently restorable candidate after audit.
6. **Determinism:** two independent proof runs produce the same stable proof
   payload and `wave6-golden-e2e-proof-v1` identity.

This evidence supports Wave 6's bounded Golden E2E acceptance. Governance
closeout remains issue #201.

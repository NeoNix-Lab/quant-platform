# Wave 5 Golden E2E — Supervised ML Determinism Proof

- **Status:** PASS
- **Milestone:** Wave 5 Active Path step 4
- **Issue:** [#174](https://github.com/NeoNix-Lab/quant-platform/issues/174)
- **Date:** 2026-09-28
- **Dataset Evidence:** `canonical/trades/bybit/BTCUSDT/trade-v1`
- **Evidence Fixture:** `fixtures/conformity/golden-bybit-btcusdt-2024-01-15.json`

This document records the Golden E2E evidence for Wave 5 supervised ML:
deterministic construction of supervised train/test input, deterministic
baseline training/evaluation, and stable model/prediction/metric artifact
identities across two independent executions over the same bounded canonical
Bybit BTCUSDT evidence.

This proof remains deliberately inside the supervised ML boundary. It does not
introduce a model registry, job runtime, live/paper trading, strategy execution,
or governance reconciliation.

---

## Path Exercised

```text
Checked-in canonical Bybit BTCUSDT fixture
        ↓
Application proof composition
        ↓
I04 SupervisedProjection
        ↓
I05 centroid_classifier_v1 baseline
        ↓
Experiment RunIdentity and ArtifactIdentity values
        ↓
Stable model, prediction and metric artifact content identities
```

---

## Proof Specification

- **Fixture identity:** `bybit-btcusdt-golden-fixture-v1:sha256:0b8f1474001bc3dacae4215d4a0204ade2d9a0d2f6a14473d9471510d7f392d1`
- **Projection identity:** `supervised-projection-v1:sha256:ee247d8b0c416b826fc6a3a991a580aa6c96e351de6ccd4bd485c91afee0c380`
- **Selection policy identity:** `supervised-selection-policy-v1:sha256:9ea9e2489c0973c521801043b9ed4722a172240a56b8d763d6d867dc3c53debe`
- **Training policy identity:** `supervised-training-policy-v1:sha256:69e087b295fe786a034629d4feb4e3ccb753eb7db831e7cf63833fb5f9de8d51`
- **Run spec identity:** `eb106bce4b2ca3308cb49f2e9e94a495f1b343da061b42d20e9b6c5f80c3d9f0`
- **Execution id:** `wave5-golden-supervised-e2e`
- **Samples:** `2` train, `2` test, `0` rejected
- **Model family:** `centroid_classifier_v1`
- **Class labels:** `0`, `1`

---

## Execution Evidence

Executed locally with:

```powershell
python tools\golden_supervised_e2e.py --json
```

Key output:

```text
accuracy=1
macro_precision=1
macro_recall=1
macro_f1=1
row_count=2
first_run_equals_second_run=True
```

Artifact content identities:

```text
normalizer   supervised-normalizer-v1:sha256:4714b35288d4ce45d635fb636d45da87567d02ebce26c46068eceaea60c9dbad
model        supervised-model-v1:sha256:c73bb6e625d5dd0e41c4b83a848487d33975948a120c58e1fd855bd1d77cddc0
predictions  supervised-predictions-v1:sha256:aa4808cdaa41bcfbbfc33cd75ab4f78455807ed1c68634dbd4c329fe09619ac0
metrics      supervised-metrics-v1:sha256:f41d6fedf89753ecba9032ebde169a66458269342829f7b148258087e61ed9ca
```

Run-scoped artifact identity fingerprints:

```text
normalizer   de70041c0a7eb775fe9451eed200d174cf6590d2d49180659a9e08e4cdad83b7
model        92e2059946190967747ac912fa6460fb7e7709bbdedf58697e417cf90e98e1de
predictions  a02bff565b648cc3ae3593b7f8aeb8b0f67e4f2cb5e96e6b74aef30a3073daea
metrics      03ec8e6304f6ff2422902244f7eda6ef6cdea7b01a877ec99ae1551a8e82f26d
```

---

## Acceptance Verification

1. **Stable fold/projection identity:** the proof emits
   `supervised-projection-v1:sha256:ee247d8b0c416b826fc6a3a991a580aa6c96e351de6ccd4bd485c91afee0c380`
   with a non-vacuous train/test split and zero rejected samples.
2. **Stable trial/run identity:** both executions use the same deterministic
   `RunIdentity` with run spec
   `eb106bce4b2ca3308cb49f2e9e94a495f1b343da061b42d20e9b6c5f80c3d9f0`.
3. **Stable metric values:** accuracy, macro precision, macro recall and
   macro F1 are all exactly `1` over two test rows.
4. **Stable artifact identities:** normalizer, model, predictions and metrics
   all emit deterministic content identities and run-scoped artifact identity
   fingerprints.
5. **Architectural seam:** `tools/golden_supervised_e2e.py` is a thin CLI and
   composes through `quant_platform.application.golden_supervised`; supervised
   domain logic remains in `quant_platform.learning`.

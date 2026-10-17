"""``feature-provider-v1`` identity and the replay output contract (#342, ADR-0069 section 7)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.feature_provider_identity import (  # noqa: E402
    FEATURE_PROVIDER_IDENTITY_PREFIX,
    REPLAY_FEATURE_PROVIDER_KEY,
    FeatureProviderIdentityError,
    ReplayOutputContractV1,
    check_replay_feature_provider,
    feature_provider_identity,
    feature_provider_payload,
    replay_feature_provider_entry,
    validate_feature_provider_payload,
)
from quant_platform.application.governed_result_import import ResultOutputContractV1  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
SOURCE = "ab" * 32


def payload(**overrides):
    values = {
        "repository": "NeoNix-Lab/quant-platform",
        "commit": COMMIT,
        "qualified_name": "deck.features:build_provider",
        "source_sha256": SOURCE,
        "parameters": {"window": 20, "name": "z"},
        "tree_is_clean": True,
    }
    values.update(overrides)
    return feature_provider_payload(**values)


class FeatureProviderIdentityV1Tests(unittest.TestCase):
    def test_the_identity_is_the_adr_0068_profile_over_the_adr_0069_payload(self) -> None:
        document = payload()
        self.assertEqual(
            {"identity_domain", "repository", "commit", "qualified_name", "source_sha256", "parameters"},
            set(document),
        )
        self.assertEqual("feature-provider-v1", document["identity_domain"])
        # sorted-compact-ascii-v1, written out: sorted keys, compact separators, ASCII.
        expected_bytes = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("ascii")
        self.assertEqual(
            "feature-provider-v1:sha256:" + hashlib.sha256(expected_bytes).hexdigest(),
            feature_provider_identity(document),
        )
        self.assertTrue(feature_provider_identity(document).startswith(FEATURE_PROVIDER_IDENTITY_PREFIX))

    def test_the_identity_depends_on_every_field_and_not_on_key_order(self) -> None:
        base = feature_provider_identity(payload())
        reordered = dict(reversed(list(payload().items())))
        self.assertEqual(base, feature_provider_identity(reordered))
        for label, change in (
            ("repository", {"repository": "NeoNix-Lab/other"}),
            ("commit", {"commit": "f" * 40}),
            ("qualified_name", {"qualified_name": "deck.features:other"}),
            ("source_sha256", {"source_sha256": "cd" * 32}),
            ("parameters", {"parameters": {"window": 21, "name": "z"}}),
        ):
            with self.subTest(label):
                self.assertNotEqual(base, feature_provider_identity(payload(**change)))

    def test_a_dirty_tree_has_no_identity(self) -> None:
        for value in (False, None, 1, "yes"):
            with self.subTest(value=value), self.assertRaises(FeatureProviderIdentityError) as caught:
                payload(tree_is_clean=value)
            self.assertEqual("dirty_tree", caught.exception.reason)

    def test_a_payload_is_exactly_its_six_admissible_fields(self) -> None:
        good = payload()
        validate_feature_provider_payload(good)
        broken = {
            "extra field": {**good, "extra": 1},
            "missing field": {key: value for key, value in good.items() if key != "commit"},
            "other domain": {**good, "identity_domain": "feature-provider-v2"},
            "a url as repository": {**good, "repository": "https://example.com/org/repo"},
            "credentials in repository": {**good, "repository": "user:token@host/repo"},
            "abbreviated commit": {**good, "commit": "0123456"},
            "uppercase commit": {**good, "commit": COMMIT.upper()},
            "name without attribute": {**good, "qualified_name": "deck.features"},
            "path as module": {**good, "qualified_name": "deck/features.py:build"},
            "short source hash": {**good, "source_sha256": "ab" * 8},
            "parameters not an object": {**good, "parameters": [1]},
            "non finite parameter": {**good, "parameters": {"x": float("nan")}},
            "oversized parameters": {**good, "parameters": {"x": "y" * 20000}},
        }
        for label, document in broken.items():
            with self.subTest(label), self.assertRaises(FeatureProviderIdentityError):
                feature_provider_identity(document)

    def test_a_sha256_object_format_commit_is_accepted(self) -> None:
        self.assertTrue(feature_provider_identity(payload(commit="a" * 64)))

    def test_the_replay_entry_is_checked_by_recomputation_only(self) -> None:
        entry = replay_feature_provider_entry(payload())
        configuration = {REPLAY_FEATURE_PROVIDER_KEY: entry, "other": 1}
        self.assertEqual(entry["identity"], check_replay_feature_provider(configuration))
        cases = {
            "provider_missing": {"other": 1},
            "provider_malformed": {REPLAY_FEATURE_PROVIDER_KEY: {"identity": entry["identity"]}},
            "provider_identity_mismatch": {REPLAY_FEATURE_PROVIDER_KEY: {
                "identity": FEATURE_PROVIDER_IDENTITY_PREFIX + "0" * 64, "payload": entry["payload"],
            }},
        }
        for reason, document in cases.items():
            with self.subTest(reason), self.assertRaises(FeatureProviderIdentityError) as caught:
                check_replay_feature_provider(document)
            self.assertEqual(reason, caught.exception.reason)
        # A payload changed after the identity was computed no longer matches it.
        tampered = {REPLAY_FEATURE_PROVIDER_KEY: {
            "identity": entry["identity"], "payload": {**entry["payload"], "commit": "f" * 40},
        }}
        with self.assertRaises(FeatureProviderIdentityError) as caught:
            check_replay_feature_provider(tampered)
        self.assertEqual("provider_identity_mismatch", caught.exception.reason)

    def test_the_replay_contract_is_a_k13_output_contract_that_adds_the_requirement(self) -> None:
        contract = ReplayOutputContractV1("replay_scores", "scores", "scores-v1")
        self.assertIsInstance(contract, ResultOutputContractV1)
        self.assertEqual("replay_feature_provider", ReplayOutputContractV1.configuration_key)
        entry = replay_feature_provider_entry(payload())
        self.assertEqual(entry["identity"], contract.check({REPLAY_FEATURE_PROVIDER_KEY: entry}))
        with self.assertRaises(FeatureProviderIdentityError):
            contract.check({})


if __name__ == "__main__":
    unittest.main()

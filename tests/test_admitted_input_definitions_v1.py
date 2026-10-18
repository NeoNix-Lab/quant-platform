"""Server carriage of canonical feature definitions through ``admit`` (issue #463, ADR-0073 section 5).

The registry and the eight refusals are exercised through the transport's own
``decode`` (the wire), the manifest through the real K12 authority over a real
``DataGateway`` (the fixture of the K12 authority tests), and the seal through
``AdmittedInputTransport`` with a SQLite admission store.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import test_admitted_input_authority_v1 as k12  # noqa: E402

from quant_platform.application import admitted_input_authority as authority_module  # noqa: E402
from quant_platform.application import admitted_input_definitions as registry  # noqa: E402
from quant_platform.application.admitted_input import AdmittedInputNotFound  # noqa: E402
from quant_platform.application.admitted_input_transport import AdmittedInputRequest  # noqa: E402
from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402

KEY = "order_flow.failed_aggression"
# The pre-change manifest of ``k12.request()`` over the K12 authority fixture, computed at 76750bc
# (before this atom existed). A request without ``definitions`` must still seal exactly this.
PRE_CHANGE_DIGEST = "eb2a55f5c865640ddb5339ec27ad79614d5bcfee5743eefd1b201daca85b3e1c"
BAR_FLOW_1M = "bar-flow-definition-v1:sha256:0832fa7366bb5918ada993c0f14750d90ec7da41c1010ecdc2f4a6685611e1f2"
BAR_FLOW_5M = "bar-flow-definition-v1:sha256:aa6690f516522b0835e057add31469527dc7522a82277ba92923d2964cb4818e"
FEATURE_DEFAULT = "feature-definition-v1:sha256:80230bfe832fb8f17f7112a5112f93e3079f29dc0cdb6c2985d4edf65b460ae3"
FEATURE_0_4 = "feature-definition-v1:sha256:ba902411b9ad02e293613a937e611a0f60f4ca931b5aee5041011f4203244693"


def entry(*, drop=(), **overrides):
    value = {"feature_key": KEY, "semantic_version": "1", "bar_duration": "1m", "parameters": {}}
    value.update(overrides)
    for name in drop:
        del value[name]
    return value


class AdmittedInputDefinitionsV1Tests(unittest.TestCase):
    # The K12 authority fixture: real files, a real DataGateway, a SQLite admission store.
    setUp = k12.AdmittedInputAuthorityV1Tests.setUp
    make_partition = k12.AdmittedInputAuthorityV1Tests.make_partition
    gateway = k12.AdmittedInputAuthorityV1Tests.gateway
    build = k12.AdmittedInputAuthorityV1Tests.build
    transport = k12.AdmittedInputAuthorityV1Tests.transport
    admit = k12.AdmittedInputAuthorityV1Tests.admit
    body = k12.AdmittedInputAuthorityV1Tests.body

    def decoded(self, *entries, **body):
        transport = self.transport()
        return transport.decode("admit", self.body(definitions=list(entries), **body))

    def reason(self, *entries, code=ConsumerErrorCode.INVALID_REQUEST, **body) -> str:
        with self.assertRaises(ConsumerApiError) as raised:
            self.decoded(*entries, **body)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual("the request is not valid", raised.exception.message)
        return raised.exception.context["reason"]

    def resolved(self, *entries):
        return self.authority.resolve(self.decoded(*entries, profile_identity="profile-v1:default"))

    # -- the closed registry -----------------------------------------------------

    def test_the_registry_is_closed_and_has_one_entry(self) -> None:
        self.assertEqual(((KEY, "1"),), registry.REGISTERED_DEFINITIONS)
        with self.assertRaises(TypeError):
            registry._REGISTRY[("other.feature", "1")] = registry._REGISTRY[(KEY, "1")]  # type: ignore[index]
        definition = registry._REGISTRY[(KEY, "1")].build({})
        self.assertEqual((KEY, "1"), (definition.feature_key, definition.semantic_version))

    def test_the_eight_reasons_are_the_adr_set(self) -> None:
        self.assertEqual(
            (
                "definitions_exceed_bound", "definition_unknown", "definition_parameters_invalid",
                "definition_bar_duration_invalid", "definition_interval_misaligned",
                "definition_bar_duration_mixed", "definition_duplicate", "definition_input_unavailable",
            ),
            registry.DEFINITION_REFUSAL_REASONS,
        )
        with self.assertRaises(ValueError):
            registry.DefinitionRefusal("anything_else")

    def test_a_default_entry_is_the_pinned_bar_flow_and_feature_identities(self) -> None:
        (definition,) = self.decoded(entry()).definitions
        self.assertEqual((KEY, "1", 60_000_000_000, ()), (
            definition.feature_key, definition.semantic_version, definition.bar_duration_ns, definition.parameters,
        ))
        self.assertEqual((BAR_FLOW_1M, FEATURE_DEFAULT), definition.identities())

    def test_the_server_canonicalizes_what_the_deck_spells(self) -> None:
        explicit = {"imbalance_threshold": "0.3", "wick_ratio": "0.5"}
        for spelled in ("1m", "60s", "00:01:00", "60000000000"):
            (definition,) = self.decoded(entry(bar_duration=spelled, parameters=explicit)).definitions
            self.assertEqual((BAR_FLOW_1M, FEATURE_DEFAULT), definition.identities(), spelled)
        # An omitted parameter takes the definition's default; a spelling of the same number is the same definition.
        for parameters in ({}, {"imbalance_threshold": "0.30"}, {"wick_ratio": "0.500"}):
            (definition,) = self.decoded(entry(parameters=parameters)).definitions
            self.assertEqual(FEATURE_DEFAULT, definition.identities()[1])
        (changed,) = self.decoded(entry(parameters={"imbalance_threshold": "0.4"})).definitions
        self.assertEqual((BAR_FLOW_1M, FEATURE_0_4), changed.identities())

    def test_entries_with_the_same_duration_and_other_parameters_are_valid(self) -> None:
        request_ = self.decoded(entry(), entry(parameters={"imbalance_threshold": "0.4"}), entry(bar_duration="60s", parameters={"wick_ratio": "0.25"}))
        self.assertEqual(3, len(request_.definitions))
        identities = registry.definition_identities(request_.definitions)
        self.assertEqual(4, len(identities))  # one bar-flow, three features
        self.assertEqual(1, sum(item.startswith("bar-flow-definition-v1:") for item in identities))
        self.assertEqual(list(identities), sorted(identities))

    def test_absent_and_empty_definitions_decode_to_the_request_of_today(self) -> None:
        transport = self.transport()
        plain = transport.decode("admit", self.body())
        empty = transport.decode("admit", self.body(definitions=[]))
        self.assertEqual(plain, empty)
        self.assertEqual((), plain.definitions)
        self.assertEqual(AdmittedInputRequest(plain.dataset, (), "strategy-compose-v1", None), plain)

    # -- the eight refusals --------------------------------------------------------

    def test_more_than_eight_entries_are_refused_before_any_entry_is_read(self) -> None:
        nine = [entry(parameters={"imbalance_threshold": f"0.{index + 1}"}) for index in range(9)]
        self.assertEqual("definitions_exceed_bound", self.reason(*nine))
        self.assertEqual("definitions_exceed_bound", self.reason(*([{"nonsense": 1}] * 9)))
        eight = [entry(parameters={"imbalance_threshold": f"0.{index + 1}"}) for index in range(8)]
        self.assertEqual(8, len(self.decoded(*eight).definitions))

    def test_an_unknown_key_or_version_is_refused(self) -> None:
        for override in (
            {"feature_key": "order_flow.other"}, {"semantic_version": "2"}, {"semantic_version": "1.0"},
            {"feature_key": "ORDER_FLOW.FAILED_AGGRESSION"}, {"feature_key": "imbalance.diagonal"},
        ):
            self.assertEqual("definition_unknown", self.reason(entry(**override)), override)

    def test_a_bad_parameter_is_refused(self) -> None:
        for parameters in (
            {"unknown": "0.3"}, {"imbalance_threshold": "abc"}, {"imbalance_threshold": "NaN"},
            {"imbalance_threshold": "Infinity"}, {"imbalance_threshold": "-0.1"},
            {"imbalance_threshold": "0"}, {"imbalance_threshold": "1.5"}, {"imbalance_threshold": "1e-1"},
            {"imbalance_threshold": " 0.3"}, {"imbalance_threshold": ""}, {"imbalance_threshold": "0." + "1" * 40},
            {"imbalance_threshold": 0.3}, {"imbalance_threshold": 1}, {"imbalance_threshold": True},
            {"imbalance_threshold": None}, {"wick_ratio": "1"}, {"wick_ratio": "-0.0"}, {"wick_ratio": "2"},
            ["0.3"], "0.3",
        ):
            self.assertEqual("definition_parameters_invalid", self.reason(entry(parameters=parameters)), parameters)

    def test_the_wire_spelling_has_no_digit_ceiling_of_its_own(self) -> None:
        from quant_platform.features.failed_aggression import failed_aggression_definition

        # Equivalent spellings of an exactly held decimal are admitted and are the constructor's identity.
        for spelling in ("0.3" + "0" * 40, "0.3" + "0" * 100_000, "0000.3", "0." + "0" * 40 + "1", "0." + "0" * 1000 + "1"):
            expected = failed_aggression_definition(imbalance_threshold=spelling).identity
            (definition,) = self.decoded(entry(parameters={"imbalance_threshold": spelling})).definitions
            self.assertEqual(expected, definition.feature_definition().identity)
        (default,) = self.decoded(entry(parameters={"imbalance_threshold": "0.3" + "0" * 40})).definitions
        self.assertEqual(FEATURE_DEFAULT, default.feature_definition().identity)
        # What the constructor cannot hold exactly, or the domain excludes, is still refused (not by spelling length).
        for spelling in ("0." + "1" * 40, "9" * 5000, "0." + "0" * 2_000_000 + "1" * 30, "1" + "0" * 40):
            self.assertEqual("definition_parameters_invalid", self.reason(entry(parameters={"imbalance_threshold": spelling})), spelling[:12])

    def test_the_domain_ends_the_constructor_enforces_are_enforced(self) -> None:
        (edge,) = self.decoded(entry(parameters={"imbalance_threshold": "1", "wick_ratio": "0"})).definitions
        self.assertEqual((("imbalance_threshold", "1"), ("wick_ratio", "0")), edge.parameters)
        (small,) = self.decoded(entry(parameters={"imbalance_threshold": "0.0001"})).definitions
        self.assertEqual(2, len(small.identities()))

    def test_a_bad_bar_duration_is_refused(self) -> None:
        for override in (
            {"bar_duration": None}, {"bar_duration": ""}, {"bar_duration": "soon"}, {"bar_duration": "0s"},
            {"bar_duration": "999ms"}, {"bar_duration": "1000ms2"}, {"bar_duration": "2d"}, {"bar_duration": "86401s"},
            {"bar_duration": 60}, {"bar_duration": True}, {"bar_duration": ["1m"]}, {"bar_duration": "-1m"},
        ):
            self.assertEqual("definition_bar_duration_invalid", self.reason(entry(**override)), override)
        self.assertEqual("definition_bar_duration_invalid", self.reason(entry(drop=("bar_duration",))))
        body = self.body()
        body["dataset"] = {**body["dataset"], "start": "2024-01-01T00:00:00Z", "end": "2024-01-03T00:00:00Z"}
        for good in ("1s", "1000ms", "86400s", "1d", "24h"):  # the bounds are inclusive
            self.transport().decode("admit", {**body, "definitions": [entry(bar_duration=good)]})

    def test_an_interval_not_aligned_to_the_duration_is_refused(self) -> None:
        for dataset in (
            {"start": "2024-01-01T00:00:30Z"}, {"end": "2024-01-01T01:59:59Z"}, {"start": "2024-01-01T00:00:00.5Z"},
            {"end": "2024-01-01T02:00:00.000000001Z"},
        ):
            body = self.body()
            body["dataset"] = {**body["dataset"], **dataset}
            with self.assertRaises(ConsumerApiError) as raised:
                self.transport().decode("admit", {**body, "definitions": [entry()]})
            self.assertEqual("definition_interval_misaligned", raised.exception.context["reason"], dataset)
        # 02:00 is not a multiple of one day since the UTC epoch; midnight is.
        self.assertEqual("definition_interval_misaligned", self.reason(entry(bar_duration="1d")))
        body = self.body()
        body["dataset"] = {**body["dataset"], "start": "2024-01-01T00:00:00Z", "end": "2024-01-03T00:00:00Z"}
        self.transport().decode("admit", {**body, "definitions": [entry(bar_duration="1d")]})
        # One entry aligned and another not: the misaligned one decides.
        for duration in ("1m", "5m", "1h", "2h"):
            self.decoded(entry(bar_duration=duration))
        self.assertEqual("definition_interval_misaligned", self.reason(entry(bar_duration="3h")))

    def test_an_interval_that_is_not_a_utc_interval_is_a_shape_refusal_not_a_definition_reason(self) -> None:
        body = self.body()
        body["dataset"] = {**body["dataset"], "start": "yesterday"}
        with self.assertRaises(ConsumerApiError) as raised:
            self.transport().decode("admit", {**body, "definitions": [entry()]})
        self.assertEqual("invalid_request_shape", raised.exception.context["reason"])

    def test_two_durations_in_one_admission_are_refused_by_nanosecond_value(self) -> None:
        self.assertEqual("definition_bar_duration_mixed", self.reason(entry(), entry(bar_duration="5m")))
        self.assertEqual(
            "definition_bar_duration_mixed",
            self.reason(entry(), entry(bar_duration="5m", parameters={"wick_ratio": "0.2"})),
        )
        self.decoded(entry(bar_duration="1m"), entry(bar_duration="60s", parameters={"wick_ratio": "0.2"}))

    def test_mixed_is_decided_after_each_entry_has_passed_its_own_validation(self) -> None:
        # An invalid second entry is its own reason, whatever the first entry's duration is.
        self.assertEqual("definition_bar_duration_invalid", self.reason(entry(), entry(bar_duration="soon")))
        self.assertEqual("definition_parameters_invalid", self.reason(entry(), entry(bar_duration="5m", parameters={"x": "1"})))
        self.assertEqual("definition_unknown", self.reason(entry(), entry(feature_key="x", bar_duration="5m")))
        self.assertEqual("definition_interval_misaligned", self.reason(entry(), entry(bar_duration="3h")))

    def test_two_entries_with_the_same_identities_are_a_duplicate(self) -> None:
        self.assertEqual("definition_duplicate", self.reason(entry(), entry()))
        self.assertEqual("definition_duplicate", self.reason(entry(), entry(bar_duration="60s", parameters={"imbalance_threshold": "0.3"})))
        self.assertEqual(
            "definition_duplicate",
            self.reason(entry(parameters={"wick_ratio": "0.5"}), entry(parameters={"wick_ratio": "0.50", "imbalance_threshold": "0.300"})),
        )

    def test_a_dataset_that_is_not_the_required_one_is_input_unavailable(self) -> None:
        body = self.body(artifact_identities=["feature-artifact-v1:sha256:" + "ab" * 32], dataset=None)
        with self.assertRaises(ConsumerApiError) as raised:
            self.transport().decode("admit", {**body, "definitions": [entry()]})
        self.assertEqual("definition_input_unavailable", raised.exception.context["reason"])
        for representation in ({"kind": "candles", "version": 1}, {"kind": "trades", "version": 2}):
            body = self.body()
            body["dataset"] = {**body["dataset"], "representation": representation}
            with self.assertRaises(ConsumerApiError) as raised:
                self.transport().decode("admit", {**body, "definitions": [entry()]})
            self.assertEqual("definition_input_unavailable", raised.exception.context["reason"], representation)

    def test_the_reason_is_the_first_failing_entry_in_order(self) -> None:
        self.assertEqual("definition_unknown", self.reason(entry(feature_key="x", parameters={"bad": "1"}, bar_duration="soon")))
        self.assertEqual("definition_parameters_invalid", self.reason(entry(parameters={"bad": "1"}, bar_duration="soon")))
        self.assertEqual("definition_bar_duration_invalid", self.reason(entry(bar_duration="soon")))

    def test_a_shape_that_is_not_the_closed_set_is_an_ordinary_invalid_request(self) -> None:
        for definitions in (None, {"feature_key": KEY}, "x", [None], [[]], [entry(extra=1)], [entry(drop=("feature_key",))], [entry(feature_key=3)]):
            with self.assertRaises(ConsumerApiError) as raised:
                self.transport().decode("admit", self.body(definitions=definitions))
            self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, raised.exception.code)
            self.assertEqual("invalid_request_shape", raised.exception.context["reason"], definitions)

    def test_a_locator_anywhere_in_a_definition_is_refused_as_a_locator(self) -> None:
        for definitions in (
            [entry(parameters={"storage_root": "0.3"})], [entry(filePath="x")], [entry(feature_key="/etc/passwd")],
            [entry(feature_key="C:\\data")], [entry(semantic_version="../1")],
            [entry(bar_duration="/etc/passwd")],
        ):
            with self.assertRaises(ConsumerApiError) as raised:
                self.transport().decode("admit", self.body(definitions=definitions))
            self.assertEqual("locator_not_accepted", raised.exception.context["reason"], definitions)

    # -- the manifest ----------------------------------------------------------------

    def test_the_manifest_holds_the_sorted_deduplicated_identities(self) -> None:
        manifest = self.resolved(entry(), entry(parameters={"imbalance_threshold": "0.4"}))
        self.assertEqual(tuple(sorted({BAR_FLOW_1M, FEATURE_DEFAULT, FEATURE_0_4})), tuple(sorted(manifest.definition_identities)))
        self.assertEqual(3, len(manifest.definition_identities))
        self.assertEqual(manifest.definition_identities, tuple(sorted(manifest.definition_identities)))
        five = self.resolved(entry(bar_duration="5m"))
        self.assertEqual(sorted((BAR_FLOW_5M, FEATURE_DEFAULT)), list(five.definition_identities))

    def test_the_manifest_holds_at_most_one_bar_flow_identity(self) -> None:
        manifest = self.resolved(entry(), entry(bar_duration="60s", parameters={"wick_ratio": "0.1"}))
        self.assertEqual(1, sum(item.startswith("bar-flow-definition-v1:") for item in manifest.definition_identities))

    def test_a_request_without_definitions_seals_the_manifest_of_today(self) -> None:
        manifest = self.authority.resolve(k12.request())
        self.assertEqual((), manifest.definition_identities)
        self.assertEqual(PRE_CHANGE_DIGEST, manifest.manifest_digest)
        self.assertEqual("admitted-input-v1:" + PRE_CHANGE_DIGEST, manifest.admission_id)
        # Through the wire, absent and empty `definitions` are the same admission.
        transport = self.transport()
        profile = {"profile_identity": "profile-v1:default"}
        for body in (self.body(**profile), self.body(definitions=[], **profile)):
            admitted = self.admit(transport, body)
            self.assertEqual(PRE_CHANGE_DIGEST, admitted["manifest_digest"])
            self.assertEqual("admitted-input-v1:" + PRE_CHANGE_DIGEST, admitted["admission_id"])
            self.assertEqual([], admitted["manifest"]["definition_identities"])

    def test_naming_definitions_is_another_admission_and_nothing_else_changes(self) -> None:
        plain = self.authority.resolve(k12.request())
        named = self.resolved(entry())
        again = self.resolved(entry())
        self.assertEqual(named.admission_id, again.admission_id)
        self.assertNotEqual(plain.admission_id, named.admission_id)
        self.assertNotEqual(named.admission_id, self.resolved(entry(parameters={"wick_ratio": "0.4"})).admission_id)
        self.assertNotEqual(named.admission_id, self.resolved(entry(bar_duration="5m")).admission_id)
        before, after = plain.canonical_fields(), named.canonical_fields()
        self.assertEqual(
            {name: value for name, value in before.items() if name != "definition_identities"},
            {name: value for name, value in after.items() if name != "definition_identities"},
        )
        self.assertEqual(k12.COMMIT, named.git_identity)

    def test_the_server_seals_and_re_serves_the_identities_without_changing_delivery(self) -> None:
        transport = self.transport()
        admitted = self.admit(transport, self.body(definitions=[entry()]))
        self.assertEqual("SEALED", admitted["state"].upper())
        self.assertEqual(sorted((BAR_FLOW_1M, FEATURE_DEFAULT)), admitted["manifest"]["definition_identities"])
        served = transport.execute("manifest", transport.decode("manifest", {"admission_id": admitted["admission_id"]}))
        self.assertEqual(admitted["manifest"], served["manifest"])
        self.assertEqual(admitted["manifest_digest"], served["manifest_digest"])
        delivered = transport.execute("deliver", transport.decode("deliver", {"admission_id": admitted["admission_id"]}))
        self.assertEqual(2, len(delivered["members"]))  # the same partitions as without definitions
        self.assertEqual(self.admit(transport, self.body(definitions=[entry()]))["admission_id"], admitted["admission_id"])

    def test_a_refused_definition_runs_no_seam_and_seals_nothing(self) -> None:
        calls = []
        resolve = self.authority.resolve
        self.authority.resolve = lambda item: calls.append(item) or resolve(item)  # type: ignore[method-assign]
        transport = self.transport()
        for entries in ([entry(), entry(bar_duration="5m")], [entry(feature_key="x")], [entry(bar_duration="3h")]):
            with self.assertRaises(ConsumerApiError):
                self.admit(transport, self.body(definitions=entries))
        self.assertEqual([], calls)
        with self.session() as store:
            for identity in (self.authority.resolve(k12.request()).admission_id, self.resolved(entry()).admission_id):
                with self.assertRaises(AdmittedInputNotFound):
                    store.get(identity)

    def test_an_authority_that_does_not_carry_the_definitions_cannot_seal(self) -> None:
        # Dropping, adding or altering an identity is an integrity failure before anything is sealed.
        resolve = self.authority.resolve
        tamperings = {
            "dropped": lambda manifest: dataclasses.replace(manifest, definition_identities=()),
            "added": lambda manifest: dataclasses.replace(manifest, definition_identities=(*manifest.definition_identities, FEATURE_0_4)),
            "altered": lambda manifest: dataclasses.replace(manifest, definition_identities=(BAR_FLOW_1M, FEATURE_0_4)),
        }
        for name, tamper in tamperings.items():
            transport = self.transport(authority=mock.Mock(
                resolve=lambda item, tamper=tamper: tamper(resolve(item)),
                members=self.authority.members, read_member=self.authority.read_member,
            ))
            with self.assertRaises(ConsumerApiError, msg=name) as raised:
                self.admit(transport, self.body(definitions=[entry()]))
            self.assertEqual(ConsumerErrorCode.INTEGRITY_FAILURE, raised.exception.code, name)
            self.assertEqual("definition_identities_mismatch", raised.exception.context["reason"], name)
            with self.session() as store:
                with self.assertRaises(AdmittedInputNotFound):
                    store.get(tamper(resolve(self.decoded(entry()))).admission_id)

    # -- mutation checks: the guards above fail when the property is weakened ---------

    def test_mutant_an_authority_that_forgets_the_identities_is_caught(self) -> None:
        with mock.patch.object(authority_module, "definition_identities", lambda _definitions: ()):
            transport = self.transport()
            with self.assertRaises(ConsumerApiError) as raised:
                self.admit(transport, self.body(definitions=[entry()]))
            self.assertEqual("definition_identities_mismatch", raised.exception.context["reason"])

    def test_mutant_identities_that_leak_into_a_request_without_definitions_are_caught(self) -> None:
        with mock.patch.object(authority_module, "definition_identities", lambda _definitions: (FEATURE_DEFAULT,)):
            self.assertNotEqual(PRE_CHANGE_DIGEST, self.authority.resolve(k12.request()).manifest_digest)

    def test_mutant_a_registry_that_accepts_mixed_durations_is_caught(self) -> None:
        # Weakening the Amendment 1 rule makes the mixed request decode; the rule's tests would then fail.
        original = registry.canonicalize_definitions

        def without_the_rule(entries, dataset):
            durations = {item.get("bar_duration") for item in entries}
            if len(durations) > 1:
                return tuple(original([item], dataset)[0] for item in entries)
            return original(entries, dataset)

        with mock.patch("quant_platform.application.admitted_input_transport.canonicalize_definitions", without_the_rule):
            mixed = self.decoded(entry(), entry(bar_duration="5m"))
        self.assertEqual(2, len(mixed.definitions))
        self.assertEqual("definition_bar_duration_mixed", self.reason(entry(), entry(bar_duration="5m")))


if __name__ == "__main__":
    unittest.main()

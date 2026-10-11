"""Application-owned J10 canonical codec and opt-in authenticated J02 route.

Only ``compose_decision`` is carried. Execution-policy data is opaque identity
evidence (ADR-0051), never an executable or a locator resolved by the server.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..data.models import Instant
from ..strategy import (
    CapitalRiskPolicy, CooldownPolicyDefinition, EntryPolicy, ExitPolicy,
    FixedFractionSizingPolicy, PositionPolicy, SessionPolicyDefinition,
    SignalCombinationPolicy, StrategyCompositionResult, StrategyInput, StrategySpec,
)
from .api_transport_server import J02FamilyHandler
from .market_data import ConsumerApiError, ConsumerErrorCode
from .strategy_consumer import (
    STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN, StrategyComposeRequest,
    execute_strategy_compose,
)


J02_STRATEGY_FAMILY = "j02-strategy-v1"
J02_STRATEGY_REQUEST_VERSION = J02_STRATEGY_FAMILY + "-request-v1"
J02_STRATEGY_RESPONSE_VERSION = J02_STRATEGY_FAMILY + "-response-v1"


def _decimal_values(value: Any, path: list[str | int]) -> list[dict[str, Any]]:
    if isinstance(value, Decimal):
        return [{"path": path, "value": str(value)}]
    if isinstance(value, Mapping):
        return [nested for key in sorted(value) for nested in _decimal_values(value[key], path + [key])]
    if isinstance(value, (list, tuple)):
        return [nested for index, item in enumerate(value) for nested in _decimal_values(item, path + [index])]
    return []


def encode_strategy_compose_request(request: StrategyComposeRequest) -> dict[str, Any]:
    """Encode J10 with separate Decimal evidence approved in issue #337.

    ``input_decimal_values`` is optional transport-only evidence: an input key,
    a path of object keys/array indices to each Decimal leaf (``[]`` means the
    whole input value), and its original Decimal text. Normalization can lose
    not just type but numeric precision, so reconstruction needs both. Unmarked
    strings stay literal. The reconstructed value must reproduce the canonical
    body. This sidecar is never part of a domain canonical payload or identity;
    the legacy identity alone therefore cannot attest its type/value evidence.
    """
    body = request.canonical_payload()
    values = [
        {"input_key": item.key, **evidence}
        for item in sorted(request.inputs, key=lambda item: item.key)
        for evidence in _decimal_values(item.value, [])
    ]
    if values:
        body["input_decimal_values"] = values
    return body


def _restore_decimal(value: Any, path: list[str | int], decimal: Decimal) -> Any:
    if not path:
        if type(value) is not str:
            raise ValueError("Decimal evidence must name a canonical string")
        return decimal
    key, *rest = path
    if type(value) is dict and type(key) is str and key in value:
        value[key] = _restore_decimal(value[key], rest, decimal)
    elif type(value) is list and type(key) is int and 0 <= key < len(value):
        value[key] = _restore_decimal(value[key], rest, decimal)
    else:
        raise ValueError("Decimal evidence does not resolve to an input value")
    return value


def _input_decimal_values(body: Mapping[str, Any]) -> dict[str, list[tuple[list[str | int], Decimal]]]:
    evidence = body.get("input_decimal_values", [])
    if type(evidence) is not list:
        raise ValueError("Decimal evidence must be an array")
    grouped: dict[str, list[tuple[list[str | int], Decimal]]] = {}
    seen = set()
    for value in evidence:
        item = _object(value, {"input_key", "path", "value"})
        key, path = item["input_key"], item["path"]
        if type(key) is not str or not key or type(path) is not list or any(
            not (type(part) is str and part or type(part) is int and part >= 0)
            for part in path
        ):
            raise ValueError("invalid Decimal evidence")
        if type(item["value"]) is not str:
            raise ValueError("Decimal text required")
        decimal = Decimal(item["value"])
        if str(decimal) != item["value"]:
            raise ValueError("non-canonical Decimal text")
        location = (key, tuple(path))
        if location in seen:
            raise ValueError("duplicate Decimal evidence")
        seen.add(location)
        grouped.setdefault(key, []).append((path, decimal))
    return grouped


def _object(value: Any, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("canonical object fields do not match")
    return value


def _json_value(value: Any) -> Any:
    """Copy only lossless JSON data, without coercing Python executable objects."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is list:
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping) and all(type(key) is str and key for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("canonical JSON data required")


def _same_json(actual: Any, expected: Any) -> bool:
    # Python equality alone would silently admit True in place of integer 1.
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _same_json(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _same_json(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _require_canonical(actual: Any, expected: Any) -> None:
    if not _same_json(_json_value(actual), expected):
        raise ValueError("payload is not canonical")


@dataclass(frozen=True, slots=True)
class _ExecutionPolicyEvidence:
    identity: str
    _payload: Mapping[str, Any]

    def stable_dict(self) -> Mapping[str, Any]:
        return _json_value(self._payload)


def _policy(value: Any, constructor: Any, parameters: set[str], *, wrapped: bool) -> Any:
    wrapper = _object(value, {"identity", "payload"}) if wrapped else None
    payload = _object(wrapper["payload"] if wrapper else value, parameters | {"policy_type"})
    policy = constructor(**{key: payload[key] for key in parameters})
    _require_canonical(payload, policy.stable_dict())
    if wrapper is not None and wrapper["identity"] != policy.identity:
        raise ValueError("policy content identity mismatch")
    return policy


def _decode_spec(value: Any) -> StrategySpec:
    payload = _object(value, {
        "identity_type", "identity_version", "strategy_key", "semantic_version",
        "entry_policy", "exit_policy", "position_policy", "sizing_policy", "risk_policy",
        "session_policy", "cooldown_policy", "signal_combination_policy", "execution_policy",
    })
    execution = _object(payload["execution_policy"], {"identity", "payload"})
    if not isinstance(execution["identity"], str) or not execution["identity"].strip():
        raise ValueError("execution policy identity required")
    if not isinstance(execution["payload"], Mapping):
        raise ValueError("execution policy canonical payload required")
    # Its internal shape and identity algorithm belong to the consumer, not J10.
    evidence = _ExecutionPolicyEvidence(execution["identity"], _json_value(execution["payload"]))
    spec = StrategySpec(
        strategy_key=payload["strategy_key"], semantic_version=payload["semantic_version"],
        entry_policy=_policy(payload["entry_policy"], EntryPolicy, {
            "policy_key", "direction", "signal_key", "confidence", "urgency",
        }, wrapped=False),
        exit_policy=_policy(payload["exit_policy"], ExitPolicy, {
            "policy_key", "signal_key",
        }, wrapped=False),
        position_policy=_policy(payload["position_policy"], PositionPolicy, {
            "policy_key", "long_target_position", "short_target_position", "flat_target_position",
        }, wrapped=False),
        sizing_policy=_policy(payload["sizing_policy"], FixedFractionSizingPolicy, {
            "policy_key", "semantic_version", "lot_size", "min_size", "max_size",
        }, wrapped=True),
        risk_policy=_policy(payload["risk_policy"], CapitalRiskPolicy, {
            "policy_key", "semantic_version", "max_drawdown_fraction",
            "max_position_notional_fraction", "max_total_exposure_fraction",
            "risk_per_trade_fraction", "max_evidence_age_seconds",
        }, wrapped=True),
        session_policy=_policy(payload["session_policy"], SessionPolicyDefinition, {
            "policy_key", "semantic_version", "calendar_version", "reference_markets",
        }, wrapped=True),
        cooldown_policy=_decode_cooldown(payload["cooldown_policy"]),
        signal_combination_policy=_policy(payload["signal_combination_policy"], SignalCombinationPolicy, {
            "policy_key", "mode", "signal_keys",
        }, wrapped=False),
        execution_policy=evidence,
    )
    _require_canonical(payload, spec.canonical_payload())
    return spec


def _decode_cooldown(value: Any) -> CooldownPolicyDefinition:
    wrapper = _object(value, {"identity", "payload"})
    parameters = {
        "policy_key", "semantic_version", "post_loss_cooldown_seconds",
        "consecutive_loss_count", "consecutive_loss_cooldown_seconds",
        "max_entries_per_utc_day", "max_trade_history_age_seconds",
    }
    payload = _object(wrapper["payload"], parameters | {
        "policy_type", "restrictions_are_upper_bounds_only", "cooldown_blocks_new_entries_only",
    })
    policy = CooldownPolicyDefinition(**{key: payload[key] for key in parameters})
    _require_canonical(payload, policy.stable_dict())
    if wrapper["identity"] != policy.identity:
        raise ValueError("cooldown content identity mismatch")
    return policy


def decode_strategy_compose_request(payload: Mapping[str, Any]) -> StrategyComposeRequest:
    """Rebuild the owned domain values, rejecting mutations before execution."""
    try:
        keys = {
            "identity_domain", "strategy_spec", "strategy_identity", "inputs",
            "decision_time", "instrument",
        }
        if "input_decimal_values" in payload:
            keys.add("input_decimal_values")
        body = _object(payload, keys)
        if body["identity_domain"] != STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN:
            raise ValueError("unsupported strategy request")
        spec = _decode_spec(body["strategy_spec"])
        if body["strategy_identity"] != spec.strategy_identity:
            raise ValueError("strategy content identity mismatch")
        if type(body["inputs"]) is not list:
            raise ValueError("inputs must be a canonical array")
        decimal_values = _input_decimal_values(body)
        inputs = []
        for value in body["inputs"]:
            item = _object(value, {"key", "value", "available_at", "provenance"})
            input_value = _json_value(item["value"])
            for path, decimal in decimal_values.pop(item["key"], []):
                input_value = _restore_decimal(input_value, path, decimal)
            decoded = StrategyInput(
                key=item["key"], value=input_value,
                available_at=item["available_at"], provenance=item["provenance"],
            )
            _require_canonical(item, decoded.stable_dict())
            inputs.append(decoded)
        if decimal_values:
            raise ValueError("Decimal evidence names an unknown input")
        if len({item.key for item in inputs}) != len(inputs):
            raise ValueError("duplicate strategy input key")
        decision_time = Instant.parse(body["decision_time"])
        _require_canonical(body["decision_time"], decision_time.isoformat())
        if not isinstance(body["instrument"], str) or not body["instrument"].strip():
            raise ValueError("instrument required")
        return StrategyComposeRequest(
            spec, body["strategy_identity"], tuple(inputs), decision_time, body["instrument"],
        )
    except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
        raise ConsumerApiError(
            ConsumerErrorCode.INVALID_REQUEST, "the request is not valid", request_identity=None,
        ) from exc


def encode_strategy_composition_result(result: StrategyCompositionResult) -> dict[str, Any]:
    """Carry the existing DecisionIntent/NoDecision payload and identities unchanged."""
    return result.stable_dict()


def strategy_family_handlers() -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicitly compose J10 into the mTLS server; no other family is installed."""
    return {
        (J02_STRATEGY_FAMILY, J02_STRATEGY_REQUEST_VERSION, "compose"): J02FamilyHandler(
            "j02.strategy.compose", J02_STRATEGY_RESPONSE_VERSION,
            decode_strategy_compose_request,
            lambda request: encode_strategy_composition_result(execute_strategy_compose(request)),
        ),
    }

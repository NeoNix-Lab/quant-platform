"""Identity-preserving JSON byte profiles (ADR-0068 section 5).

Callers own normalization and, for JSONL, field order. These profiles only
serialize already-normalized values; they never choose domain semantics.
"""

from __future__ import annotations

import json
from typing import Any, Literal


CanonicalProfile = Literal[
    "sorted-compact-ascii-v1",
    "sorted-compact-utf8-v1",
    "ordered-compact-ascii-line-v1",
    "rfc8785-v1",
]


def canonical_bytes(
    value: Any, *, profile: CanonicalProfile, allow_nan: bool = True
) -> bytes:
    """Return the named profile's bytes without normalizing input values.

    ``allow_nan`` preserves each legacy JSON site's admission policy. RFC 8785
    always rejects non-finite numbers, as required by that standard.
    """
    if profile == "rfc8785-v1":
        # Legacy JSON identities remain usable without loading the J14 codec.
        import rfc8785

        return rfc8785.dumps(value)
    if profile not in (
        "sorted-compact-ascii-v1",
        "sorted-compact-utf8-v1",
        "ordered-compact-ascii-line-v1",
    ):
        raise ValueError(f"unknown canonical byte profile: {profile}")
    encoded = json.dumps(
        value,
        sort_keys=profile != "ordered-compact-ascii-line-v1",
        separators=(",", ":"),
        ensure_ascii=profile != "sorted-compact-utf8-v1",
        allow_nan=allow_nan,
    ).encode("utf-8")
    return encoded + b"\n" if profile == "ordered-compact-ascii-line-v1" else encoded

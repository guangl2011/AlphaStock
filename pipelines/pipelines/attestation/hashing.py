"""Canonical hashing for signal objects and attestation batches."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

_HEX_ALPHABET = "0123456789abcdef"


def canonical_json(obj: Mapping[str, object]) -> bytes:
    """UTF-8 canonical JSON: sorted keys, no whitespace — stable across dict orderings."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return payload.encode("utf-8")


def signal_hash(signal: Mapping[str, object]) -> str:
    """SHA-256 of the canonical JSON encoding of one signal object."""
    return hashlib.sha256(canonical_json(signal)).hexdigest()


def batch_hash(signals: Sequence[Mapping[str, object]]) -> str:
    """SHA-256 anchor for a batch of signals.

    Stable for identical input regardless of list order: each signal is hashed
    canonically, the per-signal digests are sorted, and the newline-joined
    digest text is hashed. The empty batch hashes the empty string.
    """
    digest_text = "\n".join(sorted(signal_hash(s) for s in signals))
    return hashlib.sha256(digest_text.encode("utf-8")).hexdigest()


def is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(c in _HEX_ALPHABET for c in value)

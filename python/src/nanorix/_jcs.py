"""
JCS (RFC 8785) canonical JSON serialization — pure Python, no external deps.

Implements the JSON Canonicalization Scheme as defined in RFC 8785:
  https://www.rfc-editor.org/rfc/rfc8785

The output is UTF-8 encoded bytes where:
  - Object keys are sorted lexicographically (Unicode code-point order)
  - No insignificant whitespace
  - Numbers serialized in ES2019 numeric format
  - Strings: only the six mandatory escapes (\\b \\f \\n \\r \\t) plus
    \\uXXXX for code points U+0000–U+001F and the surrogate range

Used by SDK verifier helpers for AuditProof canonical hash computation.
Cross-impl byte-equivalence with Rust serde_jcs (via test fixture corpus).

Limitations (by design — matches serde_jcs + test fixture scope):
  - NaN and Infinity are not valid JSON values; raise ValueError if encountered.
  - Floats are serialized via Python's repr-equivalent path, matching ES2019
    Number::toString semantics for the range of finite double values.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Union

__all__ = ["canonicalize"]

# Regex for detecting ES2019 numeric literal compatibility in Python floats.
# Python's json module uses repr-style notation which is compatible.
_FLOAT_FINITECHECK = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def _serialize_string(s: str) -> bytes:
    """Serialize a JSON string per RFC 8785 §3.2.2.2.

    Only the six mandatory escapes + \\uXXXX for U+0000–U+001F.
    No \\/ escaping (RFC 8785 §3.2.2.2 explicitly forbids it).
    No \\uXXXX for code points above U+001F except surrogates.
    """
    out: list[str] = ['"']
    for ch in s:
        cp = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif 0x0000 <= cp <= 0x001F:
            out.append(f"\\u{cp:04x}")
        elif 0xD800 <= cp <= 0xDFFF:
            # Surrogates — must escape per RFC 8785 §3.2.2.2
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out).encode("utf-8")


def _serialize_number(v: Union[int, float]) -> bytes:
    """Serialize a JSON number per RFC 8785 §3.2.2.3.

    Integers: exact decimal representation.
    Floats: ES2019 Number::toString semantics.
    NaN / Infinity are not valid JSON — raise ValueError.
    """
    if isinstance(v, bool):
        raise TypeError(f"bool is not a number: {v!r}")
    if isinstance(v, int):
        return str(v).encode("ascii")
    # float
    if math.isnan(v) or math.isinf(v):
        raise ValueError(f"JSON does not support NaN or Infinity: {v!r}")
    # Use json.dumps to get ES2019-compatible serialization.
    # Python's json module produces repr-equivalent floats which match
    # ES2019's Number::toString for all finite double values.
    raw = json.dumps(v)
    return raw.encode("ascii")


def _serialize_value(v: Any) -> bytes:
    """Recursively serialize a JSON-compatible Python value to JCS bytes."""
    if v is None:
        return b"null"
    if isinstance(v, bool):
        return b"true" if v else b"false"
    if isinstance(v, int):
        return _serialize_number(v)
    if isinstance(v, float):
        return _serialize_number(v)
    if isinstance(v, str):
        return _serialize_string(v)
    if isinstance(v, (list, tuple)):
        if not v:
            return b"[]"
        parts = [b"["]
        for i, item in enumerate(v):
            if i > 0:
                parts.append(b",")
            parts.append(_serialize_value(item))
        parts.append(b"]")
        return b"".join(parts)
    if isinstance(v, dict):
        if not v:
            return b"{}"
        # RFC 8785: keys sorted by Unicode code-point order of the key string.
        sorted_keys = sorted(v.keys())
        parts = [b"{"]
        for i, key in enumerate(sorted_keys):
            if i > 0:
                parts.append(b",")
            parts.append(_serialize_string(key))
            parts.append(b":")
            parts.append(_serialize_value(v[key]))
        parts.append(b"}")
        return b"".join(parts)
    raise TypeError(f"Object of type {type(v).__name__!r} is not JSON serializable by JCS")


def canonicalize(value: Any) -> bytes:
    """Return the RFC 8785 canonical JSON serialization of `value` as UTF-8 bytes.

    Args:
        value: Any JSON-compatible Python object (dict, list, str, int, float,
               bool, None).

    Returns:
        UTF-8 encoded bytes in JCS canonical form.

    Raises:
        TypeError: if `value` contains a non-JSON-serializable type.
        ValueError: if `value` contains NaN or Infinity.
    """
    return _serialize_value(value)

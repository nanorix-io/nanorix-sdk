"""
RFC 6962 binary Merkle tree — pure Python implementation.

Mirrors the sealed runtime
EXACTLY. Used by `Capsule.batch()` to compute client-side Merkle root
that matches what the server-side runtime would emit (cross-implementation
consistency lets verifiers replay independently).

Domain-separation per RFC 6962 §2.1:
- Leaf node:  SHA-512(0x00 || leaf_bytes)
- Inner node: SHA-512(0x01 || left || right)
- Odd count: lone tail node promoted unchanged.

**FROZEN** — changing this would invalidate every previously-emitted
batch Merkle root Forever-Standard discipline.
"""

from __future__ import annotations

import hashlib
from typing import List

# Match Rust's EMPTY_SHA512_HEX constant.
EMPTY_SHA512_HEX = (
    "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
    "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e"
)

_LEAF_PREFIX = b"\x00"
_INNER_PREFIX = b"\x01"


def merkle_root_from_leaves(leaves: List[bytes]) -> str:
    """Compute RFC 6962 binary Merkle tree root.

    Args:
        leaves: List of 64-byte SHA-512 hashes (each a `bytes` of length 64).
            Order matters — matches `record_idx` in BatchRecordResult.

    Returns:
        Lowercase 128-character hex string (SHA-512 output). NO "sha512:"
        prefix — caller adds it when serializing into the wire field.

    Behavior:
        - Empty list → EMPTY_SHA512_HEX (genesis-like root).
        - Single leaf → SHA-512(0x00 || leaf).
        - Multiple leaves → level-by-level inner hashing.
    """
    if not leaves:
        return EMPTY_SHA512_HEX
    for leaf in leaves:
        if not isinstance(leaf, (bytes, bytearray)) or len(leaf) != 64:
            raise ValueError(
                f"merkle_root_from_leaves: each leaf must be 64-byte SHA-512 "
                f"output; got {type(leaf).__name__} len={len(leaf) if hasattr(leaf, '__len__') else '?'}"
            )

    # Apply leaf-domain prefix: level0[i] = SHA-512(0x00 || leaf_i)
    level: List[bytes] = []
    for leaf in leaves:
        h = hashlib.sha512()
        h.update(_LEAF_PREFIX)
        h.update(leaf)
        level.append(h.digest())

    # Build tree level-by-level.
    while len(level) > 1:
        nxt: List[bytes] = []
        i = 0
        while i + 1 < len(level):
            h = hashlib.sha512()
            h.update(_INNER_PREFIX)
            h.update(level[i])
            h.update(level[i + 1])
            nxt.append(h.digest())
            i += 2
        if i < len(level):
            # Odd tail — promote unchanged.
            nxt.append(level[i])
        level = nxt

    return level[0].hex()


def combined_record_leaf(input_hash: bytes, output_hash: bytes) -> bytes:
    """Derive a Merkle leaf from a per-record (input, output) hash pair.

    Mirrors the sealed runtime. Returns
    SHA-512(input_hash || output_hash) as 64-byte digest.

    Args:
        input_hash: 64-byte raw SHA-512 of input bytes.
        output_hash: 64-byte raw SHA-512 of output bytes.

    Returns:
        64-byte SHA-512 digest of the concatenation.
    """
    if len(input_hash) != 64 or len(output_hash) != 64:
        raise ValueError(
            f"combined_record_leaf: input_hash + output_hash must each be "
            f"64-byte SHA-512 raw digests; got {len(input_hash)} + {len(output_hash)}"
        )
    h = hashlib.sha512()
    h.update(input_hash)
    h.update(output_hash)
    return h.digest()


def hash_record_bytes(data: bytes) -> str:
    """SHA-512 helper — returns lowercase hex with `sha512:` prefix.

    Mirrors the sealed runtime wire format
    (matches existing chunk_hash / body_hash / etc. convention).
    """
    return "sha512:" + hashlib.sha512(data).hexdigest()

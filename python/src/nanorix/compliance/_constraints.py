"""Wire-constraint helpers shared by all framework context dataclasses.

Mirrors ``services/api/src/validation.rs::validate_metadata`` so the
helper fails fast at construction rather than letting an oversized
payload reach the API. Reproduce the exact wire constraints; do not
loosen them client-side.

Reserved-prefix discipline (post-G2 validation amendment):
the API whitelists ``nanorix:framework`` and ``nanorix:framework_version``
as helper-emitted reserved keys. Customer-supplied ``nanorix:*`` keys are
rejected by the API. Helpers in this module emit ONLY those two reserved
keys via ``RESERVED_FRAMEWORK_KEY`` + ``RESERVED_FRAMEWORK_VERSION_KEY``.
"""
import json
from typing import Dict, List

# Wire constraints — match validate_metadata exactly. Document the limits
# here in one place; per-framework helpers import these constants.
MAX_METADATA_KEYS = 16
MAX_KEY_LENGTH = 64
MAX_VALUE_LENGTH = 256
MAX_TOTAL_SERIALIZED = 4096

# Reserved keys the helpers emit. Server validates these against the
# RESERVED_NANORIX_KEYS_ALLOWED whitelist in validation.rs. Adding a new
# reserved key here requires a coordinated server-side amendment.
RESERVED_FRAMEWORK_KEY = "nanorix:framework"
RESERVED_FRAMEWORK_VERSION_KEY = "nanorix:framework_version"


def validate_value_length(field_name: str, value: str) -> None:
    """Raise ValueError if a metadata value exceeds the 256-char wire limit.

    The wire limit is enforced server-side (validation.rs); helpers fail
    fast at construction so the error surfaces in the customer's stack
    trace rather than as an HTTP 400.
    """
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(
            f"{field_name} exceeds {MAX_VALUE_LENGTH}-char limit "
            f"(got {len(value)} chars; matches API validate_metadata)"
        )


def validate_no_comma_in_list(field_name: str, items: List[str]) -> None:
    """Raise ValueError if any list item contains ``','``.

    List-typed fields are comma-joined into a single metadata value to
    fit the ``Dict[str, str]`` schema. A comma inside an item would
    produce ambiguous output. Helper rejects at construction.
    """
    for idx, item in enumerate(items):
        if "," in item:
            raise ValueError(
                f"{field_name}[{idx}]={item!r} contains ',' which is the "
                f"comma-join separator; remove or escape before passing"
            )


def estimate_serialized_size(metadata: Dict[str, str]) -> int:
    """Estimate JSON-serialized size of a metadata dict.

    Mirrors validation.rs which checks ``serde_json::to_string(metadata)``
    against the 4096-byte limit. ``json.dumps`` produces a similar
    representation in Python (key/value sort order may differ but byte
    count is comparable).
    """
    return len(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False))

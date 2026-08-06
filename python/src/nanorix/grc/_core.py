"""Framework-agnostic core for GRC evidence-filing adapters.

Gap-closing arc: real, installable GRC adapter
modules. This is the last mile of "the AuditProof IS the provability
artifact": push the signed AuditProof — per-record receipts +
outer envelope — into the customer's GRC platform as compliance EVIDENCE,
landed against the canonical control IDs the mapping resolves at
ingestion time.

This module is the pure, vendor-SDK-free engine that the Vanta / Drata /
Anecdotes shells (``nanorix.grc.{vanta,drata,anecdotes}``) all call. It has
NO dependency on any GRC vendor SDK and is fully unit-testable with plain
dicts (see ``tests/test_grc_adapters.py``). It reuses
:mod:`nanorix.control_map` for the mapping resolution — the SINGLE source
of the ``(jurisdiction × classification × pattern_tag) → control_ids``
artifact.

The contract is a pair of pure transforms:

    auditproof_to_evidence_bodies(proof, jurisdiction, classification, ...)
        -> list[dict]   # one evidence body per (receipt × control_id)
    receipt_to_evidence_body(receipt, envelope, control_id, ...)
        -> dict

## Three-layer separation (NON-NEGOTIABLE — RE-SCOPED)

| Layer | Carries | Evolves at |
|---|---|---|
| Receipt | ``pattern_tag`` (signed) | Forever-Standard pace |
| Mapping artifact | ``(j × c × p) → list[control_id]`` | Framework revision pace |
| Adapter (this + the shells) | reads receipt + applies mapping | Vendor pace |

The mapping is applied freshly HERE, at adapter time, against a pinned
``framework_version``. It is NEVER stamped into the signed receipt — doing
so would freeze historical receipts with stale control IDs forever.

## Narrow-claim discipline (NON-NEGOTIABLE)

Per CCO policy + three-role liability split + the narrow-signed-
claim rule:

- Nanorix emits structural evidence (role 1).
- The adapter files evidence to the GRC platform (role 2).
- The auditor adjudicates whether the evidence is acceptable for a control
  (role 3).
- **Control IDs are factual references, NEVER assertions of satisfaction.**
  Every evidence body carries ``"relationship": "references"`` and the
  mapping disclaimer. The forbidden verdict vocabulary
  (``COMPLIANT`` / ``SATISFIED`` / ``PASSED`` / ``MEETS``) is rejected by
  :func:`assert_reference_only`, which runs on every emitted body — the
  GRC platform never receives an adjudication claim from Nanorix.
- **No new signed primitive.** These transforms read the existing signed
  AuditProof; they sign nothing.
- **Customer-sole-declarer.** ``jurisdiction`` +
  ``classification`` are caller-supplied (read from the customer's
  declared values); ``pattern_tag`` is read from the signed receipt. None
  is inferred here.

## Why a framework-agnostic core

The vendor shells genuinely require an HTTP POST to the vendor's evidence
API (and, optionally, the vendor's first-party SDK). Keeping all mapping +
evidence-shaping logic here means the test suite imports only this module
+ :mod:`nanorix.control_map` + plain dicts and never needs a GRC vendor
SDK installed. Any future GRC platform (Secureframe, Thoropass, Hyperproof,
...) is wired with the SAME transforms — it is documentation, not a new
engine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from nanorix.control_map import DISCLAIMER as MAPPING_DISCLAIMER
from nanorix.control_map import Mapping as ControlMapping
from nanorix.control_map import load_mapping

__all__ = [
    "FORBIDDEN_VOCAB",
    "REFERENCE_RELATIONSHIP",
    "MAPPING_DISCLAIMER",
    "GrcError",
    "assert_reference_only",
    "receipt_to_evidence_body",
    "auditproof_to_evidence_bodies",
    "resolve_control_ids",
]

# Forbidden verdict vocabulary. Identical to the observability core + the
# docs + the control_map module. Any evidence body that contains one of
# these (case-insensitively) is rejected — the GRC platform never receives
# an adjudication claim. The relationship is always a reference.
FORBIDDEN_VOCAB: Tuple[str, ...] = ("COMPLIANT", "SATISFIED", "PASSED", "MEETS")

# The single, uniform relationship term every evidence body carries.
# "references" is a factual citation: this receipt references this control
# ID. It is NOT a claim that the control is satisfied (CCO policy).
REFERENCE_RELATIONSHIP = "references"


class GrcError(Exception):
    """Raised for GRC-adapter shaping / discipline failures.

    Forbidden-vocabulary violations, missing receipt fields, and missing
    mapping inputs raise this class so the failure surfaces in the adapter
    author's stack trace with an actionable message rather than silently
    filing a malformed or non-structural evidence body.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Forbidden-vocabulary gate (reference-only discipline)
# ─────────────────────────────────────────────────────────────────────────────


def assert_reference_only(payload: Any) -> None:
    """Reject any evidence payload carrying compliance-verdict vocabulary.

    Walks strings (and the keys + values of mappings / sequences) looking
    for the forbidden verdict words. Every shell runs this on the evidence
    body it is about to POST to the GRC platform, so an adjudication claim
    can never leave the SDK. Control IDs themselves (e.g.
    ``HIPAA-164.312(b)``) are factual references and contain none of the
    forbidden words; the gate is conservative — it scans the full body.

    Args:
        payload: A string, mapping, or sequence to scan.

    Raises:
        GrcError: When a forbidden verdict word appears anywhere in the
            payload (case-insensitive).
    """
    for token in _iter_strings(payload):
        upper = token.upper()
        for forbidden in FORBIDDEN_VOCAB:
            if forbidden in upper:
                raise GrcError(
                    f"GRC evidence body contains forbidden verdict "
                    f"vocabulary {forbidden!r}: {token!r}. Per CCO policy + "
                    " three-role discipline, evidence filings are "
                    "factual REFERENCES (applies_to / references / "
                    "related_to) — never an assertion that a control is "
                    "satisfied. The auditor adjudicates."
                )


def _iter_strings(payload: Any) -> List[str]:
    """Collect every string in a nested payload (keys + values)."""
    out: List[str] = []
    if isinstance(payload, str):
        out.append(payload)
    elif isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(key, str):
                out.append(key)
            out.extend(_iter_strings(value))
    elif isinstance(payload, (list, tuple, set)):
        for item in payload:
            out.extend(_iter_strings(item))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AuditProof / receipt field readers
# ─────────────────────────────────────────────────────────────────────────────


def _get(obj: Any, key: str) -> Any:
    """Read ``key`` from a dict-like or attribute-like object; ``None`` if absent."""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _require(obj: Any, key: str, *, ctx: str) -> Any:
    """Read a required field; raise an actionable GrcError when absent."""
    value = _get(obj, key)
    if value is None:
        raise GrcError(
            f"{ctx} is missing the required {key!r} field; cannot file "
            "structurally-complete evidence."
        )
    return value


def _extract_envelope(proof: Any) -> Dict[str, Any]:
    """Build the auditor-verifiable outer envelope from the AuditProof.

    The envelope is the data an auditor needs to verify the receipts
    independently of any Nanorix service (outer signature + outer Merkle
    root + key id + termination time). It is read tolerantly: missing
    optional fields are recorded as ``None`` rather than raising, so a
    minimal proof shape still files.
    """
    chain = _get(proof, "chain")
    step_8_chain_hash: Optional[str] = None
    if isinstance(chain, (list, tuple)) and chain:
        step_8_chain_hash = _opt_str(_get(chain[-1], "chain_hash"))

    attestation = _get(proof, "attestation")
    signature: Optional[str] = None
    if attestation is not None:
        signature = _opt_str(_get(attestation, "signature"))

    return {
        "capsule_id": _opt_str(_require(proof, "capsule_id", ctx="AuditProof")),
        "key_id": _opt_str(_get(proof, "key_id")),
        "verification_key": _opt_str(_get(proof, "verification_key")),
        "record_receipts_merkle_root": _opt_str(
            _get(proof, "record_receipts_merkle_root")
        ),
        "step_8_chain_hash": step_8_chain_hash,
        "signature": signature,
        "terminated_at": _opt_str(_get(proof, "terminated_at")),
    }


def _opt_str(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _receipt_payload(receipt: Any) -> Dict[str, Any]:
    """Shape one per-record receipt into its evidence sub-document.

    Reads the signed receipt fields verbatim. ``record_index`` /
    ``record_id`` / the three hashes are required (they are the
    cryptographic anchor an auditor verifies). ``pattern_tag`` +
    ``merkle_inclusion_proof`` are read tolerantly.
    """
    return {
        "record_index": _require(receipt, "record_index", ctx="receipt"),
        "record_id": _opt_str(_require(receipt, "record_id", ctx="receipt")),
        "record_chain_hash": _opt_str(
            _require(receipt, "record_chain_hash", ctx="receipt")
        ),
        "record_input_hash": _opt_str(
            _require(receipt, "record_input_hash", ctx="receipt")
        ),
        "record_output_hash": _opt_str(
            _require(receipt, "record_output_hash", ctx="receipt")
        ),
        "pattern_tag": _opt_str(_get(receipt, "pattern_tag")),
        "merkle_inclusion_proof": list(_get(receipt, "merkle_inclusion_proof") or []),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Mapping resolution
# ─────────────────────────────────────────────────────────────────────────────


def resolve_control_ids(
    mapping: ControlMapping,
    *,
    jurisdiction: str,
    classification: str,
    pattern_tag: Optional[str],
    additional_control_ids: Optional[List[str]] = None,
) -> List[str]:
    """Resolve the canonical control IDs a receipt references.

    Applies the mapping freshly at adapter time:

    1. When ``pattern_tag`` is set, resolve the specific
       ``(jurisdiction × classification × pattern_tag)`` cell.
    2. When that cell is empty (or ``pattern_tag`` is ``None``), fall back
       to the ``(jurisdiction × classification)`` baseline cell.
    3. Append any customer-namespace control IDs verbatim (these
       are applied entirely adapter-side, never in the mapping artifact).

    Args:
        mapping: A pinned :class:`nanorix.control_map.Mapping`.
        jurisdiction: Customer-declared jurisdiction (read verbatim).
        classification: Customer-declared data_classification (read verbatim).
        pattern_tag: The receipt's signed ``pattern_tag``, or ``None``.
        additional_control_ids: Optional customer-namespace control IDs to
            append (e.g. ``["MAYO-CS-POLICY-1.7"]``).

    Returns:
        The ordered list of control IDs the receipt references (canonical +
        customer-namespace). De-duplicated, preserving first-seen order.
    """
    if pattern_tag is not None:
        control_ids = mapping.resolve(jurisdiction, classification, pattern_tag)
        if not control_ids:
            control_ids = mapping.resolve_baseline(jurisdiction, classification)
    else:
        control_ids = mapping.resolve_baseline(jurisdiction, classification)

    if additional_control_ids:
        control_ids = [*control_ids, *additional_control_ids]

    # De-dup, preserve order.
    seen: Dict[str, None] = {}
    for cid in control_ids:
        seen.setdefault(cid, None)
    return list(seen.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Evidence-body transforms
# ─────────────────────────────────────────────────────────────────────────────


def receipt_to_evidence_body(
    receipt: Any,
    envelope: Mapping[str, Any],
    control_id: str,
    *,
    framework_version: str,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Shape one (receipt × control_id) into a vendor-neutral evidence body.

    The body matches the shape the GRC adapter docs file to Vanta / Drata /
    Anecdotes: ``control_id`` + ``evidence_type`` + ``framework_version`` +
    ``relationship: "references"`` + the signed ``receipt`` + the
    auditor-verifiable ``audit_proof_envelope`` + the mapping disclaimer.
    The shells layer vendor-specific keys (``workspace_id`` / ``tenant_id``)
    on top via ``extra_fields``.

    The reference-only gate runs on the returned body, so an adjudication
    claim can never be filed.

    Args:
        receipt: One per-record receipt (dict or model).
        envelope: The outer envelope from :func:`_extract_envelope`.
        control_id: The single control ID this body references.
        framework_version: The pinned mapping framework version.
        extra_fields: Optional vendor-specific top-level fields to merge in.

    Returns:
        The evidence body dict.
    """
    body: Dict[str, Any] = {
        "control_id": control_id,
        "evidence_type": "auditproof_receipt",
        "framework_version": framework_version,
        "relationship": REFERENCE_RELATIONSHIP,
        "receipt": _receipt_payload(receipt),
        "audit_proof_envelope": dict(envelope),
        "disclaimer": MAPPING_DISCLAIMER,
    }
    if extra_fields:
        body.update(dict(extra_fields))
    assert_reference_only(body)
    return body


def auditproof_to_evidence_bodies(
    proof: Any,
    *,
    jurisdiction: str,
    classification: str,
    framework_version: Optional[str] = None,
    mapping: Optional[ControlMapping] = None,
    additional_control_ids: Optional[List[str]] = None,
    extra_fields: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Shape a full AuditProof into a flat list of evidence bodies.

    For every per-record receipt in the proof, resolves the control IDs the
    receipt references (mapping, applied at adapter time) and emits
    one evidence body per ``(receipt × control_id)``. This is the single
    transform every GRC shell calls; the shell then POSTs each body to its
    vendor's evidence API.

    A proof with no ``record_receipts`` yields an empty list (nothing to
    file) — not an error.

    Args:
        proof: The AuditProof / FullCdp (dict or model) carrying ``chain`` +
            ``attestation`` + ``record_receipts``.
        jurisdiction: Customer-declared jurisdiction (read verbatim).
        classification: Customer-declared data_classification (read verbatim).
        framework_version: Optional mapping framework version to pin. Ignored
            when ``mapping`` is supplied; otherwise defaults to the current
            published version.
        mapping: Optional pre-loaded :class:`nanorix.control_map.Mapping`
            (recommended for high-volume adapters — load once at startup).
        additional_control_ids: Optional customer-namespace control IDs
            appended to every receipt's resolved set.
        extra_fields: Optional vendor-specific top-level fields merged into
            every body (e.g. ``{"tenant_id": "..."}``).

    Returns:
        A flat list of evidence-body dicts (one per receipt × control_id).

    Raises:
        GrcError: When a receipt is missing a required cryptographic field,
            or a body would carry forbidden verdict vocabulary.
    """
    if mapping is None:
        mapping = load_mapping(framework_version)
    fv = mapping.framework_version

    envelope = _extract_envelope(proof)
    receipts = _get(proof, "record_receipts") or []

    bodies: List[Dict[str, Any]] = []
    for receipt in receipts:
        pattern_tag = _opt_str(_get(receipt, "pattern_tag"))
        control_ids = resolve_control_ids(
            mapping,
            jurisdiction=jurisdiction,
            classification=classification,
            pattern_tag=pattern_tag,
            additional_control_ids=additional_control_ids,
        )
        for control_id in control_ids:
            bodies.append(
                receipt_to_evidence_body(
                    receipt,
                    envelope,
                    control_id,
                    framework_version=fv,
                    extra_fields=extra_fields,
                )
            )
    return bodies

"""GRC evidence-filing adapters for Nanorix AuditProofs.

Real, installable GRC adapter modules. The last
mile of "the AuditProof IS the provability artifact": push the signed
AuditProof — per-record receipts + outer envelope — into your GRC
platform as compliance EVIDENCE, landed against the canonical control IDs
the mapping resolves at ingestion time:

- **Vanta** — ``nanorix.grc.vanta`` (``pip install nanorix[vanta]``);
- **Drata** — ``nanorix.grc.drata`` (``pip install nanorix[drata]``);
- **Anecdotes** — ``nanorix.grc.anecdotes`` (``pip install nanorix[anecdotes]``).

## Architecture (framework-agnostic-core + guarded-shell)

The pure mapping + evidence-shaping engine lives in :mod:`nanorix.grc._core`
and has NO vendor-SDK dependency — it reuses :mod:`nanorix.control_map` for
the ``(jurisdiction × classification × pattern_tag) → control_ids``
resolution and turns an AuditProof into a flat list of evidence bodies using
plain dicts. It is safe to import eagerly and fully unit-testable without
any GRC vendor SDK:

    from nanorix.grc import (
        auditproof_to_evidence_bodies,
        receipt_to_evidence_body,
        resolve_control_ids,
        assert_reference_only,
    )

The per-vendor pusher shells live behind optional extras and POST via the
SDK's own httpx transport, so NO GRC vendor SDK is required at all:

    from nanorix.grc.vanta import VantaEvidenceAdapter         # pip install nanorix[vanta]
    from nanorix.grc.drata import DrataEvidenceAdapter         # pip install nanorix[drata]
    from nanorix.grc.anecdotes import AnecdotesEvidenceAdapter # pip install nanorix[anecdotes]

Secureframe, Thoropass, Hyperproof, etc. follow the SAME transforms — they
are documentation, not new engines (see
``docs/integrations/grc-adapters/``).

## Three-layer separation (NON-NEGOTIABLE — RE-SCOPED)

| Layer | Carries | Evolves at |
|---|---|---|
| Receipt | ``pattern_tag`` (signed) | Forever-Standard pace |
| Mapping | ``(j × c × p) → list[control_id]`` | Framework revision pace |
| Adapter (this) | reads receipt + applies mapping + files | Vendor pace |

The mapping is applied freshly at adapter time; it is NEVER stamped into the
signed receipt.

## Narrow-claim discipline (NON-NEGOTIABLE)

Every evidence body carries ``"relationship": "references"`` + the
mapping disclaimer, and is run through :func:`assert_reference_only` (no
``COMPLIANT`` / ``SATISFIED`` / ``PASSED`` / ``MEETS``). Nanorix emits
structural evidence (role 1); the adapter files it (role 2); the auditor
adjudicates whether it is acceptable for a control (role 3) —
three-role liability split. No new signed primitive is added (the existing
AuditProof is consumed). Customer-sole-declarer: ``jurisdiction``
+ ``classification`` are caller-supplied and read verbatim.
"""

from __future__ import annotations

from nanorix.grc._core import (
    FORBIDDEN_VOCAB,
    MAPPING_DISCLAIMER,
    REFERENCE_RELATIONSHIP,
    GrcError,
    assert_reference_only,
    auditproof_to_evidence_bodies,
    receipt_to_evidence_body,
    resolve_control_ids,
)

__all__ = [
    "FORBIDDEN_VOCAB",
    "MAPPING_DISCLAIMER",
    "REFERENCE_RELATIONSHIP",
    "GrcError",
    "assert_reference_only",
    "auditproof_to_evidence_bodies",
    "receipt_to_evidence_body",
    "resolve_control_ids",
]

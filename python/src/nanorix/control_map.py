"""Control-map resolver (Wave B 2026-05-26) — published mapping artifact.

Layer-2 published artifact `(jurisdiction × data_classification × pattern_tag) →
list[canonical_control_id]`. Consumed by GRC adapters (Vanta / Drata /
Anecdotes / internal) at ingestion time given a receipt's signed
`pattern_tag` + customer's declared `jurisdiction` + capsule's
declared `data_classification` (customer-sole-declarer).

## Three-layer separation (RE-SCOPED — founder direction 2026-05-12)

| Layer | Carries | Evolves at |
|---|---|---|
| Receipt | `pattern_tag` (signed) | Forever-Standard pace |
| Mapping artifact (this module) | `(j × c × p) → list[control_id]` | Framework revision pace |
| Adapter | Reads receipt + applies mapping | Vendor pace |

Stamping mapping output inside the cryptographic receipt would freeze
historical receipts with stale control IDs forever when frameworks revise.
The receipt carries pattern_tag (signed primitive); adapter applies the
mapping freshly at ingestion time.

## Discipline (CCO policy)

- Control IDs are factual `references` — NOT assertions of satisfaction.
- Forbidden vocabulary: COMPLIANT / SATISFIED / PASSED / MEETS.
- Required relationship vocabulary: applies_to / references / related_to.
- Every adapter surface that consumes this module must surface the disclaimer.

## Usage

```python
from nanorix.control_map import load_mapping, MAPPING_2026_02, resolve

# Option 1: helper resolver
control_ids = resolve(
    jurisdiction="US",
    classification="PHI",
    pattern_tag="pa",
)
# control_ids = ["HIPAA-164.312(b)", "URAC-UM-AC-1", ...]

# Option 2: dict-style direct access via embedded constant
mapping = MAPPING_2026_02
ids = mapping["US.PHI.pa"]

# Option 3: load_mapping pinned to a framework_version
mapping = load_mapping(framework_version="2026-02")
ids = mapping.resolve("US", "PHI", "pa")
```
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

FRAMEWORK_VERSION: str = "2026-02"
"""Canonical framework version stamp for this revision of the mapping."""

SCHEMA_VERSION: str = "1.0"
"""JSON Schema version for the published artifact format."""

DISCLAIMER: str = (
    "Control IDs are references using canonical framework identifiers. "
    "Whether evidence is acceptable for a control is determined by the "
    "customer's compliance program and the auditor's adjudication, not "
    "by Nanorix."
)
"""Canonical disclaimer per CCO policy + three-role discipline."""

# ─── Canonical mapping constants ──────────────────────────────────────────────
# Per. Mirrors the Rust resolver in
# `services/api/src/control_map.rs::resolve_controls` — single source of truth.
# Forever-Standard: append-only; old MAPPING_<version> constants
# remain available forever in future SDK revisions.
# ─────────────────────────────────────────────────────────────────────────────

MAPPING_2026_02: Dict[str, List[str]] = {
    # ── Pattern 1 — Prior Authorization (PA) decisions ──
    # Anterior, Latent Health, Itiliti Health, Humata Health
    "US.PHI.pa": [
        "HIPAA-164.312(b)",
        "HIPAA-164.312(c)",
        "URAC-UM-AC-1",
        "URAC-UM-AC-2",
        "NCQA-UM-5",
        "NCQA-UM-7",
        "ERISA-503(2)",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],
    "EU.PHI.pa": [
        "GDPR-Art-30",
        "GDPR-Art-32",
        "EU-AI-Act-Art-26",
        "ISO-42001-8.6",
        "EHDS-Art-73",
        "SOC2-CC7.2",
    ],

    # ── Pattern 4 — RCM claim, eligibility, remit ──
    # Adonis, Candid, Nym, Fathom, Cair, Sohar
    "US.PHI.rcm_claim": [
        "HIPAA-164.312(b)",
        "HIPAA-164.312(c)",
        "HITRUST-02.e",
        "CMS-CoP-482.24",
        "ACA-1557",
        "NIST-800-53-AU-2",
        "SOC2-CC7.2",
    ],
    "US.Financial.rcm_claim": [
        "HIPAA-164.312(b)",
        "PCI-DSS-10.2",
        "HITRUST-02.e",
        "SOX-302(a)(4)",
        "SOC2-CC7.2",
    ],
    "US.PHI.rcm_eligibility": [
        "HIPAA-164.312(b)",
        "HITRUST-02.e",
        "CMS-CoP-482.24",
        "NIST-800-53-AU-2",
        "SOC2-CC7.2",
    ],
    "US.PHI.rcm_remit": [
        "HIPAA-164.312(b)",
        "HITRUST-02.e",
        "PCI-DSS-10.2",
        "NIST-800-53-AU-2",
        "SOC2-CC7.2",
    ],

    # ── Pattern 4 — NCPDP e-prescription — Banjo Health ──
    "US.PHI.ncpdp_script": [
        "HIPAA-164.312(b)",
        "DEA-21CFR1311.300",
        "NCPDP-D.0",
        "NIST-800-53-AU-2",
        "SOC2-CC7.2",
    ],

    # ── Pattern 6 — DICOM imaging — Overjet, Cleerly, PathAI ──
    "US.PHI.dicom_study": [
        "HIPAA-164.312(b)",
        "FDA-21CFR820.198",
        "FDA-21CFR11.10(e)",
        "ACR-PP",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],
    "EU.PHI.dicom_study": [
        "GDPR-Art-30",
        "GDPR-Art-32",
        "EU-MDR-Art-87",
        "EU-AI-Act-Art-26",
        "EHDS-Art-73",
        "SOC2-CC7.2",
    ],
    "US.PHI.dicom_sr": [
        "HIPAA-164.312(b)",
        "FDA-21CFR820.198",
        "FDA-21CFR11.10(e)",
        "ACR-PP",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],
    "EU.PHI.dicom_sr": [
        "GDPR-Art-30",
        "GDPR-Art-32",
        "EU-MDR-Art-87",
        "EU-AI-Act-Art-26",
        "EHDS-Art-73",
        "SOC2-CC7.2",
    ],

    # ── Pattern 8 — long-horizon agent workloads ──
    # Itiliti, Humata, Hippocratic AI
    "US.PHI.agent_step": [
        "HIPAA-164.312(b)",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],
    "EU.PHI.agent_step": [
        "GDPR-Art-30",
        "EU-AI-Act-Art-26",
        "EU-AI-Act-Art-15",
        "ISO-42001-8.6",
        "SOC2-CC7.2",
    ],
    "US.PHI.agent_turn": [
        "HIPAA-164.312(b)",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],
    "EU.PHI.agent_turn": [
        "GDPR-Art-30",
        "EU-AI-Act-Art-26",
        "EU-AI-Act-Art-50",
        "ISO-42001-8.6",
        "SOC2-CC7.2",
    ],

    # ── R&D screening — Recursion, Insitro, Atomwise ──
    "US.General.screening_hit": [
        "FDA-21CFR11.10(e)",
        "21CFR-Part-58-GLP",
        "NIST-800-53-AU-2",
        "SOC2-CC7.2",
    ],

    # ── FHIR record — MDClone, Verata, Beacon, SmarterDx, Pieces ──
    "US.PHI.fhir_record": [
        "HIPAA-164.312(b)",
        "HL7-FHIR-Audit",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],

    # ── EHR document ──
    "US.PHI.ehr_document": [
        "HIPAA-164.312(b)",
        "HITECH-13402",
        "SOC2-CC7.2",
    ],

    # ── Extraction — Atria HCC coding, Pattern 3 ──
    "US.PHI.extraction": [
        "HIPAA-164.312(b)",
        "NIST-AI-RMF-MAP-4.1",
        "SOC2-CC7.2",
    ],
    "EU.PHI.extraction": [
        "GDPR-Art-30",
        "GDPR-Art-32",
        "EU-AI-Act-Art-26",
        "ISO-42001-8.6",
        "SOC2-CC7.2",
    ],

    # ── Baseline cells (fallback when no specific pattern cell seeded) ──
    "_baseline.US.PII": ["GDPR-Art-30-equivalent", "SOC2-CC7.2"],
    "_baseline.EU.PII": ["GDPR-Art-30", "GDPR-Art-32", "SOC2-CC7.2"],
    "_baseline.CA.PII": ["CCPA-1798.130", "CPRA-1798.140", "SOC2-CC7.2"],
    "_baseline.US.Financial": ["PCI-DSS-10.2", "SOX-302(a)(4)", "SOC2-CC7.2"],
    "_baseline.US.Credentials": ["NIST-800-53-IA-5", "SOC2-CC6.1"],
}
"""Canonical mapping for framework_version 2026-02. Append-only."""

# Registry of available framework_versions. Old versions remain forever.
_MAPPINGS_BY_VERSION: Dict[str, Dict[str, List[str]]] = {
    "2026-02": MAPPING_2026_02,
}


@dataclass(frozen=True)
class Mapping:
    """Pinned mapping at a specific framework_version.

    Returned by `load_mapping(framework_version)`. Encapsulates the mapping
    dict + provides the `resolve()` convenience accessor.

    Discipline: this is layer-2 of the three-layer separation. The mapping is
    applied at ADAPTER TIME, never stamped into the signed receipt.
    """

    framework_version: str
    mappings: Dict[str, List[str]]
    schema_version: str = SCHEMA_VERSION
    disclaimer: str = DISCLAIMER

    def resolve(
        self,
        jurisdiction: str,
        classification: str,
        pattern_tag: str,
    ) -> List[str]:
        """Resolve canonical control IDs for a specific cell.

        Args:
            jurisdiction: Customer-declared jurisdiction (US, EU, UK, CA, AU,
                IN, OTHER).
            classification: Customer-declared data_classification (PHI, PII,
                Financial, Credentials, General). Case-insensitive at the
                consumer surface.
            pattern_tag: Receipt's signed pattern_tag (e.g. 'pa', 'rcm_claim',
                'dicom_study').

        Returns:
            List of canonical control IDs for the cell. Empty when cell is not
            seeded — caller can fall back via `resolve_baseline()`.

        Customer-sole-declarer: no inference; arguments are taken
        verbatim from the customer-declared values.
        """
        # Canonicalize classification wire form.
        cls_canonical = _canonicalize_classification(classification)
        key = f"{jurisdiction}.{cls_canonical}.{pattern_tag}"
        return list(self.mappings.get(key, []))

    def resolve_baseline(
        self, jurisdiction: str, classification: str
    ) -> List[str]:
        """Baseline fallback for a `(jurisdiction × classification)` cell.

        Used when `resolve()` returns an empty list for a specific
        `pattern_tag` cell. Returns the `_baseline.{jurisdiction}.{classification}`
        cell if present.
        """
        cls_canonical = _canonicalize_classification(classification)
        key = f"_baseline.{jurisdiction}.{cls_canonical}"
        return list(self.mappings.get(key, []))


def _canonicalize_classification(classification: str) -> str:
    """Canonicalize classification wire form (matches `DataClassification::as_wire`)."""
    lc = classification.lower()
    if lc == "phi":
        return "PHI"
    if lc == "pii":
        return "PII"
    if lc == "financial":
        return "Financial"
    if lc == "credentials":
        return "Credentials"
    if lc == "general":
        return "General"
    # Unknown — return verbatim; caller cell lookup will miss.
    return classification


def load_mapping(framework_version: Optional[str] = None) -> Mapping:
    """Load a pinned mapping at the given framework_version.

    Args:
        framework_version: Framework version (e.g. '2026-02'). Defaults to the
            current canonical version `FRAMEWORK_VERSION`.

    Returns:
        Mapping pinned to the requested version. Caller can call
        `.resolve(jurisdiction, classification, pattern_tag)` against it.

    Raises:
        ValueError: If `framework_version` is not available. I0
            Forever-Standard, old versions remain available forever — new
            framework_versions are added to `_MAPPINGS_BY_VERSION` in future
            SDK releases, never removed.
    """
    fv = framework_version or FRAMEWORK_VERSION
    if fv not in _MAPPINGS_BY_VERSION:
        raise ValueError(
            f"framework_version '{fv}' not available. "
            f"Available versions: {sorted(_MAPPINGS_BY_VERSION.keys())}. "
            f"Forever-Standard, old versions remain available "
            f"forever; ensure you are using a published version."
        )
    return Mapping(
        framework_version=fv,
        mappings=dict(_MAPPINGS_BY_VERSION[fv]),
    )


def resolve(
    jurisdiction: str,
    classification: str,
    pattern_tag: str,
    framework_version: Optional[str] = None,
) -> List[str]:
    """Convenience accessor — resolve canonical control IDs for a cell.

    Equivalent to `load_mapping(framework_version).resolve(...)`.

    Customer-sole-declarer: arguments are customer-declared values
    consumed verbatim. Nanorix does NOT infer jurisdiction or classification.

    Args:
        jurisdiction: Customer-declared jurisdiction (US, EU, UK, CA, AU, IN, OTHER).
        classification: Customer-declared data_classification (PHI, PII,
            Financial, Credentials, General).
        pattern_tag: Receipt's signed pattern_tag.
        framework_version: Framework version (defaults to current).

    Returns:
        List of canonical control IDs. Empty when no specific cell is seeded.

    Example:
        >>> resolve("US", "PHI", "pa")
        ['HIPAA-164.312(b)', 'HIPAA-164.312(c)', 'URAC-UM-AC-1', ...]
    """
    return load_mapping(framework_version).resolve(
        jurisdiction, classification, pattern_tag
    )


def available_framework_versions() -> List[str]:
    """List all available framework_versions in this SDK release.

    Forever-Standard: this list is append-only across SDK
    releases — old framework_versions are NEVER removed.
    """
    return sorted(_MAPPINGS_BY_VERSION.keys())


__all__ = [
    "FRAMEWORK_VERSION",
    "SCHEMA_VERSION",
    "DISCLAIMER",
    "MAPPING_2026_02",
    "Mapping",
    "load_mapping",
    "resolve",
    "available_framework_versions",
]

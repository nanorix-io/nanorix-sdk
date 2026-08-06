"""Healthcare data-format adapters + regulatory framework helpers for
capsule workloads.

This module provides healthcare-domain helpers in two flavors:

1. **Data-format adapters** that parse and structurally describe
   healthcare interchange formats — HL7 V2 messages, C-CDA XML
   documents, and Epic SMART-on-FHIR launch contexts.
2. **Healthcare regulatory framework helpers** (FDA SaMD, etc.) that
   declare framework context for the workload's metadata, parallel
   to ``nanorix.compliance`` but scoped to healthcare-specific
   regulatory frameworks.

Helpers shipped:

- :class:`HL7v2Adapter`         — HL7 V2 ADT (A01/A03/A08) + ORU (R01)
                                  message parsing → structural metadata.
- :class:`CCDAReader`           — C-CDA XML reader → FHIR Bundle
                                  DocumentReference + Binary adapter.
- :class:`EpicLaunchContext`    — Epic SMART-on-FHIR token-response →
                                  ``patient_fhir_id`` /
                                  ``encounter_fhir_id`` / ``fhirUser``
                                  AuditProof metadata binding.
- :class:`FDASaMDContext`       — FDA Software as a Medical Device
                                  (SaMD) framework + 21 CFR Part 860
                                  device class + 510(k) / De Novo /
                                  PMA submission pathway context.
- :class:`AIFairnessContext`    — AI fairness evaluation context
                                  (composes with NIST AI RMF MEASURE
                                  + FDA SaMD subgroup_performance;
                                  cross-vertical: hiring / housing /
                                  financial / criminal-justice /
                                  healthcare).
- :class:`DICOMwebAdapter`      — DICOMweb WADO-RS / STOW-RS / QIDO-RS
                                  HTTP transaction parser → structural
                                  metadata (StudyInstanceUID,
                                  SeriesInstanceUID, SOPInstanceUID,
                                  SOP Class UID, modality). Gates
                                  Overjet-shape (FDA-cleared dental
                                  imaging AI, CBCT 3D imaging).
- :class:`DICOMSRBinding`       — DICOM SR (Structured Reporting) AuditProof
                                  binding: document SHA-256 + SOP Instance
                                  UID + content template ID + creator
                                  + algorithm_identification (for AI-
                                  generated SR per FDA SaMD).

Three-role discipline:
    Nanorix is an evidence vendor (role 1). Helpers in this module
    structurally parse customer-supplied data and emit metadata fields
    suitable for AuditProof binding. Helpers do NOT interpret clinical
    content, validate against any specific implementation guide, or
    certify the upstream system's conformance. Clinical interpretation,
    implementation-guide conformance validation, and downstream
    consumer responsibilities remain the customer's.

Customer-sole-declarer:
    The customer is the sole declarer of capsule content. These helpers
    parse customer-supplied healthcare-interchange payloads to produce
    structural metadata only; Nanorix injects nothing into the workload
    beyond the published base-image spec.

Forever-Standard discipline:
    Helpers emit metadata via the existing
    ``Option<HashMap<String, String>>`` field. No new
    API surface; no changes to AuditProof / Capsulefile shape.

Usage::

    from nanorix import Client
    from nanorix.healthcare import HL7v2Adapter

    client = Client(api_key="nrx_live_...")

    adapter = HL7v2Adapter()
    parsed = adapter.parse_message(hl7_bytes)
    capsule = client.capsules.create(
        capsulefile="cf_...",
        metadata=parsed.to_metadata(),
    )

Optional dependencies:

- HL7 V2:    ``pip install nanorix[hl7-v2]`` (``hl7apy``)
- C-CDA:     ``pip install nanorix[ccda]`` (``lxml``)
- Epic:      ``pip install nanorix[epic-smart]`` (``python-jose``)
- DICOMweb:  ``pip install nanorix[dicomweb]`` (``dicomweb-client``)
- DICOM SR:  ``pip install nanorix[dicom-sr]`` (``pydicom``)

Companion posture documents:

- ``docs/healthcare/hl7-v2-integration.md``
- ``docs/healthcare/ccda-fhir-adapter.md``
- ``docs/healthcare/epic-smart-on-fhir-deployment.md``
- ``docs/healthcare/fda-samd-pathway.md``
- ``docs/healthcare/ai-fairness-evaluation.md``
- ``docs/healthcare/dicom-integration.md``
- ``docs/healthcare/dicomweb-adapter.md``
- ``docs/healthcare/dicom-sr-binding.md``
"""
from nanorix.healthcare.ai_fairness import AIFairnessContext
from nanorix.healthcare.bulk_fhir import (
    BulkFhirAdapter,
    BulkFhirKickoff,
    BulkFhirResource,
    BulkFhirStatus,
)
from nanorix.healthcare.ccda import CCDADocumentReference, CCDAReader
from nanorix.healthcare.dicom_sr import DICOMSRBinding
from nanorix.healthcare.dicomweb import DICOMwebAdapter, DICOMwebTransaction
from nanorix.healthcare.epic_smart import (
    EpicLaunchContext,
    EpicLaunchContextResult,
)
from nanorix.healthcare.fda_samd import FDASaMDContext
from nanorix.healthcare.hl7_v2 import (
    HL7v2Adapter,
    HL7v2ParsedMessage,
)
from nanorix.healthcare.ihe import (
    IHEAdapter,
    IHEBindingError,
    IHETransaction,
    IHETransactionType,
)
from nanorix.healthcare.ncpdp import (
    NCPDPAdapter,
    NCPDPParsedTransaction,
)
from nanorix.healthcare.x12_278 import (
    X12ServicesReviewAdapter,
    X12ServicesReviewTransaction,
)
from nanorix.healthcare.x12_eligibility import (
    X12EligibilityAdapter,
    X12EligibilityTransaction,
)
from nanorix.healthcare.x12_full_rcm import (
    X12FullRCMAdapter,
    X12RCMTransaction,
)

__all__ = [
    "AIFairnessContext",
    "BulkFhirAdapter",
    "BulkFhirKickoff",
    "BulkFhirResource",
    "BulkFhirStatus",
    "CCDADocumentReference",
    "CCDAReader",
    "DICOMSRBinding",
    "DICOMwebAdapter",
    "DICOMwebTransaction",
    "EpicLaunchContext",
    "EpicLaunchContextResult",
    "FDASaMDContext",
    "HL7v2Adapter",
    "HL7v2ParsedMessage",
    "IHEAdapter",
    "IHEBindingError",
    "IHETransaction",
    "IHETransactionType",
    "NCPDPAdapter",
    "NCPDPParsedTransaction",
    "X12EligibilityAdapter",
    "X12EligibilityTransaction",
    "X12FullRCMAdapter",
    "X12RCMTransaction",
    "X12ServicesReviewAdapter",
    "X12ServicesReviewTransaction",
]

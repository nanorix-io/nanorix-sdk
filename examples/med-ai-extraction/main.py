"""Medical document extraction reference workload — production pattern.

Pipeline shape: PDF (PHI) → text-extraction router (pypdf → OCR fallback →
vision-LLM fallback) → document-class router (referral / lab / discharge)
→ class-specific extraction prompt → PHI-redaction stub → FHIR-shaped
structured output → /data/output.

Every outbound HTTPS call to api.anthropic.com (and to OCR providers
when the OCR fallback is the cloud Document AI route) is recorded as
one SealedHttpRequest activity event in the capsule's AuditProof.
The input PDF is buffered in volatile memory only — destroyed with
the capsule.

Production-shape fallbacks demonstrated (stubbed where remote service
or GPU is required):

  1. pypdf text extraction. If text length / chars-per-page below
     threshold → assume scanned image-only PDF; fall through.
  2. Tesseract OCR via pytesseract (rasterized via Pillow).
     Confidence threshold checked. If still low confidence → fall through.
  3. Vision-LLM fallback (Claude 3.5 Sonnet vision) on rasterized pages.
     This is the highest-cost path and is the production fallback for
     low-quality scans / handwritten content.

Document-class router stub: simple keyword heuristics map
{referral, lab_report, discharge_summary} → class-specific extraction
prompt. Production customers swap this for a fine-tuned classifier or
an LLM-based routing call.

PHI-redaction stub: regex pattern for SSN / DOB / MRN. Demonstrates
where the redaction hook sits (pre-LLM, post-text-extraction). NOT a
production redactor — production deployments compose Presidio /
Philter / customer's HHS Safe Harbor de-identifier.

FHIR output: structured fields shape conforms to FHIR R4 patterns —
DocumentReference (the source document) + Observation (extracted
clinical observations) + Condition (diagnoses). Customer's downstream
EHR integration (Epic Hyperdrive REST, Cerner Millennium FHIR) ingests
this shape directly.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, Field
from pypdf import PdfReader

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
VISION_MODEL = os.environ.get("VISION_MODEL", "claude-3-5-sonnet-latest")
PYPDF_MIN_CHARS_PER_PAGE = int(os.environ.get("PYPDF_MIN_CHARS_PER_PAGE", "200"))
OCR_MIN_CONFIDENCE = float(os.environ.get("OCR_MIN_CONFIDENCE", "0.70"))

DocClass = Literal["referral", "lab_report", "discharge_summary", "unknown"]
ExtractionRoute = Literal["pypdf", "tesseract_ocr", "vision_llm"]


# -- FHIR-shaped output schema --------------------------------------------
#
# Output JSON conforms to FHIR R4 resource patterns. Customer's EHR
# integration consumes these directly into Epic / Cerner / etc.


class FHIRObservation(BaseModel):
    """FHIR Observation resource — vitals, labs, clinical findings."""

    code: str  # LOINC or SNOMED code (e.g. "8302-2" for body height)
    code_system: str = "http://loinc.org"
    display: str  # human-readable name
    value: str | float | None = None
    unit: str | None = None
    effective_datetime: str | None = None  # ISO 8601


class FHIRCondition(BaseModel):
    """FHIR Condition resource — diagnoses, problems."""

    code: str  # ICD-10 code
    code_system: str = "http://hl7.org/fhir/sid/icd-10"
    display: str
    clinical_status: Literal["active", "recurrence", "relapse", "inactive", "remission", "resolved"] = "active"
    onset_datetime: str | None = None


class FHIRMedication(BaseModel):
    """FHIR MedicationStatement — patient-reported / extracted medications."""

    code: str  # RxNorm code
    code_system: str = "http://www.nlm.nih.gov/research/umls/rxnorm"
    display: str
    dose_text: str | None = None
    frequency: str | None = None


class FHIRDocumentReference(BaseModel):
    """FHIR DocumentReference — wraps the source clinical document."""

    document_class: DocClass
    extraction_route: ExtractionRoute
    extraction_confidence: float
    source_filename: str
    source_size_bytes: int
    page_count: int


class ExtractedDocument(BaseModel):
    """Top-level FHIR-shaped extraction result.

    Conforms to FHIR R4 transaction-bundle pattern — one DocumentReference
    + N Observations + N Conditions + N Medications. Customer's downstream
    EHR consumer treats the JSON as a denormalized FHIR Bundle entry list.
    """

    document_reference: FHIRDocumentReference
    observations: list[FHIRObservation] = Field(default_factory=list)
    conditions: list[FHIRCondition] = Field(default_factory=list)
    medications: list[FHIRMedication] = Field(default_factory=list)
    extraction_warnings: list[str] = Field(default_factory=list)


# -- PHI redaction stub ---------------------------------------------------


_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_DOB_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
_MRN_RE = re.compile(r"\bMRN[:\s]*([A-Z0-9-]{4,16})\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")


def redact_phi(text: str) -> tuple[str, list[str]]:
    """Pre-LLM PHI redaction stub.

    NOT A PRODUCTION REDACTOR. Production customers compose Presidio
    (Microsoft), Philter (BeKnown Health), AWS Comprehend Medical PHI
    detection, or their own HHS Safe Harbor §164.514(b)(2)(i) 18-element
    de-identification pipeline.

    This stub demonstrates the integration shape: text in, redacted text
    out, list of redaction-event tags for AuditProof activity_trail
    enrichment. The redaction events are deterministically counted (not
    the values themselves) so verifier replay can confirm "N PHI tokens
    were redacted" without seeing the original.
    """
    redactions: list[str] = []
    redacted = text
    for pattern_name, pat, replacement in [
        ("ssn", _SSN_RE, "[REDACTED-SSN]"),
        ("dob", _DOB_RE, "[REDACTED-DOB]"),
        ("mrn", _MRN_RE, "[REDACTED-MRN]"),
        ("phone", _PHONE_RE, "[REDACTED-PHONE]"),
        ("email", _EMAIL_RE, "[REDACTED-EMAIL]"),
    ]:
        matches = pat.findall(redacted)
        if matches:
            redactions.extend([pattern_name] * len(matches))
            redacted = pat.sub(replacement, redacted)
    return redacted, redactions


# -- Text-extraction router (pypdf → OCR → vision-LLM) -------------------


def extract_via_pypdf(pdf_path: Path) -> tuple[str, int, float]:
    """Layer 1: pypdf text extraction.

    Returns (text, page_count, confidence). Confidence is a heuristic
    based on chars-per-page; truly scanned image-only PDFs return
    near-empty text and confidence ≈ 0.
    """
    reader = PdfReader(str(pdf_path))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n\n".join(pages_text)
    page_count = len(reader.pages)
    if page_count == 0:
        return text, 0, 0.0
    chars_per_page = len(text) / page_count
    confidence = min(1.0, chars_per_page / PYPDF_MIN_CHARS_PER_PAGE)
    return text, page_count, confidence


def extract_via_tesseract(pdf_path: Path) -> tuple[str, float]:
    """Layer 2: Tesseract OCR via pytesseract on rasterized pages.

    STUB — full implementation rasterizes pages via pdf2image (Poppler)
    and feeds each page to pytesseract.image_to_data, aggregating the
    per-token confidence. Returns (text, mean_confidence).

    Production deployments substitute Google Document AI (clinical
    document parser model), AWS Textract, or Azure Form Recognizer for
    higher accuracy on clinical forms (handwritten lab values, etc).
    The integration shape is identical: bytes in, structured response
    out. Each cloud-OCR call is a SealedHttpRequest in the AuditProof.
    """
    # Stub: pretend Tesseract returned text + confidence.
    # Production: pytesseract.image_to_data + aggregate confidence.
    print("  [tesseract_ocr] STUB — production substitutes pytesseract or cloud Document AI", file=sys.stderr)
    return "[OCR_STUB_OUTPUT — production deployment fills this layer]", 0.5


def extract_via_vision_llm(pdf_path: Path) -> tuple[str, float]:
    """Layer 3: Claude vision on rasterized pages.

    STUB — full implementation rasterizes each page (pdf2image →
    base64-encoded PNG) and sends to Claude with a vision-extraction
    prompt. Each page is one SealedHttpRequest. Confidence is derived
    from the model's self-reported confidence in the response.

    This is the highest-cost path and the production fallback for
    handwritten / low-quality / unusual-format scans.
    """
    print("  [vision_llm] STUB — production rasterizes pages + Claude vision API", file=sys.stderr)
    return "[VISION_LLM_STUB_OUTPUT — production deployment fills this layer]", 0.85


def extract_text(pdf_path: Path) -> tuple[str, int, ExtractionRoute, float]:
    """Document-routing pipeline.

    Tries pypdf first (cheapest, no egress). Falls through to Tesseract
    OCR if confidence too low. Falls through to vision-LLM if OCR
    confidence still too low.

    Returns (text, page_count, route_taken, confidence).
    """
    text, page_count, conf = extract_via_pypdf(pdf_path)
    if conf >= 0.8:
        return text, page_count, "pypdf", conf

    print(f"  [pypdf] confidence={conf:.2f} below threshold — falling through to OCR", file=sys.stderr)
    ocr_text, ocr_conf = extract_via_tesseract(pdf_path)
    if ocr_conf >= OCR_MIN_CONFIDENCE:
        return ocr_text, page_count, "tesseract_ocr", ocr_conf

    print(f"  [ocr] confidence={ocr_conf:.2f} below threshold — falling through to vision-LLM", file=sys.stderr)
    vis_text, vis_conf = extract_via_vision_llm(pdf_path)
    return vis_text, page_count, "vision_llm", vis_conf


# -- Document-class router ------------------------------------------------


def classify_document(text: str) -> DocClass:
    """Stub document-class router based on keyword heuristics.

    Production deployments swap this for a fine-tuned classifier
    (DistilBERT / Clinical-BERT) or an LLM routing call. The router
    selects the class-specific extraction prompt downstream.
    """
    lowered = text.lower()
    referral_signals = ["referral", "consult", "referring physician", "referred by"]
    lab_signals = ["laboratory", "lab report", "specimen", "reference range", "loinc"]
    discharge_signals = ["discharge summary", "hospital course", "discharge medications", "follow-up"]

    score_referral = sum(1 for s in referral_signals if s in lowered)
    score_lab = sum(1 for s in lab_signals if s in lowered)
    score_discharge = sum(1 for s in discharge_signals if s in lowered)

    if max(score_referral, score_lab, score_discharge) == 0:
        return "unknown"
    if score_lab >= max(score_referral, score_discharge):
        return "lab_report"
    if score_discharge >= score_referral:
        return "discharge_summary"
    return "referral"


# -- Class-specific LLM extraction ---------------------------------------


_PROMPTS: dict[DocClass, str] = {
    "referral": (
        "Extract structured clinical data from this REFERRAL letter. "
        "Identify: referring physician specialty, referral reason (chief complaint), "
        "presenting conditions (ICD-10 where inferrable), current medications "
        "(RxNorm where inferrable), pertinent observations (vitals, labs cited)."
    ),
    "lab_report": (
        "Extract structured clinical data from this LABORATORY report. "
        "Identify: each lab observation (LOINC code if known, value, unit, "
        "reference range, abnormal flag), specimen collection date, "
        "ordering provider where present."
    ),
    "discharge_summary": (
        "Extract structured clinical data from this DISCHARGE SUMMARY. "
        "Identify: principal diagnosis (ICD-10), secondary diagnoses, "
        "discharge medications (RxNorm + dose + frequency), key observations "
        "from hospital course, discharge disposition."
    ),
    "unknown": (
        "Extract structured clinical data from this medical document. "
        "Identify: any conditions (ICD-10), medications (RxNorm), "
        "observations (LOINC where applicable)."
    ),
}


def llm_extract(text: str, doc_class: DocClass) -> dict[str, Any]:
    """Class-specific LLM extraction call.

    The prompt is dispatched based on doc_class. Returns parsed JSON.
    """
    client = anthropic.Anthropic()
    prompt = (
        _PROMPTS[doc_class]
        + "\n\nReturn JSON with these top-level arrays:\n"
        + '{"observations": [{"code": "<loinc>", "display": "<name>", '
        + '"value": <num|str|null>, "unit": "<unit|null>", '
        + '"effective_datetime": "<iso8601|null>"}], '
        + '"conditions": [{"code": "<icd10>", "display": "<name>", '
        + '"clinical_status": "active|...", "onset_datetime": "<iso8601|null>"}], '
        + '"medications": [{"code": "<rxnorm>", "display": "<name>", '
        + '"dose_text": "<dose|null>", "frequency": "<freq|null>"}]}\n\n'
        + "When a code is not confidently inferrable, set code to '' and "
        + "rely on display.\n\nDocument:\n"
        + text
    )
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())


# -- Pipeline orchestration ----------------------------------------------


def process_pdf(pdf_path: Path) -> ExtractedDocument:
    text, page_count, route, route_confidence = extract_text(pdf_path)

    # PHI-redaction is pre-LLM. Redacted text is what the LLM sees.
    # Original plaintext stays in volatile capsule memory only.
    redacted_text, redaction_tags = redact_phi(text)

    doc_class = classify_document(redacted_text)

    warnings: list[str] = []
    if route == "tesseract_ocr":
        warnings.append("text_extracted_via_ocr_fallback")
    elif route == "vision_llm":
        warnings.append("text_extracted_via_vision_llm_fallback")
    if doc_class == "unknown":
        warnings.append("document_class_routing_returned_unknown")
    if redaction_tags:
        warnings.append(f"phi_redacted_n={len(redaction_tags)}_kinds={set(redaction_tags)}")

    if route in ("tesseract_ocr", "vision_llm") and "STUB" in redacted_text:
        # In stub mode the LLM extraction would be vacuous; skip it
        # and emit empty FHIR shape so the pipeline contract is still
        # demonstrated.
        return ExtractedDocument(
            document_reference=FHIRDocumentReference(
                document_class=doc_class,
                extraction_route=route,
                extraction_confidence=route_confidence,
                source_filename=pdf_path.name,
                source_size_bytes=pdf_path.stat().st_size,
                page_count=page_count,
            ),
            extraction_warnings=warnings + ["fallback_layer_is_stub_in_v0_1"],
        )

    extracted = llm_extract(redacted_text, doc_class)

    return ExtractedDocument(
        document_reference=FHIRDocumentReference(
            document_class=doc_class,
            extraction_route=route,
            extraction_confidence=route_confidence,
            source_filename=pdf_path.name,
            source_size_bytes=pdf_path.stat().st_size,
            page_count=page_count,
        ),
        observations=[
            FHIRObservation(
                code=o.get("code", "") or "",
                display=o.get("display", "") or "",
                value=o.get("value"),
                unit=o.get("unit"),
                effective_datetime=o.get("effective_datetime"),
            )
            for o in extracted.get("observations", [])
        ],
        conditions=[
            FHIRCondition(
                code=c.get("code", "") or "",
                display=c.get("display", "") or "",
                clinical_status=c.get("clinical_status", "active") or "active",
                onset_datetime=c.get("onset_datetime"),
            )
            for c in extracted.get("conditions", [])
        ],
        medications=[
            FHIRMedication(
                code=m.get("code", "") or "",
                display=m.get("display", "") or "",
                dose_text=m.get("dose_text"),
                frequency=m.get("frequency"),
            )
            for m in extracted.get("medications", [])
        ],
        extraction_warnings=warnings,
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(INPUT_DIR.glob("*.pdf"))
    if not pdfs:
        print("No input PDFs found in /data/input/uploads")
        return
    results = []
    for pdf in pdfs:
        print(f"Processing {pdf.name}")
        doc = process_pdf(pdf)
        print(
            f"  → class={doc.document_reference.document_class} "
            f"route={doc.document_reference.extraction_route} "
            f"obs={len(doc.observations)} conditions={len(doc.conditions)} "
            f"meds={len(doc.medications)} warnings={len(doc.extraction_warnings)}"
        )
        results.append(json.loads(doc.model_dump_json()))
    output_path = OUTPUT_DIR / "extraction.json"
    output_path.write_text(json.dumps(
        {
            "schema_version": "fhir-r4-bundle-flattened-v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "documents": results,
        },
        indent=2,
    ))
    print(f"Extracted {len(results)} documents → {output_path}")


if __name__ == "__main__":
    main()

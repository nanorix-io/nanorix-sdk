# med-ai-extraction

**Production-pattern reference** for clinical document extraction inside a Nanorix capsule. Demonstrates the OCR-first pipeline shape that matches how Tennr / Sully / Notable structure their production document-AI workflows: text-extraction routing layer (pypdf → Tesseract OCR fallback → Claude vision fallback) → document-class router → class-specific LLM extraction → PHI-redaction stub → FHIR R4-shaped output.

> **Important: this is a *pattern reference*, not a production redactor or a SaMD-classified clinical AI.** The OCR fallback layers and PHI-redaction layer ship as stubs that demonstrate the integration shape. Production customers fill the stubs with their chosen vendor (Google Document AI clinical parser / AWS Textract Medical / Azure Form Recognizer / Microsoft Presidio / Philter) and assess SaMD classification with their regulatory counsel before any clinical deployment.

## Industry signal

Companies running adjacent claims-document / referral-letter / lab-report extraction patterns: **Tennr** (claims faxes + referrals), **Sully** (referral letters → structured intake), **Notable** (provider-side document workflows), **Augmedix** (clinical document drafting), **Nuance DAX** (ambient documentation). Production pipelines in this space are uniformly OCR-first: scanned faxes, handwritten lab values, and old-EMR-export PDFs constitute the bulk of real-world clinical document corpora — text-only `pypdf` extraction misses 60-80% of the corpus.

## Pipeline shape

```
PDF input
  │
  ▼
[Layer 1] pypdf text extraction  ──────┐
  │                                     │
  │  confidence < threshold              │
  ▼                                     │
[Layer 2] Tesseract OCR (pytesseract) ──┤  →  text out
  │                                     │
  │  OCR confidence < threshold         │
  ▼                                     │
[Layer 3] Claude vision-LLM ────────────┘
  │
  ▼
PHI redaction stub (regex SSN/DOB/MRN/phone/email)
  │
  ▼
Document-class router (referral | lab_report | discharge_summary | unknown)
  │
  ▼
Class-specific LLM extraction (Claude w/ class-tuned prompt)
  │
  ▼
FHIR R4 output (DocumentReference + Observations + Conditions + Medications)
```

## Layered extraction strategy

| Layer | Tool | When triggered | Cost | Production substitute |
|-------|------|----------------|------|----------------------|
| 1 | `pypdf` | Always tried first | $0 (in-capsule) | `pdfplumber`, `pymupdf` |
| 2 | Tesseract via `pytesseract` | Layer 1 chars-per-page < `PYPDF_MIN_CHARS_PER_PAGE` | $0 (in-capsule, CPU-bound) | Google Document AI clinical parser, AWS Textract Medical, Azure Form Recognizer |
| 3 | Claude vision (3.5 Sonnet) | Layer 2 confidence < `OCR_MIN_CONFIDENCE` | $$$ (vision LLM) | GPT-4 vision, Gemini 1.5 vision |

Routing thresholds are env-tunable (`PYPDF_MIN_CHARS_PER_PAGE`, `OCR_MIN_CONFIDENCE`). Customer tunes per their corpus — referral letters from EMR exports tend to clear Layer 1; faxed lab reports usually require Layer 2; handwritten clinic notes typically only resolve at Layer 3.

## PHI-redaction stub

The `redact_phi` function is **not a production redactor**. It applies regex patterns for SSN, DOB, MRN, phone, email — illustrative only. The redaction call sits **between** text extraction and the LLM call: redacted text goes to the LLM; original plaintext stays in volatile capsule memory only and is destroyed with the capsule.

Production deployments substitute one of:

- **Microsoft Presidio** — open-source PII/PHI redaction with NER backend
- **Philter (BeKnown Health)** — production clinical PHI redactor, HHS Safe Harbor §164.514(b)(2)(i) 18-element coverage
- **AWS Comprehend Medical PHI detection** — managed service
- **Customer's HHS Safe Harbor de-identification pipeline** — bespoke

The redaction layer's outputs flow into AuditProof activity_trail as redaction-event tags (count + kind, never values), so verifier replay can confirm "N PHI tokens of kinds {SSN, DOB, MRN} were redacted before LLM exposure" without seeing the original.

## Document-class router

Stub keyword-heuristic router classifies into `referral | lab_report | discharge_summary | unknown`. Each class triggers a class-specific extraction prompt (referral → chief complaint + referring specialty; lab → LOINC observations; discharge → ICD-10 diagnoses + RxNorm meds).

Production substitute: fine-tuned classifier (DistilBERT / Clinical-BERT on customer's labeled corpus) or LLM-based routing call.

## FHIR R4 output schema

Output JSON is shaped for direct ingestion by EHR FHIR endpoints (Epic Hyperdrive REST, Cerner Millennium FHIR, Oracle Health Discern). Top-level shape is a flattened FHIR transaction-bundle:

```json
{
  "schema_version": "fhir-r4-bundle-flattened-v1",
  "generated_at": "2026-...",
  "documents": [
    {
      "document_reference": {
        "document_class": "lab_report",
        "extraction_route": "tesseract_ocr",
        "extraction_confidence": 0.83,
        "source_filename": "...",
        "source_size_bytes": 0,
        "page_count": 4
      },
      "observations": [
        {
          "code": "2160-0",
          "code_system": "http://loinc.org",
          "display": "Creatinine [Mass/volume] in Serum or Plasma",
          "value": 1.2,
          "unit": "mg/dL",
          "effective_datetime": "2026-..."
        }
      ],
      "conditions": [
        {
          "code": "N18.3",
          "code_system": "http://hl7.org/fhir/sid/icd-10",
          "display": "Chronic kidney disease, stage 3 (moderate)",
          "clinical_status": "active",
          "onset_datetime": null
        }
      ],
      "medications": [
        {
          "code": "1373928",
          "code_system": "http://www.nlm.nih.gov/research/umls/rxnorm",
          "display": "Lisinopril 10 MG Oral Tablet",
          "dose_text": "10 mg PO",
          "frequency": "daily"
        }
      ],
      "extraction_warnings": [
        "text_extracted_via_ocr_fallback"
      ]
    }
  ]
}
```

Code-systems align with the canonical FHIR R4 URIs: LOINC for observations, ICD-10 for conditions, RxNorm for medications.

## Why a Nanorix capsule

PDFs of medical records contain PHI under HIPAA. Calling an LLM with PHI in the prompt triggers HIPAA's "minimum necessary" + business-associate clauses. Without sealed isolation, the calling host (your AI service, your dev laptop, your CI runner) becomes part of the HIPAA scope. With Nanorix:

- The PDF lives in volatile memory only, destroyed with the capsule.
- The HTTPS call to `api.anthropic.com` (and to `documentai.googleapis.com` for the cloud OCR substitution path) flows through the sealed egress layer: TLS pin verification + per-request hash + duration recorded in the AuditProof.
- The structured output is hashed into the activity trail (not the raw PHI text).
- The PHI-redaction event count is hashed into the activity trail; the redacted strings are not recorded in AuditProof.
- The AuditProof is signed Ed25519 and emits as evidence the capsule destroyed all PHI it touched.

## What the AuditProof records

Activity-trail events for one PDF processed through the full fallback chain:

1. **`FileUploaded`** — one per uploaded PDF. Carries `filename` + `size_bytes` + SHA-512 of bytes.
2. **`SealedHttpRequest`** (one per OCR cloud call when Layer 2 substitutes Document AI / Textract) — `host`, `body_hash` (SHA-512 of rasterized page bytes), `response_body_hash`, TLS metadata.
3. **`SealedHttpRequest`** (one per Claude extraction call) — `body_hash` carries the SHA-512 of the rendered prompt (which embeds the redacted text + class-specific instructions); `response_body_hash` carries the SHA-512 of the structured response.
4. **`SealedHttpRequest`** (one per Claude vision call when Layer 3 triggers) — same shape, with rasterized page bytes in body.
5. **`ExecutionCompleted`** — exit + duration.
6. **`FileDownloaded`** — `extraction.json` output.

The customer's verifier replay confirms that the PDF bytes the customer logged match `body_hash` of the OCR/vision call (when those layers triggered), the LLM's response matches `response_body_hash`, and TLS validation passed against the pinned cert.

## Compliance binding

```python
from nanorix import Client
from nanorix.compliance import HIPAAContext

client = Client()
proof = client.proofs.get(capsule_id)
HIPAAContext().bind(proof)  # → factual reference map under §164.312
```

The `regulatory_context` block enumerates HIPAA Security Rule sections this AuditProof's activity trail relates to (§164.312(a) access control, §164.312(b) audit controls, §164.312(c) integrity, §164.312(e) transmission security). Nanorix uses `related_to` language only — never asserts compliance status. Customer's compliance officer makes the final determination.

For SaMD-classified clinical AI workflows, customer's regulatory counsel determines FDA Class (I / II / III) per the AI/ML Action Plan and applicable 510(k) / De Novo submission strategy. Nanorix attests to the data-handling boundary; customer attests to the clinical workflow.

## Running this bundle

```bash
nanorix build -f capsulefile.yaml
# → cf_<uuid>

# Drop sample medical PDFs into input/
cp /path/to/redacted-sample.pdf input/

# Create capsule + execute
CAPSULE=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification PHI)
nanorix capsules upload $CAPSULE -f input/redacted-sample.pdf
ANTHROPIC_API_KEY=sk-ant-... nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f extraction.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

## What this bundle does NOT cover

- **Multi-document batch processing** — see [`legal-ediscovery/`](../legal-ediscovery/) for the batch + CAL pattern.
- **Long-running multi-turn extraction** — see [`long-running-agent/`](../long-running-agent/) for memory artifact composition.
- **Custom KMS signing** — see [`claims-processing/`](../claims-processing/) for the per-event customer attestation pattern.
- **Production OCR / vision-LLM fallback bodies** — Layers 2+3 ship as stubs. Customer fills with their chosen vendor.
- **Production PHI redactor** — `redact_phi` is regex-only illustrative; customer fills with Presidio / Philter / Comprehend Medical.
- **SaMD classification** — Nanorix attests to data-handling; SaMD class for the clinical workflow is customer + regulatory counsel.

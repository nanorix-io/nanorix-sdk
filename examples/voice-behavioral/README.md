# voice-behavioral

**Production-pattern reference** for clinical voice AI inside a Nanorix capsule. Demonstrates the pipeline shape that Eleos Health / Cogito / behavioral-AI vendors operate: audio in → 42 CFR Part 2 consent gate → VAD silence strip → speaker diarization (clinician vs patient channel) → per-speaker Whisper ASR → behavioral risk LLM scoring → real-time SI/abuse escalation webhook → FHIR-encounter-linked structured output.

> **Distinguish two distinct workflows.** Clinical AI scribes (**Abridge**, **Suki**) generate SOAP notes from clinician-patient encounters — different risk profile, different downstream consumer. **Eleos Health**, **Cogito** behavioral analytics is the workflow this bundle approximates. Don't conflate.

> **Important: this is NOT a SaMD-classified clinical product.** Production behavioral-risk scoring that informs clinician management is **likely Class II SaMD** under FDA's AI/ML Action Plan + 21st Century Cures §3060 carve-outs. The classification fulcrum is "informs vs directs" patient management. Customer must determine SaMD class with regulatory counsel before any clinical deployment.

## Pipeline shape

```
audio.{mp3,wav,m4a,flac}
  │
  ▼
[Consent gate] 42 CFR Part 2 — abort if substance-use encounter
                                without part2_consent_acknowledged
  │
  ▼
[Layer 1] VAD silence strip (silero-vad / WebRTC VAD)
  │
  ▼
[Layer 2] Speaker diarization (pyannote.audio 3.1 / WhisperX)
          ├─ clinician channel
          ├─ patient channel
          └─ third_party / unknown
  │
  ▼
[Layer 3] Per-segment Whisper ASR (clip → submit → aggregate)
  │
  ▼
[Layer 4] Per-speaker behavioral assessment (Claude)
          ├─ patient: risk + safety_triggers
          └─ clinician: workflow signals
  │
  ▼
[Layer 5] Real-time escalation webhook
          fires on patient.safety_triggers ≠ ∅
  │
  ▼
FHIR-Encounter-linked structured output
```

## What's implemented vs stubbed

| Layer | Implementation | Production substitute |
|-------|----------------|----------------------|
| Consent gate | **Implemented** — reads `part2_consent_acknowledged` from capsule metadata, aborts before ASR if absent for substance-use encounters | Customer's signed Part 2 §2.31 written-consent record system; consent_id in metadata for audit binding |
| VAD silence strip | Stub | silero-vad, WebRTC VAD, py-webrtcvad |
| Speaker diarization | Stub falls back to single-segment unknown-role; pyannote.audio 3.1 import path wired (commented production code) | pyannote.audio 3.1 (GPU), WhisperX, AssemblyAI Universal-1, Speechmatics |
| Per-segment Whisper ASR | **Implemented** for the stub single-segment case (calls real Whisper-1) | Domain-tuned Whisper (Suki / Abridge custom fine-tunes), Nuance Dragon Medical, AssemblyAI Universal-1 medical |
| Per-speaker behavioral assessment | **Implemented** — separate prompts for patient / clinician channels, distinguishes risk_categories from safety_triggers | Customer's clinical-safety-board-approved prompt + post-processing rules |
| Real-time escalation | **Implemented** — POSTs to `ESCALATION_WEBHOOK_URL`, falls back to synthetic envelope when not configured | PagerDuty Events API, Twilio SMS, Epic in-basket message via FHIR Communication, custom on-call routing |
| FHIR encounter linkage | **Implemented** — emits encounter_id + fhir_subject_reference in structured output | Customer's EHR write-back layer (Epic Hyperdrive REST → Encounter / Observation / Communication; Cerner Millennium FHIR) |

## 42 CFR Part 2 consent gate

42 CFR Part 2 governs federally-assisted substance-use disorder records. Sending a substance-use transcript to Anthropic for behavioral scoring is a **redisclosure event** under §2.31. The pipeline gates on a `part2_consent_acknowledged: true` field in capsule metadata; if the encounter is flagged `is_substance_use_encounter: true` and consent is absent, the pipeline aborts before any ASR call (no audio bytes leave the capsule).

The `part2_consent_id` field carries the customer-side consent record identifier; if present, it's hashed into the AuditProof activity_trail, binding "this AI run was authorized by consent record ID X" cryptographically.

## Real-time escalation

Mandatory under clinical-safety governance for any behavioral-health AI that scores patient safety signals. Detected `safety_triggers` (self_harm_ideation, suicidal_intent, abuse_disclosure, acute_substance_use, homicidal_ideation) trigger an immediate webhook POST to `ESCALATION_WEBHOOK_URL`.

The webhook payload includes:
- `encounter_id` — FHIR Encounter reference for chart deep-linking
- `safety_triggers` — list of triggered categories
- `risk_score` + `confidence` — for triage prioritization
- `recommended_followup` — model-generated action recommendation
- `alerted_at` — ISO-8601 timestamp

The webhook URL is sealed-egress-routed; the call appears as one `SealedHttpRequest` in AuditProof. Verifier replay confirms "this AI run detected SI signal X, escalated to URL Y at time Z, webhook returned status N." Without escalation in AuditProof, regulators / clinical safety boards cannot audit the safety-trigger response chain.

> Absent `ESCALATION_WEBHOOK_URL`, the pipeline records a synthetic `no_webhook_configured` envelope and prints a warning. This is **development / non-clinical use only**.

## FHIR encounter linkage

Production deployments link the assessment back to the EHR encounter via FHIR R4 `Encounter.id`. Pattern:

1. Customer's intake workflow creates the encounter in Epic / Cerner.
2. Customer captures `encounter_id` (FHIR Encounter resource ID).
3. Customer creates the Nanorix capsule with `metadata.encounter_id = "<id>"` and `metadata.fhir_subject_reference = "Patient/<id>"`.
4. Pipeline emits both fields in the structured output.
5. Customer's EHR write-back layer ingests the structured output, posts back to FHIR `/Encounter/{id}` (linking the assessment) + `/Communication` (for the safety-trigger alert) + `/Observation` (for the risk score as a clinical observation).

The capsule's `metadata` slot is open-vocabulary by design; customer-defined keys like `encounter_id` flow through unchanged.

## Industry signal

Companies running adjacent behavioral / counseling voice-AI patterns: **Eleos Health** (behavioral health AI scoring + supervision), **Cogito** (real-time call coaching with patient affect detection), **Lyssn** (counselor performance scoring), and provider-side teams running custom risk-scoring on counseling session recordings. Adjacent-but-distinct: **Abridge** (ambient SOAP scribe), **Suki** (ambient documentation), **Augmedix** (transcription + chart prep), **Nuance DAX** (ambient clinical documentation).

## Why a Nanorix capsule

Audio recordings contain spoken PHI in clinical contexts, plus the highest-stakes personal content (suicidal ideation, substance use disclosures, abuse disclosures) in behavioral health. Three sealed HTTPS calls per encounter: Whisper for transcription, Claude for assessment, escalation webhook. Each carries audio bytes (Whisper) / transcript (Claude) / safety-trigger envelope (webhook) into a third-party service.

With Nanorix:
- The audio file lives in volatile memory only.
- All three calls are sealed-daemon-routed: TLS validated against system trust roots + body hash + response hash + duration recorded.
- The transcript text is held in capsule memory; AuditProof binds the SHA-512 hash but not the plaintext.
- The behavioral assessment is structured and bound into the activity_trail.
- The escalation event (when fired) is one `SealedHttpRequest` in AuditProof, providing audit-trail evidence that safety-trigger response actually happened.
- The capsule destroys all session data.

## What the AuditProof records

- **`FileUploaded`** — one per audio file. Carries SHA-512 of audio bytes + size.
- **`SealedHttpRequest`** (Whisper ASR) — request body hash includes the audio bytes; response is the transcript text.
- **`SealedHttpRequest`** (Claude patient assessment) — request body hash includes the rendered prompt with the patient transcript; response is the assessment JSON.
- **`SealedHttpRequest`** (Claude clinician assessment) — same shape, clinician channel.
- **`SealedHttpRequest`** (escalation webhook, when fired) — body hash includes the safety-trigger payload; response carries the webhook's status code.
- **`StreamingEgressStarted` / `StreamingEgressChunk` / `StreamingEgressCompleted`** — emitted IF Whisper's response uses chunked-transfer-encoding or SSE (Whisper's streaming-transcription mode). Each chunk carries its own SHA-512; the streaming Merkle root binds them.
- **`ExecutionCompleted`** — exit + duration.

## Compliance binding

```python
from nanorix.compliance import HIPAAContext, GDPRContext, Part2Context

# US clinical: HIPAA
HIPAAContext().bind(proof)

# US substance-use disclosures: 42 CFR Part 2 (stricter than HIPAA)
Part2Context().bind(proof)

# EU behavioral health: GDPR Article 9 special-category personal data
GDPRContext().bind(proof)  # Art 9(2) lawful basis + Art 32 security
```

`GDPRContext` produces a `regulatory_context` block enumerating GDPR articles the AuditProof's activity_trail relates to — Article 5 (purpose limitation), Article 9 (special-category processing requires explicit consent or other Art 9(2) basis), Article 25 (data protection by design), Article 32 (security of processing), Article 30 (records of processing).

## Running this bundle

```bash
nanorix build -f capsulefile.yaml

# Capsule create with FHIR encounter metadata + Part 2 consent flag
CAPSULE=$(nanorix capsules create \
  --capsulefile cf_<uuid> \
  --jurisdiction US \
  --data-classification PHI \
  --metadata '{"encounter_id":"enc_5f3a","fhir_subject_reference":"Patient/p_8821","is_substance_use_encounter":true,"part2_consent_acknowledged":true,"part2_consent_id":"consent_a1b2","speaker_role_map":{"SPEAKER_00":"clinician","SPEAKER_01":"patient"}}')

nanorix capsules upload $CAPSULE -f input/session.mp3
OPENAI_API_KEY=... ANTHROPIC_API_KEY=sk-ant-... \
  ESCALATION_WEBHOOK_URL=https://oncall.example.com/alert \
  HUGGINGFACE_TOKEN=hf_... \
  nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f assessments.jsonl
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

## Multi-session sessions (>60min)

A 90-minute therapy session would exceed the V1 60-minute capsule ceiling. Use the **memory artifact pattern** to checkpoint at 50-minute boundaries: each 50-minute capsule produces a memory artifact (encrypted with customer's KMS key, never plaintext on Nanorix infrastructure). The next capsule loads the checkpoint and continues. The two AuditProofs link via `parent_cdp_id`. See [`long-running-agent/`](../long-running-agent/).

## What this bundle does NOT cover

- **Production-grade VAD / diarization layer bodies** — Layers 1-2 ship as stubs with the production import path wired but commented. Customer fills with pyannote.audio 3.1 (GPU) + silero-vad, WhisperX, or pre-diarized upload.
- **Domain-tuned ASR** — uses stock Whisper-1 (15-25% WER on clinical jargon). Customer substitutes Suki / Abridge custom fine-tunes, Nuance Dragon Medical, or AssemblyAI Universal-1 medical.
- **Production PHI redactor for transcript pre-LLM** — see `med-ai-extraction` bundle's `redact_phi` pattern for the integration shape.
- **Production escalation routing logic** — escalation webhook is a single POST. Customer's escalation system handles deduplication, on-call rotation, retry, acknowledgment workflow.
- **SaMD classification** — Nanorix attests to data-handling boundary; SaMD class for the clinical decision-support workflow is customer + regulatory counsel.

"""Voice transcription + behavioral risk reference workload — production pattern.

Pipeline shape: audio file (PHI / sensitive disclosures) → 42 CFR Part 2
consent gate → VAD silence stripping → speaker diarization (clinician /
patient channel separation) → per-speaker Whisper ASR → behavioral risk
LLM scoring → real-time SI/abuse escalation webhook → /data/output.

Every outbound HTTPS call (Whisper, Claude assessment, escalation
webhook) is sealed-egress-routed and bound into AuditProof. The audio
file lives in volatile memory only.

This bundle demonstrates the production-shape composition that
clinical voice-AI vendors (Eleos Health, Cogito, Abridge, Suki)
operate. STUB layers are explicitly marked — production deployments
fill them with customer's chosen vendor or a GPU-backed in-capsule
implementation.

HIPAA business-associate framing
--------------------------------
Audio recordings in clinical context are PHI under 45 CFR 160.103.
Customer is the covered entity; Nanorix is downstream BA only via
signed BAA. Customer's signed BAA chain (clinician → EHR vendor → AI
vendor → Nanorix) governs the data flow. This code operates inside
that chain — it does NOT itself constitute a BAA, nor does Nanorix's
attestation satisfy 45 CFR 164.504(e).

42 CFR Part 2 redisclosure
--------------------------
Substance-use disclosures captured in clinician-patient encounters
fall under 42 CFR Part 2 — stricter than HIPAA on redisclosure.
Sending the transcript to Anthropic for behavioral scoring IS a
redisclosure event. Customer must hold patient consent for
redisclosure (typically a Part 2 §2.31-compliant written consent
referencing the AI vendor) BEFORE this pipeline runs. The pipeline
GATES on a `part2_consent_acknowledged` metadata flag — absent or
false, the pipeline aborts before ASR.

FHIR encounter linkage
----------------------
Production deployments link the assessment back to the Epic / Cerner
encounter via FHIR R4 `encounter_id` (Encounter resource, R4 path
`/Encounter/{id}`). The capsule receives `encounter_id` as a
metadata field at create time; the pipeline emits `encounter_id` in
the structured output for downstream EHR write-back.

Real-time SI/abuse escalation
-----------------------------
Mandatory under clinical-safety governance for behavioral health AI.
Detected SI / abuse / acute risk signals trigger an immediate
webhook POST to the customer-configured escalation endpoint (an
on-call clinician's PagerDuty integration, the supervising LCSW's
SMS gateway, etc). The webhook URL is read from the
ESCALATION_WEBHOOK_URL env var; absent it, the pipeline falls back
to logging a warning into the structured output (development /
non-clinical use only).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import anthropic
import httpx
from openai import OpenAI
from pydantic import BaseModel, Field

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
METADATA_PATH = Path("/data/input/metadata.json")  # capsule create-time metadata
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-1")
ESCALATION_WEBHOOK_URL = os.environ.get("ESCALATION_WEBHOOK_URL", "")
DIARIZATION_MIN_SEGMENT_S = float(os.environ.get("DIARIZATION_MIN_SEGMENT_S", "0.5"))


# -- Speaker channel + segment shape --------------------------------------


SpeakerRole = Literal["clinician", "patient", "third_party", "unknown"]


class DiarizedSegment(BaseModel):
    """One diarization segment.

    Production: pyannote.audio's SpeakerDiarization pipeline returns a
    list of (start, end, speaker_label) tuples; the speaker_label is
    a generic "SPEAKER_00" / "SPEAKER_01". Mapping to clinician /
    patient roles is a downstream step (heuristic via segment-order
    or via embedding-comparison to a reference clinician voice). For
    higher-stakes deployments (Eleos / Cogito), customers ship the
    role mapping with the capsule's metadata so it's deterministic.
    """

    speaker_id: str  # e.g. "SPEAKER_00"
    speaker_role: SpeakerRole
    start_s: float
    end_s: float
    transcript: str = ""  # filled in after per-segment Whisper pass


class BehavioralAssessment(BaseModel):
    """Per-speaker behavioral assessment.

    Production assessments separate clinician vs patient signals.
    Patient SI / abuse / substance-use disclosures are the actionable
    safety signals; clinician-side signals (e.g. clinician fatigue,
    missed empathic response) are separate workflow signals.
    """

    risk_score: float = Field(ge=0.0, le=1.0)
    risk_categories: list[str]  # e.g. ["self_harm_ideation", "substance_use"]
    safety_triggers: list[str]  # subset of risk_categories that REQUIRE escalation
    recommended_followup: str
    confidence: float = Field(ge=0.0, le=1.0)


class EncounterAssessment(BaseModel):
    """Top-level structured output bound to FHIR Encounter."""

    source: str
    encounter_id: str | None  # FHIR Encounter.id reference
    fhir_subject_reference: str | None  # FHIR Patient/{id} reference
    duration_s: float
    speaker_segment_count: int
    diarization_route: str  # "pyannote" | "stub_no_diarization"
    asr_route: str  # "whisper-1" | "domain_finetune"
    patient_assessment: BehavioralAssessment | None
    clinician_assessment: BehavioralAssessment | None
    escalation: dict | None
    pipeline_warnings: list[str] = Field(default_factory=list)
    generated_at: str


# -- Consent gate (42 CFR Part 2) ---------------------------------------


class CapsuleMetadata(BaseModel):
    """Customer-declared metadata at capsule create time.

    Loaded from /data/input/metadata.json (which the runtime
    materializes from the capsule create POST body's metadata map).
    """

    encounter_id: str | None = None
    fhir_subject_reference: str | None = None  # Patient/{id}
    part2_consent_acknowledged: bool = False
    part2_consent_id: str | None = None  # customer-side consent record id
    speaker_role_map: dict[str, str] = Field(default_factory=dict)
    # e.g. {"SPEAKER_00": "clinician", "SPEAKER_01": "patient"}
    is_substance_use_encounter: bool = False


def load_metadata() -> CapsuleMetadata:
    """Load create-time metadata.

    For local-test / development runs without a real metadata.json,
    returns defaults. Production capsules always materialize this
    file from the create POST.
    """
    if not METADATA_PATH.exists():
        return CapsuleMetadata()
    return CapsuleMetadata.model_validate_json(METADATA_PATH.read_text())


def gate_part2_consent(meta: CapsuleMetadata) -> None:
    """42 CFR Part 2 consent gate.

    For substance-use encounters: redisclosure to Anthropic for
    behavioral scoring requires Part 2 §2.31-compliant written consent.
    Absent it, abort before ASR — never call out to a third party
    with substance-use audio.
    """
    if not meta.is_substance_use_encounter:
        return
    if not meta.part2_consent_acknowledged:
        print(
            "[consent_gate] 42 CFR Part 2 consent NOT acknowledged for "
            "substance-use encounter; aborting before ASR.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if not meta.part2_consent_id:
        print(
            "[consent_gate] Part 2 consent acknowledged but no "
            "consent_id recorded; production policy may require id "
            "for audit binding. Continuing with warning.",
            file=sys.stderr,
        )


# -- VAD (voice-activity detection) --------------------------------------


def vad_silence_strip(audio_path: Path) -> tuple[Path, list[str]]:
    """Layer 1: VAD silence stripping pre-ASR.

    STUB — production substitutes one of:
      - silero-vad (open-source, CPU)
      - WebRTC VAD (in pyannote.audio / WhisperX)
      - py-webrtcvad

    Stripping silence reduces ASR cost (Whisper is per-second priced)
    and improves diarization accuracy. Stub returns the original
    audio path unchanged + a warning tag.
    """
    print("  [vad] STUB — production substitutes silero-vad / WebRTC VAD", file=sys.stderr)
    return audio_path, ["vad_silence_strip_stub"]


# -- Diarization (pyannote) ---------------------------------------------


def diarize_speakers(
    audio_path: Path, role_map: dict[str, str]
) -> tuple[list[DiarizedSegment], str, list[str]]:
    """Layer 2: speaker diarization (clinician vs patient channel).

    Production: pyannote.audio.Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token="<HF_TOKEN>"
    ).

    pyannote's diarization-3.1 pipeline runs on GPU; CPU runs are
    unbounded. Production deployments either:
      (a) run the capsule on a GPU-backed Nanorix runtime tier
          (in-capsule pyannote), or
      (b) pre-diarize via a separate GPU service before uploading
          to the capsule (segments are uploaded as JSONL alongside
          the audio).

    The role_map (from capsule metadata) translates pyannote's
    generic SPEAKER_00 / SPEAKER_01 labels to clinician / patient
    semantic roles. If absent, falls back to "unknown" for all.

    STUB — returns a single full-length unknown-role segment when
    pyannote isn't loaded (CPU path / dev mode).
    """
    try:
        # The actual production import path. Wrapped in try because
        # pyannote requires HuggingFace auth token + GPU for the v3.1
        # pipeline; dev / CI runs fall back to the stub.
        from pyannote.audio import Pipeline as _PyannotePipeline  # noqa: F401
        # Production:
        # pipeline = _PyannotePipeline.from_pretrained(
        #     "pyannote/speaker-diarization-3.1",
        #     use_auth_token=os.environ["HUGGINGFACE_TOKEN"],
        # )
        # diarization = pipeline(str(audio_path))
        # segments = []
        # for turn, _, speaker in diarization.itertracks(yield_label=True):
        #     if turn.duration < DIARIZATION_MIN_SEGMENT_S:
        #         continue
        #     role = role_map.get(speaker, "unknown")
        #     segments.append(DiarizedSegment(
        #         speaker_id=speaker, speaker_role=role,
        #         start_s=turn.start, end_s=turn.end,
        #     ))
        # return segments, "pyannote", []
        raise ImportError("pyannote stub path")  # remove in production
    except ImportError:
        print(
            "  [diarize] pyannote unavailable (GPU path) — falling back to single-speaker stub",
            file=sys.stderr,
        )
        # Stub: treat the whole file as one segment, unknown role.
        # Customer's pipeline either loads pyannote or pre-diarizes.
        return (
            [DiarizedSegment(
                speaker_id="SPEAKER_00",
                speaker_role="unknown",
                start_s=0.0,
                end_s=0.0,  # filled by ASR layer when Whisper returns duration
                transcript="",
            )],
            "stub_no_diarization",
            ["diarization_stub_no_pyannote"],
        )


# -- Per-segment ASR (Whisper) ------------------------------------------


def transcribe_segment(audio_path: Path, segment: DiarizedSegment) -> str:
    """Per-segment Whisper transcription.

    Production: clip audio to [segment.start_s, segment.end_s] and
    submit each clip independently. This gives per-segment word
    timing + speaker attribution.

    For the stub diarization path (single full-length segment), this
    submits the full file once.

    Note on ASR error rate: general-purpose Whisper has 15-25% WER
    on clinical jargon. Production deployments either (a) substitute
    a domain-tuned ASR (Suki / Abridge custom Whisper fine-tunes,
    Nuance Dragon Medical), or (b) post-process Whisper output
    through a clinical-vocabulary correction LLM pass. This bundle
    uses stock Whisper to keep the egress allowlist clean.
    """
    client = OpenAI()
    with audio_path.open("rb") as f:
        response = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=f,
            # Production: pass segment-clipped audio + word-level
            # timestamps via response_format="verbose_json".
        )
    return response.text


# -- Behavioral risk LLM pass --------------------------------------------


_PATIENT_ASSESSMENT_PROMPT = (
    "You are reviewing a clinical encounter transcript. Score the "
    "PATIENT-side behavioral risk signals only. Distinguish risk "
    "categories from safety triggers: risk categories are general "
    "concern signals (e.g. low_mood, social_withdrawal); safety "
    "triggers are signals requiring IMMEDIATE clinician escalation "
    "(self_harm_ideation, suicidal_intent, abuse_disclosure, "
    "acute_substance_use, homicidal_ideation).\n\n"
    "Return JSON: {risk_score: float in [0,1], "
    "risk_categories: list[str], safety_triggers: list[str], "
    "recommended_followup: str, confidence: float in [0,1]}\n\n"
    "Patient transcript:\n"
)

_CLINICIAN_ASSESSMENT_PROMPT = (
    "You are reviewing a clinical encounter transcript from the "
    "CLINICIAN's side only. Score clinician-side workflow signals "
    "(missed empathic response, lack of safety probing, etc). "
    "Return the same JSON shape as the patient assessment. "
    "safety_triggers should be empty unless the clinician's behavior "
    "constitutes a workflow safety incident (e.g. failure to safety-"
    "plan after a patient SI disclosure).\n\n"
    "Return JSON: {risk_score: float in [0,1], "
    "risk_categories: list[str], safety_triggers: list[str], "
    "recommended_followup: str, confidence: float in [0,1]}\n\n"
    "Clinician transcript:\n"
)


def assess_speaker(transcript: str, role: SpeakerRole) -> BehavioralAssessment | None:
    if not transcript.strip():
        return None
    prompt_prefix = (
        _PATIENT_ASSESSMENT_PROMPT
        if role == "patient"
        else _CLINICIAN_ASSESSMENT_PROMPT
    )
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt_prefix + transcript}],
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return BehavioralAssessment.model_validate_json(raw.strip())


# -- Real-time escalation webhook ---------------------------------------


def escalate(
    audio_filename: str,
    encounter_id: str | None,
    safety_triggers: list[str],
    assessment: BehavioralAssessment,
) -> dict:
    """Real-time webhook escalation for safety triggers.

    Production webhook payload includes encounter_id so the on-call
    clinician's surface (PagerDuty / SMS gateway / EHR inbox) can
    deep-link back to the patient chart.

    The webhook URL is sealed-egress-routed and recorded as a
    SealedHttpRequest in AuditProof. The activity_trail therefore
    binds: "this AI run detected SI signal X, escalated to URL Y at
    time Z, response was status N." Verifier replay confirms the
    escalation happened.
    """
    payload = {
        "alert_type": "behavioral_safety_trigger",
        "source_filename": audio_filename,
        "encounter_id": encounter_id,
        "safety_triggers": safety_triggers,
        "risk_score": assessment.risk_score,
        "confidence": assessment.confidence,
        "recommended_followup": assessment.recommended_followup,
        "alerted_at": datetime.now(timezone.utc).isoformat(),
    }
    if not ESCALATION_WEBHOOK_URL:
        # Non-clinical / dev mode: log warning + return a synthetic
        # escalation envelope so the structured output records the
        # safety signal even when no live webhook is configured.
        print(
            f"[escalate] WARNING: safety_triggers={safety_triggers} but "
            "ESCALATION_WEBHOOK_URL is not set; production deployments "
            "MUST configure escalation. Recording synthetic envelope.",
            file=sys.stderr,
        )
        return {
            "status": "no_webhook_configured",
            "would_have_sent": payload,
        }
    try:
        response = httpx.post(
            ESCALATION_WEBHOOK_URL,
            json=payload,
            timeout=10,
        )
        return {
            "status": "sent",
            "webhook_status_code": response.status_code,
            "webhook_url_host": ESCALATION_WEBHOOK_URL.split("/")[2],
            "alerted_at": payload["alerted_at"],
            "safety_triggers": safety_triggers,
        }
    except httpx.HTTPError as exc:
        print(f"[escalate] webhook POST failed: {exc}", file=sys.stderr)
        return {
            "status": "webhook_post_failed",
            "error": type(exc).__name__,
            "alerted_at": payload["alerted_at"],
        }


# -- Pipeline orchestration ---------------------------------------------


def process_audio(audio_path: Path, meta: CapsuleMetadata) -> EncounterAssessment:
    warnings: list[str] = []

    # 1. VAD silence strip
    cleaned_path, vad_warnings = vad_silence_strip(audio_path)
    warnings.extend(vad_warnings)

    # 2. Diarization
    segments, diarization_route, diar_warnings = diarize_speakers(
        cleaned_path, meta.speaker_role_map
    )
    warnings.extend(diar_warnings)

    # 3. Per-segment Whisper ASR
    # Stub diarization path: single segment for the full file.
    # Production: clip per-segment + per-segment Whisper call.
    for seg in segments:
        seg.transcript = transcribe_segment(cleaned_path, seg)

    # 4. Aggregate per-speaker transcripts
    patient_text = "\n".join(s.transcript for s in segments if s.speaker_role == "patient")
    clinician_text = "\n".join(s.transcript for s in segments if s.speaker_role == "clinician")
    unknown_text = "\n".join(s.transcript for s in segments if s.speaker_role == "unknown")

    # If diarization stubbed (all unknown), assess against the
    # whole transcript as the patient channel — degraded mode.
    if not patient_text and unknown_text:
        warnings.append("patient_assessment_run_against_unattributed_transcript")
        patient_text = unknown_text

    # 5. Behavioral assessment per speaker
    patient_assessment = assess_speaker(patient_text, "patient")
    clinician_assessment = (
        assess_speaker(clinician_text, "clinician") if clinician_text else None
    )

    # 6. Real-time escalation if safety triggers fire
    escalation = None
    if patient_assessment and patient_assessment.safety_triggers:
        escalation = escalate(
            audio_path.name,
            meta.encounter_id,
            patient_assessment.safety_triggers,
            patient_assessment,
        )

    duration_s = segments[-1].end_s if segments and segments[-1].end_s > 0 else 0.0

    return EncounterAssessment(
        source=audio_path.name,
        encounter_id=meta.encounter_id,
        fhir_subject_reference=meta.fhir_subject_reference,
        duration_s=duration_s,
        speaker_segment_count=len(segments),
        diarization_route=diarization_route,
        asr_route=WHISPER_MODEL,
        patient_assessment=patient_assessment,
        clinician_assessment=clinician_assessment,
        escalation=escalation,
        pipeline_warnings=warnings,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = load_metadata()
    gate_part2_consent(meta)

    audio_files = sorted(
        f for f in INPUT_DIR.iterdir() if f.suffix.lower() in (".mp3", ".wav", ".m4a", ".flac")
    )
    if not audio_files:
        print("No audio files found in /data/input/uploads")
        return

    results = []
    for audio_path in audio_files:
        print(f"Processing {audio_path.name}")
        encounter = process_audio(audio_path, meta)
        print(
            f"  → diar={encounter.diarization_route} "
            f"segments={encounter.speaker_segment_count} "
            f"safety_triggers={encounter.patient_assessment.safety_triggers if encounter.patient_assessment else []} "
            f"escalation={encounter.escalation['status'] if encounter.escalation else 'none'}"
        )
        results.append(json.loads(encounter.model_dump_json()))

    output_path = OUTPUT_DIR / "assessments.jsonl"
    output_path.write_text("\n".join(json.dumps(r) for r in results))
    print(f"Assessed {len(results)} sessions → {output_path}")


if __name__ == "__main__":
    main()

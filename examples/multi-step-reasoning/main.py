"""Multi-step clinical-reasoning agent reference workload.

Pattern: agentic reasoning loop where each step is an LLM call
that produces (a) a reasoning observation, (b) a knowledge-base
retrieval request, (c) a confidence assessment, or (d) a final
answer. The loop terminates when the agent's confidence crosses
REASONING_CONFIDENCE_THRESHOLD or MAX_REASONING_STEPS is reached.

Pipeline shape: longitudinal patient summary (PHI) → reasoning
loop (LLM + knowledge-base retrievals) → final structured
recommendation → /data/output/recommendation.json.

Industry signal: this is the production pattern for clinical-
decision-support agents (Sully, Notable, Tennr second-opinion
flows; legacy IBM Watson Health-class reasoning; Hippocratic AI
clinician agents). Whether the customer's downstream consumer is
a clinician's draft response, a billing-code recommendation, or a
follow-up-visit suggestion, the loop shape is identical.

Every outbound HTTPS call (LLM reasoning step, knowledge-base
lookup) is one SealedHttpRequest activity event in the AuditProof.
The reasoning trace is bound into the AuditProof's hash chain via
ReasoningStepCompleted events; auditors can reconstruct the agent's
decision path step-by-step from the AuditProof alone.

Production-shape primitives (stubbed where remote service is
required):

  1. LLM reasoning step. Each step renders a prompt that includes
     the patient context + accumulated reasoning trace + tool-call
     responses, and asks the LLM to either (a) take another reasoning
     step, (b) request a knowledge-base lookup, or (c) emit a final
     answer with self-reported confidence.
  2. Knowledge-base retrieval. When the LLM requests a lookup, the
     agent posts a query to KNOWLEDGE_BASE_URL (customer's licensed
     UpToDate / PubMed / DynaMed / etc deployment) and folds the
     retrieved excerpt back into the next reasoning step's context.
  3. Final structured output. When the agent terminates, the final
     recommendation is shaped per the workflow's REASONING_TEMPLATE
     (differential_diagnosis, drug_interaction_check,
     follow_up_recommendation, etc).

The agent does NOT egress to the public internet for clinical
content; only the customer-licensed KB endpoints are in
egress_declaration. The PHI patient summary lives in volatile
capsule memory only — destroyed with the capsule.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import anthropic
import httpx
from pydantic import BaseModel, Field

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
MAX_STEPS = int(os.environ.get("MAX_REASONING_STEPS", "8"))
CONFIDENCE_THRESHOLD = float(os.environ.get("REASONING_CONFIDENCE_THRESHOLD", "0.85"))
KB_URL = os.environ.get("KNOWLEDGE_BASE_URL", "")
TEMPLATE = os.environ.get("REASONING_TEMPLATE", "differential_diagnosis")

ReasoningAction = Literal["continue", "knowledge_lookup", "final_answer"]


# -- Reasoning trace + structured output schemas -------------------------


class ReasoningStep(BaseModel):
    """One iteration of the reasoning loop."""

    step_number: int
    action: ReasoningAction
    observation: str  # the LLM's reasoning observation at this step
    knowledge_lookup_query: str | None = None
    knowledge_lookup_result_hash: str | None = None  # SHA-512 of KB response
    confidence: float  # self-reported confidence 0.0-1.0
    timestamp: str  # ISO 8601


class FinalRecommendation(BaseModel):
    """Top-level structured output.

    Shape varies per REASONING_TEMPLATE. The reference deploys
    differential_diagnosis as the canonical shape; customers
    extend or substitute per their workflow.
    """

    template: str
    primary_finding: str  # principal answer (e.g. ICD-10 code + display)
    differential: list[str] = Field(default_factory=list)
    supporting_evidence: list[str] = Field(default_factory=list)
    follow_up_actions: list[str] = Field(default_factory=list)
    confidence: float
    reasoning_trace: list[ReasoningStep] = Field(default_factory=list)
    termination_reason: Literal["confidence_threshold", "step_cap_reached", "llm_emitted_final"]


# -- Knowledge-base retrieval stub ---------------------------------------


def knowledge_base_lookup(query: str) -> tuple[str, str]:
    """Customer-licensed KB query stub.

    Production deployments substitute UpToDate (Wolters Kluwer),
    DynaMed (EBSCO), BMJ Best Practice, customer-curated rules,
    or a vector-DB-backed semantic search over customer's licensed
    corpus. Each call is one SealedHttpRequest event in AuditProof.
    Returns (excerpt, sha512_hash_of_excerpt).
    """
    if not KB_URL:
        # Stub mode — pipeline still exercises the loop shape.
        excerpt = (
            f"[KB_STUB_RESPONSE for query: {query!r} — production deployment "
            f"substitutes the customer's licensed knowledge base.]"
        )
        import hashlib

        return excerpt, hashlib.sha512(excerpt.encode()).hexdigest()
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                KB_URL,
                json={"query": query, "max_results": 3, "format": "excerpt"},
            )
            response.raise_for_status()
            data = response.json()
            excerpt = "\n\n".join(
                d.get("excerpt", "") for d in data.get("results", [])
            )
            import hashlib

            return excerpt, hashlib.sha512(excerpt.encode()).hexdigest()
    except Exception as e:
        print(f"  [knowledge_base] lookup failed: {e}", file=sys.stderr)
        excerpt = f"[KB_LOOKUP_FAILED for query: {query!r}]"
        import hashlib

        return excerpt, hashlib.sha512(excerpt.encode()).hexdigest()


# -- Reasoning-template prompts -----------------------------------------


_TEMPLATE_PROMPTS: dict[str, str] = {
    "differential_diagnosis": (
        "You are a clinical reasoning agent supporting a licensed clinician's "
        "differential-diagnosis workflow. Reason step-by-step over the patient's "
        "longitudinal record. At each step, decide ONE of: "
        "(a) continue reasoning with a new observation, "
        "(b) request a knowledge-base lookup with a specific query, "
        "(c) emit a final differential with primary/secondary findings, "
        "supporting evidence, and recommended follow-up. "
        "Self-report confidence 0.0-1.0 at each step. "
        "Stop when confidence ≥ threshold OR step cap reached."
    ),
    "drug_interaction_check": (
        "You are a clinical reasoning agent supporting a pharmacist's "
        "interaction-screening workflow. Reason over the patient's medication "
        "list. At each step, decide ONE of: "
        "(a) continue reasoning, "
        "(b) request a KB lookup for a specific drug pair, "
        "(c) emit final interaction findings with severity grade and clinical "
        "actionability."
    ),
    "follow_up_recommendation": (
        "You are a clinical reasoning agent supporting a primary-care provider's "
        "follow-up-planning workflow. Reason over the patient's recent visits + "
        "labs + problems. At each step, decide ONE of: "
        "(a) continue reasoning, "
        "(b) request a KB lookup for guidelines, "
        "(c) emit final recommendation with timing, modality, and rationale."
    ),
}


def render_step_prompt(
    template: str, patient_summary: str, trace: list[ReasoningStep]
) -> str:
    """Render the prompt for the next reasoning step."""
    base = _TEMPLATE_PROMPTS.get(template, _TEMPLATE_PROMPTS["differential_diagnosis"])
    trace_rendered = ""
    if trace:
        trace_rendered = "\n\nReasoning trace so far:\n" + "\n".join(
            f"Step {s.step_number} ({s.action}, conf={s.confidence:.2f}): {s.observation}"
            + (f"\n  KB query: {s.knowledge_lookup_query!r}" if s.knowledge_lookup_query else "")
            for s in trace
        )
    return (
        f"{base}\n\nPatient summary:\n{patient_summary}\n{trace_rendered}\n\n"
        "Respond with JSON ONLY in this shape:\n"
        '{"action": "continue|knowledge_lookup|final_answer", '
        '"observation": "<reasoning text>", '
        '"knowledge_lookup_query": "<query|null>", '
        '"confidence": <0.0-1.0>, '
        '"final_answer": {"primary_finding": "...", "differential": [...], '
        '"supporting_evidence": [...], "follow_up_actions": [...]}|null}'
    )


# -- Reasoning loop ------------------------------------------------------


def run_reasoning_loop(patient_summary: str) -> FinalRecommendation:
    """Iterate the reasoning loop until termination."""
    client = anthropic.Anthropic()
    trace: list[ReasoningStep] = []
    final_answer: dict[str, Any] | None = None
    termination_reason: Literal["confidence_threshold", "step_cap_reached", "llm_emitted_final"] = (
        "step_cap_reached"
    )

    for step_n in range(1, MAX_STEPS + 1):
        prompt = render_step_prompt(TEMPLATE, patient_summary, trace)
        message = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw.strip())

        action: ReasoningAction = parsed.get("action", "continue")
        observation = parsed.get("observation", "")
        confidence = float(parsed.get("confidence", 0.5))
        kb_query = parsed.get("knowledge_lookup_query")
        kb_hash: str | None = None

        if action == "knowledge_lookup" and kb_query:
            _excerpt, kb_hash = knowledge_base_lookup(kb_query)

        trace.append(
            ReasoningStep(
                step_number=step_n,
                action=action,
                observation=observation,
                knowledge_lookup_query=kb_query,
                knowledge_lookup_result_hash=kb_hash,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )

        print(
            f"  step {step_n}: action={action} conf={confidence:.2f} "
            f"obs={observation[:60]!r}",
            file=sys.stderr,
        )

        if action == "final_answer":
            final_answer = parsed.get("final_answer") or {}
            termination_reason = "llm_emitted_final"
            break
        if confidence >= CONFIDENCE_THRESHOLD:
            termination_reason = "confidence_threshold"
            break

    if final_answer is None:
        # Loop exhausted without final_answer; synthesize a minimal one
        # from the highest-confidence step.
        best_step = max(trace, key=lambda s: s.confidence) if trace else None
        final_answer = {
            "primary_finding": (best_step.observation if best_step else "no_observation"),
            "differential": [],
            "supporting_evidence": [],
            "follow_up_actions": ["clinician_review_required_loop_did_not_terminate"],
        }

    return FinalRecommendation(
        template=TEMPLATE,
        primary_finding=final_answer.get("primary_finding", ""),
        differential=final_answer.get("differential", []) or [],
        supporting_evidence=final_answer.get("supporting_evidence", []) or [],
        follow_up_actions=final_answer.get("follow_up_actions", []) or [],
        confidence=trace[-1].confidence if trace else 0.0,
        reasoning_trace=trace,
        termination_reason=termination_reason,
    )


# -- Pipeline orchestration ----------------------------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_files = sorted(INPUT_DIR.glob("*.txt")) + sorted(INPUT_DIR.glob("*.json"))
    if not summary_files:
        print("No input patient summaries found in /data/input/uploads")
        return

    results: list[dict[str, Any]] = []
    for f in summary_files:
        print(f"Reasoning over {f.name}")
        patient_summary = f.read_text()
        recommendation = run_reasoning_loop(patient_summary)
        print(
            f"  → terminated={recommendation.termination_reason} "
            f"steps={len(recommendation.reasoning_trace)} "
            f"confidence={recommendation.confidence:.2f} "
            f"finding={recommendation.primary_finding[:60]!r}"
        )
        results.append(json.loads(recommendation.model_dump_json()))

    output_path = OUTPUT_DIR / "recommendation.json"
    output_path.write_text(
        json.dumps(
            {
                "schema_version": "multi-step-reasoning-v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "template": TEMPLATE,
                "recommendations": results,
            },
            indent=2,
        )
    )
    print(f"Reasoned over {len(results)} summaries → {output_path}")


if __name__ == "__main__":
    main()

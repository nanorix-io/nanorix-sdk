"""LangChain agent reference workload — Tier A integration pattern.

Pattern: customer's existing LangChain agent loop, wrapped in a Nanorix
capsule, with `NanorixCallback` attached to emit per-LLM-call and
per-tool-call ActivityEvents into the in-capsule activity_trail buffer.
The capsule's daemon drains the buffer at destroy and binds the events
into the AuditProof's hash chain.

Pipeline shape:

  task definition (PHI / PII / financial context)
    │
    ▼
  LangChain agent_executor.invoke(..., callbacks=[NanorixCallback(...)])
    │  ┌────────────────────────────────────────────────┐
    │  │ on_chain_start  → record start_ts (no event)   │
    │  │ on_llm_start    → record llm_call_id (no event)│
    │  │ on_llm_end      → ReasoningStepCompleted       │
    │  │ on_tool_start   → ToolCallAttempted            │
    │  │ on_tool_end     → ToolCallCompleted            │
    │  │ on_tool_error   → ToolCallFailed               │
    │  │ on_chain_end    → flush buffer (no event)      │
    │  └────────────────────────────────────────────────┘
    ▼
  agent's structured output → /data/output/agent-output.json
    │
    ▼ (capsule destroy)
  AuditProof emitted with full activity_trail

Run inside a capsule (production) or locally (dev) — when run locally,
set `NANORIX_BUFFER_PATH` env var to a writable file path; the buffer
helper writes the same JSONL shape the daemon ingests in production.

Customer requirements:
- LangChain ≥ 0.3 + langchain-core ≥ 0.3 (declared in capsulefile.yaml)
- Anthropic API key (or any LangChain-supported LLM provider)
- nanorix Python SDK installed with `[langchain]` extra

The bundle is a *pattern reference*. Customer adapts the tool catalog,
LLM provider, and prompts to their domain.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.tools import tool

# This example uses ChatAnthropic; customer can swap for any LangChain
# chat model. The callback handler is provider-agnostic.
try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None  # type: ignore[assignment,misc]

from langgraph.prebuilt import create_react_agent  # type: ignore[import-untyped]

from nanorix.integrations.langchain import NanorixCallback


INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")


# ── Reference tool catalog ─────────────────────────────────────────


@tool
def lab_result_fetch(member_id: str, loinc_codes: List[str]) -> Dict[str, Any]:
    """Fetch the most recent lab results for a member.

    Returns LOINC-coded observations with values, units, and effective
    dates. PRODUCTION: customer replaces with their EHR / data-lake
    integration. STUB: returns illustrative values.
    """
    # Stub response — production integration replaces with actual
    # EHR / data-lake call (the capsule's egress_declaration must
    # whitelist the EHR host). NEVER include real PHI in stubs.
    return {
        "member_id": member_id,
        "results": [
            {"loinc": code, "value": 1.0, "unit": "mg/dL", "date": "2026-04-01"}
            for code in loinc_codes
        ],
    }


@tool
def encounter_history(member_id: str, limit: int = 5) -> Dict[str, Any]:
    """List recent encounters for a member.

    PRODUCTION: customer replaces with EHR integration.
    """
    return {
        "member_id": member_id,
        "encounters": [
            {"date": "2026-04-01", "type": "office_visit", "primary_dx": "ICD-X"}
            for _ in range(limit)
        ],
    }


@tool
def billing_code_lookup(description: str) -> Dict[str, Any]:
    """Look up billing codes by description.

    PRODUCTION: customer replaces with their coding-system integration.
    """
    # Stub: return illustrative codes
    return {
        "description": description,
        "candidates": [
            {"code": "99213", "system": "CPT", "description": description},
        ],
    }


TOOLS = [lab_result_fetch, encounter_history, billing_code_lookup]


# ── Agent loop ─────────────────────────────────────────────────────


def run_agent(task: str, callback: NanorixCallback) -> Dict[str, Any]:
    """Run a LangGraph ReAct agent with the Nanorix callback attached.

    The callback emits ActivityEvents into the capsule's activity_trail
    buffer per LLM call + per tool call. At capsule destroy, the daemon
    drains the buffer and binds the events into the AuditProof.
    """
    if ChatAnthropic is None:
        raise RuntimeError(
            "langchain-anthropic not installed. Add to capsulefile.yaml "
            "packages.python: 'langchain-anthropic>=0.3'."
        )

    llm = ChatAnthropic(model=MODEL)
    agent = create_react_agent(llm, TOOLS)

    config: Dict[str, Any] = {"callbacks": [callback]}
    result = agent.invoke({"messages": [("human", task)]}, config=config)

    # Extract the final assistant message
    messages = result.get("messages", [])
    final = ""
    if messages:
        last = messages[-1]
        final = getattr(last, "content", "") if hasattr(last, "content") else str(last)

    return {
        "task": task,
        "final_answer": final,
        "iteration_count": len(messages),
    }


# ── Pipeline orchestration ─────────────────────────────────────────


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # In-capsule buffer path (production); local path when running
    # outside a capsule for development.
    buffer_path = os.environ.get(
        "NANORIX_BUFFER_PATH", "/data/activity_events.jsonl"
    )

    callback = NanorixCallback(
        framework=os.environ.get("NANORIX_FRAMEWORK", "HIPAA"),
        capsulefile=os.environ.get("NANORIX_CAPSULEFILE", "langchain-agent"),
        framework_version="2026-02",
        buffer_path=buffer_path,
        agent_id="reference-langchain-agent",
    )

    task_files = sorted(INPUT_DIR.glob("*.txt")) + sorted(INPUT_DIR.glob("*.json"))
    if not task_files:
        # Allow running from outside a capsule with a synthetic task
        # for the docs quickstart.
        synthetic = os.environ.get("NANORIX_SYNTHETIC_TASK")
        if synthetic:
            tasks = [synthetic]
        else:
            print("No input task files found in /data/input/uploads")
            return
    else:
        tasks = [f.read_text() for f in task_files]

    runs: list[Dict[str, Any]] = []
    for task in tasks:
        print(f"Running agent on task: {task[:80]}...", file=sys.stderr)
        result = run_agent(task, callback)
        print(
            f"  → terminated with {result['iteration_count']} messages",
            file=sys.stderr,
        )
        runs.append(result)

    output_path = OUTPUT_DIR / "agent-output.json"
    output_path.write_text(json.dumps({"runs": runs}, indent=2))
    print(f"Completed {len(runs)} agent runs → {output_path}")


if __name__ == "__main__":
    main()

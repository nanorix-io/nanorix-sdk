"""Multi-step LangGraph-style agent reference workload.

Pattern: load checkpoint (or start fresh) → research via MCP tool
calls → synthesize findings → write checkpoint for next session →
final report. Composes the memory artifact pattern + MCP-aware
egress.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anthropic
from pydantic import BaseModel

INPUT_DIR = Path("/data/input")
OUTPUT_DIR = Path("/data/output")
MEMORY_PATH = Path("/data/memory/checkpoint.json")  # Memory artifact mount
MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest")
MCP_URI = os.environ.get("MCP_SERVER_URI", "https://mcp.example.com/v1")


class AgentState(BaseModel):
    research_plan: list[str]
    completed_steps: list[str]
    findings: list[dict[str, Any]]
    iteration: int


def load_checkpoint() -> AgentState | None:
    """Load checkpoint from memory artifact mount, if present.

    Memory artifact pattern: customer's KMS-encrypted state blob is
    mounted at /data/memory/ at capsule start IF the capsule was
    created with `parent_cdp_id` referencing a prior capsule. The
    blob never decrypts on Nanorix infrastructure; KMS roundtrip
    happens inside the capsule's volatile memory.
    """
    if not MEMORY_PATH.exists():
        return None
    return AgentState.model_validate_json(MEMORY_PATH.read_text())


def write_checkpoint(state: AgentState) -> None:
    """Persist state to the memory artifact mount.

    On capsule destroy, the file is encrypted using the customer's
    KMS key and stored as the new memory artifact. The next capsule
    the customer creates with `parent_cdp_id` referencing this
    destruction's AuditProof gets the decrypted state at capsule
    start.
    """
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_PATH.write_text(state.model_dump_json(indent=2))


def call_mcp_tool(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over HTTPS+SSE.

    The sealed egress layer checks tool_name against the
    Capsulefile's mcp_policy allowed_tools whitelist. Calls to
    non-whitelisted tools fail with mcp_tool_not_allowed and the
    activity_trail records the policy denial. The egress layer
    pairs each request with its response by JSON-RPC id and emits
    one McpToolInvoked event per call.
    """
    client = anthropic.Anthropic()  # placeholder MCP client
    # In production, this would be an actual MCP HTTP+SSE call.
    # The HTTPS request body would be a JSON-RPC envelope:
    #   {"jsonrpc":"2.0","id":N,"method":"tools/call",
    #    "params":{"name":"<tool>","arguments":<args>}}
    # The sealed daemon parses this body, checks the whitelist,
    # forwards if allowed, and pairs the response by id.
    return {"tool_name": tool_name, "args": args, "result": "stub"}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_checkpoint()
    if state is None:
        topic = (INPUT_DIR / "uploads" / "topic.txt").read_text().strip()
        state = AgentState(
            research_plan=[f"Research aspect {i + 1} of {topic}" for i in range(5)],
            completed_steps=[],
            findings=[],
            iteration=0,
        )
        print(f"Fresh start: {len(state.research_plan)} steps planned")
    else:
        print(
            f"Resumed from checkpoint: iteration={state.iteration}, "
            f"{len(state.completed_steps)}/{len(state.research_plan)} steps done"
        )

    # Each iteration: pick next plan step, call MCP tool, record finding.
    while state.completed_steps != state.research_plan:
        next_step = state.research_plan[len(state.completed_steps)]
        finding = call_mcp_tool(
            "search_documents", {"query": next_step}
        )
        state.findings.append(finding)
        state.completed_steps.append(next_step)
        state.iteration += 1
        print(f"  step {state.iteration}: {next_step}")

    # All steps done → final synthesis
    synthesis = call_mcp_tool(
        "synthesize_findings",
        {"findings": [f for f in state.findings]},
    )
    output_path = OUTPUT_DIR / "report.json"
    output_path.write_text(json.dumps(synthesis, indent=2))
    print(f"Synthesized → {output_path}")

    # Persist final state for verifier audit
    write_checkpoint(state)


if __name__ == "__main__":
    main()

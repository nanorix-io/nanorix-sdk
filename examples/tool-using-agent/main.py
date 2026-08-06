"""Tool-using agent reference workload — production pattern.

Pipeline shape: task definition (PHI context: member id, encounter
ref, etc) → Anthropic tool-use loop with MCP-backed tools →
sealed-daemon-enforced tool whitelist + per-tool-call attestation
→ structured agent output → /data/output/agent-output.json.

Industry signal: this is the production pattern for clinical-
workflow agents (Augmedix's intake/discharge agents; Notable's
provider-side workflow agents; Hippocratic ADK's Polaris stack;
the broader Anthropic-MCP ecosystem maturing late-2024 onward).
The whitelist-at-infrastructure pattern (vs. prompt-level "please
don't call delete_record") is the production discipline for agents
handling regulated data or production write operations.

Each tool call's request body and response body are hashed into
the AuditProof activity_trail. Calls to non-whitelisted tools are
rejected by the sealed daemon BEFORE any HTTPS round-trip leaves
the capsule namespace; the McpToolDenied event records the
attempted tool name + denial reason.

Production-shape primitives (stubbed where remote service is
required):

  1. Anthropic tool-use loop. The agent's prompt declares the tool
     schema (matching the MCP server's published tool catalog).
     Anthropic responds with either a `tool_use` block (name +
     input) or a `text` block (final answer). The loop continues
     until Anthropic emits a final-answer text block or
     MAX_AGENT_ITERATIONS is reached.
  2. MCP tool invocation. The agent forwards the tool-use call to
     the customer's MCP server over HTTPS+SSE. The sealed daemon
     parses the JSON-RPC envelope, checks tool_name against the
     Capsulefile's mcp_policy.allowed_tools whitelist, forwards
     if allowed, hashes request + response, emits McpToolInvoked.
  3. Tool denial recovery. When a tool is blocked, the daemon
     returns an error to the agent with reason code
     mcp_tool_not_allowed; the agent's loop folds the error into
     the next inference step's context, typically reasoning around
     the missing tool or surfacing the gap to the workflow caller.

The PHI context (member ID, encounter ref) lives in volatile
capsule memory only — destroyed with the capsule. Tool-call
arguments and results are hashed (not retained) in the AuditProof.
"""

from __future__ import annotations

import hashlib
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
MCP_URI = os.environ.get("MCP_SERVER_URI", "")
MAX_ITERATIONS = int(os.environ.get("MAX_AGENT_ITERATIONS", "10"))


# -- Tool catalog --------------------------------------------------------
#
# Customer's production tool-use loop derives the schema from the
# MCP server's `tools/list` JSON-RPC method. The shape below is
# illustrative — Anthropic's tool-use schema requires `name`,
# `description`, and `input_schema` (JSON Schema).


TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "lab_result_fetch",
        "description": (
            "Fetch the most recent lab results for a member. "
            "Returns LOINC-coded observations with values, units, "
            "reference ranges, and effective dates."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "member_id": {"type": "string"},
                "loinc_codes": {"type": "array", "items": {"type": "string"}},
                "since_date": {"type": "string", "description": "ISO 8601 date"},
            },
            "required": ["member_id"],
        },
    },
    {
        "name": "encounter_history_query",
        "description": (
            "List recent encounters for a member with date, "
            "encounter type, and primary diagnosis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "member_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["member_id"],
        },
    },
    {
        "name": "billing_code_lookup",
        "description": (
            "Look up billing codes by description. Returns matching "
            "ICD-10 / CPT / HCPCS codes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "code_system": {
                    "type": "string",
                    "enum": ["icd10", "cpt", "hcpcs"],
                },
            },
            "required": ["description"],
        },
    },
    {
        "name": "icd10_translate",
        "description": (
            "Translate a clinical-language description to ICD-10 codes "
            "with confidence scores."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "max_results": {"type": "integer"},
            },
            "required": ["description"],
        },
    },
]


# -- Per-tool-call attestation record -----------------------------------


class ToolCallRecord(BaseModel):
    """Audit record of one tool invocation.

    Hashes are SHA-512 of the canonical-JSON request and response.
    The sealed daemon emits these as McpToolInvoked / McpToolDenied
    ActivityEvents in the AuditProof; this in-process record is
    the local-side mirror for the agent-output JSON.
    """

    iteration: int
    tool_name: str
    outcome: Literal["invoked", "denied"]
    args_hash: str
    result_hash: str | None = None
    denial_reason: str | None = None
    duration_ms: int | None = None
    timestamp: str


# -- MCP client (stub or production HTTPS+SSE) --------------------------


def call_mcp_tool(
    tool_name: str, args: dict[str, Any], iteration: int
) -> tuple[ToolCallRecord, dict[str, Any] | None]:
    """Forward a tool call to the customer's MCP server.

    Production: HTTPS+SSE JSON-RPC `tools/call` to MCP_SERVER_URI.
    The sealed daemon parses the envelope, checks allowed_tools
    whitelist, hashes request + response, and emits the
    McpToolInvoked / McpToolDenied event into AuditProof.

    Returns (record, result_payload_or_None).
    """
    args_canonical = json.dumps(args, sort_keys=True, separators=(",", ":"))
    args_hash = hashlib.sha512(args_canonical.encode()).hexdigest()
    timestamp = datetime.now(timezone.utc).isoformat()

    if not MCP_URI:
        # Stub mode — emit the canonical record shape; real call would
        # propagate over HTTPS+SSE.
        print(f"  [mcp] STUB call: {tool_name}({args})", file=sys.stderr)
        result_payload = {
            "stub": True,
            "tool": tool_name,
            "synthetic_response": (
                f"[MCP_STUB_RESPONSE for {tool_name} — production deployment "
                f"forwards this call to MCP_SERVER_URI]"
            ),
        }
        result_canonical = json.dumps(
            result_payload, sort_keys=True, separators=(",", ":")
        )
        result_hash = hashlib.sha512(result_canonical.encode()).hexdigest()
        return (
            ToolCallRecord(
                iteration=iteration,
                tool_name=tool_name,
                outcome="invoked",
                args_hash=args_hash,
                result_hash=result_hash,
                duration_ms=0,
                timestamp=timestamp,
            ),
            result_payload,
        )

    envelope = {
        "jsonrpc": "2.0",
        "id": iteration,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    started = datetime.now(timezone.utc)
    try:
        with httpx.Client(timeout=60.0) as client:
            response = client.post(f"{MCP_URI}/jsonrpc", json=envelope)
            duration_ms = int(
                (datetime.now(timezone.utc) - started).total_seconds() * 1000
            )
            if response.status_code == 403:
                # Sealed daemon rejected the call (tool not in whitelist
                # OR in prohibited_tools list). Read the structured error.
                err = response.json().get("error", {})
                return (
                    ToolCallRecord(
                        iteration=iteration,
                        tool_name=tool_name,
                        outcome="denied",
                        args_hash=args_hash,
                        denial_reason=err.get("code", "mcp_tool_not_allowed"),
                        duration_ms=duration_ms,
                        timestamp=timestamp,
                    ),
                    None,
                )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result", {})
            result_canonical = json.dumps(
                result, sort_keys=True, separators=(",", ":")
            )
            result_hash = hashlib.sha512(result_canonical.encode()).hexdigest()
            return (
                ToolCallRecord(
                    iteration=iteration,
                    tool_name=tool_name,
                    outcome="invoked",
                    args_hash=args_hash,
                    result_hash=result_hash,
                    duration_ms=duration_ms,
                    timestamp=timestamp,
                ),
                result,
            )
    except httpx.HTTPError as e:
        duration_ms = int(
            (datetime.now(timezone.utc) - started).total_seconds() * 1000
        )
        return (
            ToolCallRecord(
                iteration=iteration,
                tool_name=tool_name,
                outcome="denied",
                args_hash=args_hash,
                denial_reason=f"http_error:{e.__class__.__name__}",
                duration_ms=duration_ms,
                timestamp=timestamp,
            ),
            None,
        )


# -- Agent output schema -------------------------------------------------


class AgentOutput(BaseModel):
    """Top-level agent-run output."""

    task_description: str
    final_answer: str
    iteration_count: int
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    termination_reason: Literal["agent_emitted_final", "iteration_cap_reached"]


# -- Agent loop ----------------------------------------------------------


def run_agent_loop(task_description: str) -> AgentOutput:
    """Run the Anthropic tool-use loop with MCP-backed tools."""
    client = anthropic.Anthropic()
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": task_description}
    ]
    tool_calls: list[ToolCallRecord] = []
    final_answer = ""
    termination_reason: Literal["agent_emitted_final", "iteration_cap_reached"] = (
        "iteration_cap_reached"
    )

    for iteration in range(1, MAX_ITERATIONS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            tools=TOOL_CATALOG,
            messages=messages,
        )

        # Anthropic returns content blocks: text, tool_use, etc.
        # Inspect the stop_reason to decide loop control.
        if response.stop_reason == "end_turn":
            # Agent emitted final text answer. Extract it.
            for block in response.content:
                if block.type == "text":
                    final_answer = block.text
            termination_reason = "agent_emitted_final"
            break

        if response.stop_reason == "tool_use":
            # Forward each tool_use to the MCP server, append the
            # results to messages, loop.
            assistant_blocks = [block.model_dump() for block in response.content]
            messages.append({"role": "assistant", "content": assistant_blocks})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                record, result = call_mcp_tool(
                    block.name, dict(block.input), iteration
                )
                tool_calls.append(record)
                if record.outcome == "invoked" and result is not None:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result),
                        }
                    )
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(
                                {
                                    "error": "mcp_tool_not_allowed",
                                    "reason": record.denial_reason,
                                    "tool_name": block.name,
                                }
                            ),
                            "is_error": True,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Other stop_reasons: surface and exit.
        print(
            f"  [agent] unexpected stop_reason={response.stop_reason}; exiting loop",
            file=sys.stderr,
        )
        break

    return AgentOutput(
        task_description=task_description,
        final_answer=final_answer,
        iteration_count=iteration,
        tool_calls=tool_calls,
        termination_reason=termination_reason,
    )


# -- Pipeline orchestration ----------------------------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task_files = sorted(INPUT_DIR.glob("*.txt")) + sorted(INPUT_DIR.glob("*.json"))
    if not task_files:
        print("No input task files found in /data/input/uploads")
        return

    runs: list[dict[str, Any]] = []
    for f in task_files:
        print(f"Running agent on {f.name}")
        task = f.read_text()
        result = run_agent_loop(task)
        invoked = sum(1 for tc in result.tool_calls if tc.outcome == "invoked")
        denied = sum(1 for tc in result.tool_calls if tc.outcome == "denied")
        print(
            f"  → terminated={result.termination_reason} "
            f"iterations={result.iteration_count} "
            f"tools_invoked={invoked} tools_denied={denied}"
        )
        runs.append(json.loads(result.model_dump_json()))

    output_path = OUTPUT_DIR / "agent-output.json"
    output_path.write_text(
        json.dumps(
            {
                "schema_version": "tool-using-agent-v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "runs": runs,
            },
            indent=2,
        )
    )
    print(f"Completed {len(runs)} agent runs → {output_path}")


if __name__ == "__main__":
    main()

# long-running-agent

Multi-step agent reference pipeline composing the **memory artifact pattern**, **long-running TTL discipline**, and **MCP-aware egress**. Demonstrates the production pattern customer agents (Hippocratic ADK, Sully, Devin-style researchers) use for tasks that exceed the V1 60-minute capsule ceiling.

## Industry signal

Companies running this pattern: **Hippocratic AI** (clinician agent stack), **Sully** (clinical workflow agents), **Notable** (provider-side multi-step workflows), **Devin** (general-purpose engineering agent), and the LangGraph + LangChain ecosystem at large.

## The composition

This bundle is the most complex of the five reference bundles because it composes three pattern primitives:

### Long-running TTL discipline

The capsule declares `default_ttl: 3600` (60 min, V1 capsule ceiling). For workflows beyond 60 minutes, the agent checkpoints state to the memory artifact mount near the TTL boundary and the customer creates a follow-on capsule referencing this destruction's AuditProof via `parent_cdp_id`.

### Memory artifact pattern — encrypted state continuity

The agent's progress (research plan, completed steps, findings, iteration counter) lives in `AgentState` (Pydantic model). At capsule destroy, the agent writes this state to `/data/memory/checkpoint.json`. The runtime encrypts it with the customer's KMS key (customer holds key custody; Nanorix never sees plaintext) and stores it as the destroyed capsule's memory artifact. The next capsule with `parent_cdp_id = <this_capsule_id>` gets the decrypted state at start. State continuity invariant: `session_id + parent_cdp_id` chain proves "this is the same agent run."

The `MemoryArtifactLoaded` and `MemoryArtifactPersisted` ActivityEvent variants record the load + persist operations into AuditProof, with hashes of the encrypted blob (not the plaintext).

### MCP-aware egress — per-tool-call attestation

The agent uses MCP (Model Context Protocol — Anthropic's Nov 2024 spec) to call tools hosted on `mcp.example.com`. The Capsulefile declares `mcp_policy.allowed_tools` (whitelist) and `mcp_policy.prohibited_tools` (blocklist). At runtime:

- The sealed egress layer parses outbound JSON-RPC `tools/call` envelopes and rejects calls whose `tool_name` is not whitelisted, with the `mcp_tool_not_allowed` activity event recorded.
- The egress layer pairs each successful tool call with its response envelope (correlating by JSON-RPC `id`), computes `tool_args_hash` (SHA-512 of canonical-JSON arguments) and `tool_result_hash` (SHA-512 of canonical-JSON result content), and emits one `McpToolInvoked` event per call.

Each tool invocation produces a cryptographic per-tool-call attestation: tuple `(server_uri, tool_name, args_hash, result_hash, duration_ms, outcome)`. Customer's verifier can confirm "agent called tool X with these arguments and got this result, on this date, against this MCP server" without trusting the MCP server's logs.

## What the AuditProof records

For each capsule in a multi-session chain:
- **`MemoryArtifactLoaded`** — when checkpoint exists. Carries `parent_cdp_id`, `key_id`, encrypted blob hash.
- **`McpToolInvoked`** — one per allowed tool call. Tuple as above.
- **`StreamingEgressStarted` / `StreamingEgressChunk` / `StreamingEgressCompleted`** — MCP rides on HTTP+SSE under the hood; the streaming events are emitted underneath the McpToolInvoked semantic events. Verifier can drill from "tool X was called" down to "these specific SSE chunks carried the request + response."
- **`MemoryArtifactPersisted`** — at capsule destroy. Carries the new memory artifact's encrypted blob hash for chaining to the next capsule.

For the multi-session chain as a whole, verifier replay walks the `parent_cdp_id` chain: capsule N's AuditProof carries the hash of capsule N-1's destroyed memory artifact, and so on back to the genesis capsule (where `parent_cdp_id == null`).

## Compliance binding

```python
from nanorix.compliance import HIPAAContext, GDPRContext, SOC2Context

# Long-running clinical agents triple-bind frequently
HIPAAContext().bind(proof)
GDPRContext().bind(proof)
SOC2Context().bind(proof)
```

## Running this bundle

```bash
nanorix build -f capsulefile.yaml
echo "research the patient's medication adherence patterns" > input/topic.txt

# Session 1: fresh start
CAPSULE_1=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US)
nanorix capsules upload $CAPSULE_1 -f input/topic.txt
ANTHROPIC_API_KEY=sk-ant-... nanorix capsules exec $CAPSULE_1 "python main.py"
nanorix capsules destroy $CAPSULE_1
nanorix proofs get $CAPSULE_1 > auditproof-1.json

# Session 2: resume from checkpoint
PARENT_CDP=$(jq -r .capsule_id auditproof-1.json)
CAPSULE_2=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --parent-cdp $PARENT_CDP)
ANTHROPIC_API_KEY=sk-ant-... nanorix capsules exec $CAPSULE_2 "python main.py"
nanorix capsules destroy $CAPSULE_2
nanorix proofs get $CAPSULE_2 > auditproof-2.json

# Verify the chain
nanorix verify auditproof-1.json
nanorix verify auditproof-2.json
nanorix verify --chain auditproof-1.json auditproof-2.json
```

## Caveats

- The MCP tool calls in `main.py` are stubbed. Production deployments use a real MCP client SDK; the sealed egress layer is transport-agnostic — any HTTPS+SSE traffic from inside the capsule to a host in the egress allowlist flows through it. The MCP-aware enforcement (whitelist) + attestation (per-call hash) operate on the wire bytes, not on a specific client library.
- Memory artifact runtime is currently in development; the `/data/memory/` mount path is reserved. In v0.1, the file persists for the lifetime of one capsule but is destroyed with it (no cross-capsule continuity yet).
- Heartbeat + graceful finalize are planned. In v0.1, the capsule runs to completion within the 60-minute TTL or is hard-killed at TTL expiry with a `TimeoutFinalization` event.

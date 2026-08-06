# tool-using-agent

**Production-pattern reference** for tool-using agent workloads inside a Nanorix capsule. Demonstrates the Anthropic tool-use loop pattern with MCP-backed tools and sealed-daemon-enforced tool whitelisting: task definition (with PHI context) → agent reasoning + tool invocation cycle → per-tool-call attestation in AuditProof → structured agent output. Each tool invocation produces a cryptographic record (tuple `(tool_name, args_hash, result_hash, outcome, duration_ms)`) bound into the AuditProof's hash chain.

> **Important: this is a *pattern reference*, not a turnkey clinical-workflow agent or substitute for a SaMD-classified product.** The tool catalog and agent loop are illustrative. Production deployments substitute the customer's MCP server (mTLS-attested per ADR-016), customer's tool catalog (matching the MCP server's published `tools/list`), and customer's regulatory-counsel-determined SaMD classification. Per `feedback_substrate_reframe_pattern.md`, Nanorix is the attestation substrate; the agent product is the customer's.

## Industry signal

Pattern 2 in the [9-pattern atlas](../../docs-site/docs/recipes/tool-using-agent.md) — 33 companies tracked at the substrate-fit horizon. Production tool-using-agent workflows in this space:

- **Augmedix** — clinical workflow agents (intake, encounter setup, discharge tasks)
- **Notable** — provider-side agents (lab-result review, billing-code lookup, scheduling)
- **Hippocratic AI** — Polaris stack agents calling EHR-adjacent tools
- **Anthropic MCP server ecosystem** — broader 2026 agent-builder ecosystem maturing on MCP
- **Internal teams at AI-first SaaS** — building production agents over MCP

These workflows uniformly require: (a) infrastructure-level whitelist enforcement (not "trust the prompt"), (b) per-tool-call audit-grade attestation (which tool, with what arguments, what result, how long), (c) explicit denial trail when the agent attempts blocked operations, (d) zero PHI persistence in tool call args/results outside the capsule.

## The whitelist-at-infrastructure pattern

Why this pattern exists: agents talking to tools that touch regulated data or perform writes are not safe to govern with prompt-level "please don't call delete_record." LLMs follow the prompt mostly; the tail of behavior under adversarial inputs (prompt injection, RAG poisoning, jailbreak attempts) is where compliance + clinical-safety + audit posture matter most. The sealed daemon enforces the whitelist at the network namespace BEFORE any HTTPS round-trip leaves the capsule:

```
Agent reasoning loop
  │
  ├─→ Anthropic API (api.anthropic.com)
  │     │
  │     └─→ tool_use block: name="lab_result_fetch", input={...}
  │
  ▼
Forward to MCP server (mcp.example.com)
  │
  ▼
[Sealed daemon — sealed network namespace exit]
  │
  ├─→ Parse JSON-RPC envelope; extract tool_name
  ├─→ Check tool_name against capsulefile.mcp_policy.allowed_tools
  ├─→ If not in whitelist → REJECT before forwarding; emit
  │     McpToolDenied(tool_name, reason="mcp_tool_not_allowed")
  ├─→ If in prohibited_tools → REJECT; emit McpToolDenied with
  │     prohibited_tool reason code (more explicit than not-in-whitelist)
  └─→ If allowed → forward, hash request + response, emit
        McpToolInvoked(tool_name, args_hash, result_hash, duration_ms)
```

The whitelist is enforced in transport-bytes inspection, not in client-library trust. Calls bypassing the SDK (raw httpx, raw requests, raw urllib) hit the same enforcement.

## Pipeline shape

```
Task definition (PHI context: member_id, encounter_ref, etc)
  │
  ▼
[Agent loop iteration 1..N]
  │  ┌─────────────────────────────────────────┐
  │  │ Anthropic tool-use call                 │
  │  │  → tool_use blocks (name + input)       │
  │  │     OR text block (final answer)        │
  │  └─────────────────────────────────────────┘
  │       │
  │       ▼ (per tool_use block)
  │  ┌─────────────────────────────────────────┐
  │  │ MCP server invocation (HTTPS+SSE)       │
  │  │  → sealed daemon whitelist check        │
  │  │  → invoked OR denied → record           │
  │  └─────────────────────────────────────────┘
  │       │
  │       ▼
  │  Append tool_result to messages; continue loop
  │       │
  │       ▼ stop_reason=end_turn → final_answer reached
  ▼
Structured agent output
```

Loop control: `MAX_AGENT_ITERATIONS` env var sets the per-task iteration cap. Production-typical values: 6-10 iterations for complex multi-tool workflows; 2-4 for single-lookup tasks.

## Reference tool catalog

The bundle ships an illustrative catalog of read-mostly clinical-workflow tools:

| Tool | Purpose | Whitelist class |
|------|---------|-----------------|
| `lab_result_fetch` | Recent lab results by LOINC | allowed |
| `encounter_history_query` | Recent encounters list | allowed |
| `medication_list_fetch` | Active medication list | allowed |
| `allergy_list_fetch` | Documented allergies | allowed |
| `billing_code_lookup` | ICD-10 / CPT / HCPCS lookup | allowed |
| `icd10_translate` | Description → ICD-10 codes | allowed |
| `cpt_lookup` | CPT code lookup | allowed |
| `scheduling_window_query` | Provider scheduling availability (read) | allowed |
| `provider_directory_lookup` | Provider directory query | allowed |
| `schedule_appointment_write` | Write schedule slot | **prohibited** |
| `send_secure_message` | Send patient message | **prohibited** |
| `delete_record` | Delete record | **prohibited** |
| `admin_console_access` | Admin UI access | **prohibited** |
| `impersonate_user` | User impersonation | **prohibited** |
| `export_member_directory` | Bulk member export | **prohibited** |

Production deployments replace this catalog with the customer's MCP server's published `tools/list` schema. The whitelist + blocklist are customer-authored per workflow + clinical-safety review.

## Multi-runtime composition (Python + Node)

The Capsulefile reserves a `packages.node` slot (commented in the bundle) for customers whose MCP tool stubs are TypeScript/Node.js — the Anthropic-published MCP server templates predominantly ship Node first. Same capsule, multi-runtime workload:

```yaml
packages:
  python:
    - anthropic==0.39.0
  node:
    - "@modelcontextprotocol/sdk@^0.5.0"
```

The runtime declaration `runtime: python3.12` selects the entrypoint runtime; auxiliary runtime packages stay available via PATH. Customer's `main.py` orchestrates the agent loop; the customer's tool stubs (Node) listen on a local socket the daemon mediates outbound. See [`docs-site/docs/recipes/tool-using-agent.md`](../../docs-site/docs/recipes/tool-using-agent.md) for the recipe-level walkthrough.

## Why a Nanorix capsule

Tool-using agents handling PHI invoke external tools whose arguments and results carry PHI (member ID, encounter ref, lab values returned). Without sealed isolation, the calling host (your AI service, your CDS gateway, your dev laptop) sees plaintext tool args and results — extending HIPAA scope. With Nanorix:

- The task description (with PHI member-ID) lives in volatile memory only.
- Each agent reasoning HTTPS call to `api.anthropic.com` flows through sealed egress.
- Each MCP tool call to `mcp.example.com` flows through sealed egress with whitelist enforcement.
- Tool args and results are hashed (SHA-512) into the AuditProof; raw plaintext is not retained in AuditProof.
- Whitelist denials are recorded as explicit `McpToolDenied` events (transparent denial trail).
- AuditProof is signed Ed25519; verifiers can confirm "agent invoked exactly these tools and was denied exactly these other tools."

## What the AuditProof records

Activity-trail events for one task processed through a 3-iteration tool-using loop:

1. **`FileUploaded`** — task definition. Carries `filename` + `size_bytes` + SHA-512.
2. **`SealedHttpRequest`** (one per Anthropic call, 3 events) — `host: api.anthropic.com`, `body_hash` (SHA-512 of conversation messages + tools schema), `response_body_hash` (SHA-512 of LLM response), TLS metadata.
3. **`McpToolInvoked`** (one per allowed tool call) — `tool_name`, `args_hash` (SHA-512 of canonical-JSON args), `result_hash` (SHA-512 of canonical-JSON result), `duration_ms`, `outcome: invoked`.
4. **`McpToolDenied`** (one per blocked tool call) — `tool_name`, `args_hash`, `denial_reason: mcp_tool_not_allowed | prohibited_tool | host_not_allowlisted`, `outcome: denied`.
5. **`StreamingEgressStarted` / `StreamingEgressChunk` / `StreamingEgressCompleted`** — MCP rides on HTTP+SSE; streaming events are emitted underneath the McpToolInvoked semantic events. Verifiers can drill from "tool X was called" down to the SSE chunks carrying the request + response.
6. **`ExecutionCompleted`** — exit + duration.
7. **`FileDownloaded`** — `agent-output.json` output bytes hashed.

When customer enables BYO-HSM signing, each `McpToolInvoked` + `McpToolDenied` event also produces a `per_event_attestations` entry signed by the customer's HSM — enabling per-tool-call non-repudiation under the customer's signing authority (per ADR-031). For workflows where the agent's tool-invocation discipline IS the audit story (e.g., agent denied in real-time from invoking a write operation against a covered entity's record), per-event customer attestation provides courtroom-grade evidence beyond the substrate's signature.

## Compliance binding

```python
from nanorix import Client
from nanorix.compliance import HIPAAContext, SOC2Context, AIActContext, NISTAIRMFContext

client = Client()
proof = client.proofs.get(capsule_id)

HIPAAContext().bind(proof)        # related_to §164.312(a-e)
SOC2Context().bind(proof)         # SOC 2 CC6.1 logical access
AIActContext().bind(proof)        # EU AI Act Article 13 transparency
NISTAIRMFContext().bind(proof)    # NIST AI RMF MAP-1.1
```

The `regulatory_context` block enumerates provisions this AuditProof's activity trail is `related_to`:

- **HIPAA Security Rule §164.312(a-e)** (framework_version: 2026-02) — tool-call activity-trail supports access control + audit controls + integrity + transmission security
- **SOC 2 CC6.1** (framework_version: 2026-02) — logical access controls; the tool whitelist is the access-control mechanism, the activity trail is the access-log evidence
- **EU AI Act Article 13** (framework_version: 2026-02) — transparency for high-risk AI systems; McpToolInvoked / McpToolDenied trail supports the technical-documentation requirement
- **NIST AI RMF MAP-1.1** (framework_version: 2026-02) — explicit tool whitelist is deployment-context evidence
- **ISO 42001 AI management** (framework_version: 2026-02) — per-capsule policy + activity trail supports management-system evidence base

Nanorix uses `related_to` language only — never asserts compliance status. Customer's compliance officer makes the final determination. For SaMD classification, customer's regulatory counsel determines FDA Class.

> **Disclaimer:** Nanorix is an evidence vendor (role 1 of three-role liability split per ADR-017). Customer remains responsible for their organizational GRC program. The tool catalog and agent loop are illustrative reference implementations — production composes customer's MCP server, customer's tool catalog (per MCP server's published `tools/list`), customer's clinical-safety + regulatory-counsel sign-off on the whitelist, and customer's SaMD determination.

## Common gotchas (top 3)

1. **Tool denied because the name isn't in `allowed_tools`.** The agent's reasoning loop loops back to Anthropic with the error context; the workflow stalls or surfaces the gap. Fix: either add the tool to the whitelist (re-finalize Capsulefile + re-create capsule) or update the agent's system prompt to acknowledge the tool isn't available. The `McpToolDenied` events surface the blocked tool names.

2. **MCP server unreachable because its host isn't in `egress_declaration`.** The `mcp_policy.allowed_tools` whitelist only governs tool names; the host itself must also be in the capsulefile's `egress_declaration`. Both gates apply. Symptom: `egress_host_not_allowlisted` denial event.

3. **Whitelist + blocklist precedence confusion.** When `allowed_tools` is set, ONLY those tools are allowed (regardless of `prohibited_tools`). The blocklist is a redundant safety net for clarity, not the primary control. For "allow most, block a few" semantics, set `allowed_tools: []` to mean allow-all — though this is discouraged for regulated workloads where positive whitelisting is the audit-friendly posture.

## Running this bundle

```bash
nanorix build -f capsulefile.yaml
# → cf_<uuid>

# Drop a representative task into input/
# (no real PHI — synthetic / consented research data only)
echo "$(cat <<'EOF'
TASK: For member m-sample-001, fetch their most recent labs
(creatinine, eGFR, HbA1c) and most recent encounter. Identify
whether their CKD staging has changed in the last 12 months
and what billing codes the encounter record supports.
EOF
)" > input/task-001.txt

# Create capsule + execute
CAPSULE=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification PHI)
nanorix capsules upload $CAPSULE -f input/task-001.txt
ANTHROPIC_API_KEY=sk-ant-... \
  nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f agent-output.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

## Cross-links

- Recipe: [`docs-site/docs/recipes/tool-using-agent.md`](../../docs-site/docs/recipes/tool-using-agent.md) — recipe-level walkthrough
- CLI reference: [`docs-site/docs/cli-reference.md`](../../docs-site/docs/cli-reference.md) — `nanorix build` / `capsules` / `proofs` / `verify`
- Compliance frameworks:
  - [`docs/compliance/hipaa-controls-mapping.md`](../../docs/compliance/hipaa-controls-mapping.md) — §164.312 control walk
  - [`docs/compliance/eu-ai-act.md`](../../docs/compliance/eu-ai-act.md) — Article 13 binding
- Adjacent bundles:
  - [`multi-step-reasoning/`](../multi-step-reasoning/) — Pattern 1; reasoning loops without tool calls
  - [`long-running-agent/`](../long-running-agent/) — Pattern 8; multi-session agent + memory artifact

## What this bundle does NOT cover

- **Stateful multi-session agents** (>60min via memory artifact + parent_cdp_id chain) — see [`long-running-agent/`](../long-running-agent/).
- **Document extraction as a prior pipeline stage** — see [`med-ai-extraction/`](../med-ai-extraction/) (Pattern 3).
- **Production MCP server bodies** — MCP server behavior ships as stub when `MCP_SERVER_URI` is unset; production fills with customer's MCP server.
- **Production tool catalog** — the 9-tool reference catalog is illustrative; customer authors per MCP server `tools/list`.
- **Write-tool composition** — bundle blocks all write operations by default; customer's clinical-safety + regulatory-counsel review required before any write tool enters the whitelist.
- **SaMD classification** — Nanorix attests to data-handling boundary; SaMD class for the agent workflow is customer + regulatory counsel.

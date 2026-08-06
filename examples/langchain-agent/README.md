# langchain-agent

**Tier A (proxy/library) integration reference** — demonstrates how a customer's existing LangChain agent runs inside a Nanorix capsule with one-line callback attachment that emits per-LLM-call + per-tool-call sub-receipts into the AuditProof.

> **This is a *pattern reference*, not a turnkey clinical-workflow agent or substitute for a SaMD-classified product.** The tool catalog is illustrative. Production deployments substitute the customer's tools, LLM provider, and clinical-safety + regulatory-counsel sign-off. Per `feedback_substrate_reframe_pattern.md`, Nanorix is the attestation substrate; the agent product is the customer's.

## Five-minute quickstart

```bash
# Install Nanorix SDK with LangChain extra
pip install 'nanorix[langchain]'

# Build the bundle
nanorix build -f capsulefile.yaml
# → cf_<uuid>

# Drop a task description (no real PHI — synthetic only)
mkdir -p input
cat > input/task-001.txt <<'EOF'
TASK: For member m-sample-001, fetch their most recent labs
(creatinine, eGFR) and recent encounters. Identify candidate
billing codes for the most recent visit.
EOF

# Create capsule + execute
CAPSULE=$(nanorix capsules create \
  --capsulefile cf_<uuid> \
  --jurisdiction US \
  --data-classification PHI)

nanorix capsules upload $CAPSULE -f input/task-001.txt
ANTHROPIC_API_KEY=sk-ant-... \
  nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f agent-output.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

## Three-tier integration model — where this bundle fits

| Tier | Effort | Claim                                                             | This bundle |
|------|--------|-------------------------------------------------------------------|-------------|
| A    | 5 min  | Per-call receipts (sub-receipts per LLM call + per tool call)     | YES (Tier A reference) |
| B    | 30 min | Full workflow destruction proof (decorator)                       | Future bundle (item 3 of ship sequence) |
| C    | 1 hr   | Per-route receipts (FastAPI/Flask middleware)                     | Future bundle (item 4) |

This bundle ships Tier A only. The claim is **partial**: per-LLM-call and per-tool-call sub-receipts are bound into the AuditProof's activity_trail; full workflow destruction proof requires combining with Tier B (decorator) or Tier C (middleware), which provide capsule-lifecycle wrapping. Customer-facing docs at `docs/integrations/langchain.md` make this distinction explicit.

## What the AuditProof records

For one task processed through a 2-iteration agent loop with 3 tool calls:

1. **`FileUploaded`** — task definition. Carries `filename` + `size_bytes` + SHA-512.
2. **`ReasoningStepCompleted`** (one per LLM call) — `prompt_hash`, `response_hash`, `duration_ms`, `model`, `step_idx`. Plaintext prompts / responses stay in capsule memory only.
3. **`ToolCallAttempted` + `ToolCallCompleted`** (paired by `tool_call_id`) — `tool_name`, `tool_args_hash`, `tool_result_hash`, `duration_ms`. Plaintext args / results stay in capsule memory only.
4. **`ToolCallFailed`** (when a tool raises) — `tool_name`, `failure_reason` (free-form narrative), `failure_kind` (closed-enum: `timeout` / `permission_denied` / `invalid_args` / `upstream_failure` / `cancelled`).
5. **`ExecutionCompleted`** — exit + duration.
6. **`FileDownloaded`** — `agent-output.json` output bytes hashed.

All sub-receipts are bound into the AuditProof's `canonical_hash` Forever-Standard. The 4-language verifier (Rust + Go + Python + TypeScript) accepts the AuditProof byte-equivalent.

## Hook → ActivityEvent mapping

| LangChain hook        | ActivityEvent emitted          | Notes                                         |
|-----------------------|--------------------------------|-----------------------------------------------|
| `on_chain_start`      | (none — record start_ts)       | Open chain context                            |
| `on_llm_start`        | (none — record llm_call_id)    | Prepare metadata                              |
| `on_chat_model_start` | (none — record llm_call_id)    | Same as on_llm_start, chat-model variant      |
| `on_llm_end`          | `ReasoningStepCompleted`       | Hash response; record token counts; emit      |
| `on_tool_start`       | `ToolCallAttempted`            | Record tool_name + args_hash; emit            |
| `on_tool_end`         | `ToolCallCompleted`            | Record result_hash + duration; emit           |
| `on_tool_error`       | `ToolCallFailed`               | Record failure_kind + error message hash      |
| `on_chain_end`        | (none — flush buffer)          | Buffer drain handled by daemon at destroy     |
| `on_chain_error`      | (none — capsule incomplete)    | Customer's outer code marks destruction_state |

## Compliance binding

```python
from nanorix import Client
from nanorix.compliance import HipaaContext

client = Client()
ctx = HipaaContext(
    covered_entity="Acme Health",
    minimum_necessary_purpose="Inference for billing-code lookup workflow",
)
capsule = client.capsules.create(
    capsulefile="cf_<uuid>",
    jurisdiction="US",
    data_classification="PHI",
    metadata=ctx.to_metadata(),
)
```

The metadata field binds into the AuditProof's `metadata_hash`. The `regulatory_context` block in the destruction response enumerates `related_to` provisions:

- **HIPAA Security Rule §164.312(a-e)** (framework_version: `2026-02`) — agent reasoning + tool-call activity-trail support access control + audit controls + integrity + transmission security
- **SOC 2 CC6.1** (framework_version: `2026-02`) — logical access controls
- **EU AI Act Article 13** (framework_version: `2026-02`) — transparency for high-risk AI systems
- **NIST AI RMF MAP-1.1** (framework_version: `2026-02`) — deployment-context evidence

Nanorix uses `related_to` language only — never asserts compliance status. Customer's compliance officer makes the final determination.

## Cross-links

- Customer-facing docs: [`docs/integrations/langchain.md`](../../docs/integrations/langchain.md) — install, basic, advanced, troubleshooting
- Quickstart: [`docs/quickstart-langchain.md`](../../docs/quickstart-langchain.md) — 5-minute path
- Adjacent bundles:
  - [`tool-using-agent/`](../tool-using-agent/) — Pattern 2; native MCP-backed tool whitelist
  - [`multi-step-reasoning/`](../multi-step-reasoning/) — Pattern 1; reasoning-only loops
  - [`long-running-agent/`](../long-running-agent/) — Pattern 8; multi-session agent

## What this bundle does NOT cover

- **Full workflow destruction proof** — see Tier B (`@nanorix.workload` decorator) or Tier C (FastAPI/Flask middleware) bundles, future items in the ship sequence.
- **PHI scanning** — Alt B, Nanorix does NOT inspect prompts or responses for PHI patterns. Customer's prompt + tool-args + tool-results stay in the capsule's volatile memory; only SHA-512 hex digests reach the AuditProof.
- **Production tool catalog** — the 3-tool reference catalog is illustrative.
- **SaMD classification** — Nanorix attests to data-handling boundary; SaMD class is customer + regulatory counsel.

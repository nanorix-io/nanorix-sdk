# Multi-step agent showcase

> **Pattern 2/8 (multi-step agent) × Healthcare AI cohort.** LangGraph agent with per-step receipts + chain-walk verification.

## Cohort context

Named cohort customers — Hippocratic AI (ADK), Sully, Tennr, Notable — operate multi-step AI agent workloads where:

- A single user-facing transaction spans multiple LLM calls + tool invocations.
- Each step's evidence matters independently for audit (e.g., "did the agent retrieve from the approved KB before generating advice?").
- Reasoning traces must be auditable without exposing patient content to the agent vendor.
- Memory-artifact composition lets agents exceed 60min ceilings (Pattern 8) by chaining capsules.

## What this showcase demonstrates

1. **Build** a 5-step LangGraph agent (knowledge-base retrieval → reasoning → tool-use → reflection → output).
2. **Emit** a per-step receipt for each LangGraph node execution. Each step's `pattern_tag` identifies the node ("kb_retrieval", "reasoning", "tool_use", "reflection", "output").
3. **Bind** all per-step receipts into the AuditProof Step 8 Merkle root.
4. **Verify** the chain-walk: the auditor can reconstruct the exact sequence of nodes and their input/output hashes from the AuditProof.

## Pattern 8 composition note

For agents that exceed the 60min capsule lifetime (`max_duration_seconds = 3600`), chain multiple capsules via parent-proof binding. Each follow-on capsule references the prior's AuditProof chain hash as a `parent_proof_hash`; verifier walks the chain across capsules.

```python
# Memory-artifact composition for >60min agents.
prior_capsule_audit_proof = ...  # From previous capsule's destroy.
next_capsule = client.capsules.create(
    capsulefile=cf_id,
    parent_proof_hash=prior_capsule_audit_proof["chain"][-1]["chain_hash"],
    metadata={"continuation_of": prior_capsule_audit_proof["capsule_id"]},
)
```

## Files

| File | Purpose |
|---|---|
| `pipeline.py` | LangGraph agent runner |
| `requirements.txt` | Python deps (langgraph + langchain + nanorix) |
| `capsulefile.yaml` | Agent Capsulefile |
| `input/agent_input.json` | Synthetic user query |
| `expected_audit_proof.json` | Anchor |
| `LICENSE` | PROPRIETARY |

## Reproduction recipe

```bash
export NANORIX_API_KEY="nrx_live_..."
pip install -r requirements.txt
nanorix build -f capsulefile.yaml
python pipeline.py --query "synthetic test query" --output audit_proof.json
nanorix verify audit_proof.json
```

## Discipline

- **Per-step narrowness:** each receipt carries `pattern_tag` for the node type. Mapping to canonical control IDs (e.g., "AI risk assessment per NIST AI RMF") happens at adapter time via `nanorix_control_map`.
- **Customer-sole-declarer:** the agent vendor declares `data_classification`; Nanorix does not infer from query content.
- **No verdict language:** the AuditProof captures the chain; the auditor adjudicates fitness.

## License

Proprietary — Nanorix Inc.

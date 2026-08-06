# Nanorix Reference Capsulefile Bundles

Nine reference bundles covering the full pipeline-pattern atlas Nanorix is built for. Each bundle is a complete, copy-paste-ready example: `capsulefile.yaml` + workload code + `README.md` explaining the pattern, the regulated-data flow, and how AuditProof captures it.

| Bundle | Pattern | Industry signal | Key Nanorix feature |
|---|---|---|---|
| [`multi-step-reasoning/`](multi-step-reasoning/) | Pattern 1: Iterative LLM reasoning loop with knowledge-base retrieval (clinical-decision support / drug-interaction / follow-up planning) | Sully, Notable, Hippocratic AI, Tennr | Per-step ReasoningStepCompleted events + KB-retrieval attestation + confidence-threshold termination + optional BYO-HSM per-step signing |
| [`tool-using-agent/`](tool-using-agent/) | Pattern 2: Anthropic tool-use loop with MCP-backed tools + sealed-daemon-enforced tool whitelist | Augmedix, Notable, Hippocratic ADK, Anthropic MCP ecosystem | Whitelist-at-infrastructure (vs prompt-level) + McpToolInvoked / McpToolDenied events + per-tool-call hash binding + multi-runtime (Python + Node) reservation |
| [`med-ai-extraction/`](med-ai-extraction/) | Pattern 3: Clinical document AI: pypdf → OCR → vision-LLM router → FHIR-shaped output | Tennr, Sully, Notable, Augmedix | OCR fallback chain + PHI-redaction stub + FHIR R4 output schema |
| [`high-volume-per-record/`](high-volume-per-record/) | Pattern 4: One-capsule-per-claim X12 EDI 837 adjudication with clearinghouse round-trip | HealthEdge, Olive AI, Edifecs, Availity, Cohere Health | Per-claim AuditProof economics + ClaimAdjudicated events + audit-grain matches dispute-grain + HIPAA-scope minimization per record |
| [`claims-processing/`](claims-processing/) | Pattern 5: Insurance prior-auth review (CMS-0057-F era): X12 278/275 → InterQual/MCG criteria → LLM advisory → FHIR R4 PAS response → appeal-loop case-state | Cohere Health, Notable, Anthem/Elevance, EvolentHealth | X12 EDI parse + InterQual/MCG binding + CMS-0057-F FHIR PAS conformance + per-event customer KMS attestation + appeal-loop case-state |
| [`legal-ediscovery/`](legal-ediscovery/) | Pattern 6: TAR 2.0 / Continuous Active Learning loop (seed-set → train → score → uncertainty band → iterate) | Relativity aiR, Disco, Everlaw, Reveal | CAL iteration + memory artifact state continuity + court-defensibility record |
| [`voice-behavioral/`](voice-behavioral/) | Pattern 7: Diarized clinical voice AI (consent gate → VAD → diarization → per-speaker ASR → safety-trigger escalation) | Eleos Health, Cogito, Lyssn | 42 CFR Part 2 consent gate + pyannote diarization + real-time escalation webhook + FHIR encounter linkage |
| [`long-running-agent/`](long-running-agent/) | Pattern 8: Multi-step LangGraph agent with memory artifact composition for >60min runs | Hippocratic ADK, Sully, Devin-style | Memory artifact + parent_cdp_id chain + MCP-aware egress + heartbeat |
| [`federated-learning/`](federated-learning/) | Pattern 9: Cross-organization federated learning fragment (one capsule per round per participating organization) | Owkin, Rhino Health, Lifebit, NVIDIA FLARE federations, MELLODDY | Multi-party trust composition (each org signs under their HSM) + WeightDeltaEmitted events + DP integration + coordinator-attested global-snapshot binding |

This covers all 9 patterns of the [pipeline atlas](../docs-site/docs/recipes/) for V1.

## How each bundle is structured

```
<bundle-name>/
├── capsulefile.yaml      # Environment definition (uploaded via `nanorix build`)
├── main.py               # Workload entrypoint (or main.ts for Node bundles)
├── input/                # Sample input data (representative shape, no real PHI)
└── README.md             # Pattern explanation + AuditProof field walkthrough
```

## Running a bundle

```bash
# 1. Build + upload the Capsulefile
cd med-ai-extraction
nanorix build -f capsulefile.yaml

# 2. The build command prints the cf_... id; create a capsule from it
nanorix capsules create --capsulefile cf_<id> --jurisdiction US

# 3. Upload your input data + execute
nanorix capsules upload <capsule_id> -f input/sample.pdf
nanorix capsules exec <capsule_id> "python main.py"

# 4. Destroy + retrieve the AuditProof
nanorix capsules destroy <capsule_id>
nanorix proofs get <capsule_id> > auditproof.json

# 5. Verify offline
nanorix verify auditproof.json
```

Each bundle's README explains which AuditProof activity_trail variants its workload produces and what verifiers can do with them.

## Regulated-data discipline

Customer declares `data_classification` in the capsulefile (`"PHI"`, `"PII"`, `"financial"`, `"credentials"`, `"general"`) AND `jurisdiction` at capsule create time (`"US"`, `"EU"`, `"UK"`, `"CA"`, `"AU"`, `"IN"`, `"OTHER"`). Nanorix does NOT verify or override these declarations — they ride into AuditProof as customer-attested context (`capsule_context`). Verifier-side compliance frameworks (HIPAA / GDPR / SOC 2) consume them via `nanorix.compliance` SDK module.

## Trust boundary

Nanorix attests to the capsule boundary: ephemeral isolation, sealed egress, AuditProof (8-step SHA-512 + Ed25519). Customer composes downstream attestations: payer responses, lab results, KMS signing, observability traces inside the capsule. See [`docs.nanorix.io`](https://docs.nanorix.io) for the architecture, threat model, and onboarding playbook.

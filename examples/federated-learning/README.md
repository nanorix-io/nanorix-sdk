# federated-learning

**Production-pattern reference** for cross-organization federated learning inside Nanorix capsules. Demonstrates the per-round per-organization pattern: receive global model snapshot from coordinator → train locally on this organization's PHI-bearing data → optionally apply differential-privacy noise → emit weight delta only (raw data never leaves the capsule). Each participating organization's per-round AuditProof attests to the privacy commitment + the global-snapshot binding + the per-organization signing authority.

> **Important: this is a *pattern reference*, not a production federated-learning framework.** The local training loop, coordinator client, and differential-privacy integration ship as illustrative stubs. Production deployments substitute customer-licensed federated frameworks (NVIDIA FLARE, Flower, PySyft, OpenFL, Owkin Connect, Rhino Health platform), customer's model architecture + dataset loaders, customer's privacy-preservation parameters (DP epsilon/delta tuned to threat model), and customer's regulatory-counsel-determined privacy posture. Per `feedback_substrate_reframe_pattern.md`, Nanorix is the attestation substrate; the federated-learning product is the customer's.

## Industry signal

Pattern 9 in the [9-pattern atlas](../../docs-site/docs/recipes/) — 8 companies tracked at the substrate-fit horizon. Production federated-learning workflows in this space:

- **Owkin** — cross-academic-medical-center federated learning for oncology + rare-disease research
- **Rhino Health** — federated platform for healthcare AI (cross-hospital model training)
- **Lifebit / Lifebit AI** — genomic-data federation (cohort-spanning analyses without data egress)
- **NVIDIA FLARE** — production federated framework underlying many vertical-specific deployments
- **MELLODDY** — pharma-industry federated learning consortium (drug discovery)
- **Federated learning consortia in financial services** — under bank-secrecy laws + cross-jurisdictional constraints

These workflows uniformly require: (a) data-residency commitment per participating organization (PHI / PII / proprietary data NEVER leaves the organization's perimeter), (b) cryptographic non-repudiation per round per participant (load-bearing for IRB approvals, BAA terms, multi-party trust agreements), (c) coordinator-attested global-snapshot binding (each round's training context is the coordinator's published snapshot, not a drifted local copy), (d) per-organization signing authority (no cross-organization key sharing; each organization's HSM signs their own contribution per ADR-031).

## Pipeline shape

```
Federation coordinator                     Participating organization (one per capsule)
  (customer or partner-                    (Nanorix capsule lifecycle)
   operated; outside
   any one capsule)
        │
        │  GET round metadata + snapshot
        │◄──────────────────────────────────[Capsule starts]
        │                                    │
        │                                    ▼
        │                                  Fetch global snapshot
        │  global model weights ─────────► (HTTPS + sealed egress)
        │                                    │
        │                                    ▼
        │                                  Local training loop (PHI in volatile memory only)
        │                                    │
        │                                    ▼
        │                                  Compute weight delta = local_state - global_state
        │                                    │
        │                                    ▼
        │                                  Optional: apply DP noise
        │                                    (clip + Gaussian, opacus PrivacyEngine)
        │                                    │
        │                                    ▼
        │  POST weight delta ◄──────────── Emit weight delta to coordinator
        │                                    (HTTPS + sealed egress)
        │                                    │
        │                                    ▼
        │                                  AuditProof emitted at capsule destroy
        │                                  (signed under organization's authority
        │                                   when BYO-HSM is enabled per ADR-031)
        │
        │  Coordinator aggregates N organizations' deltas per
        │  aggregation strategy (FedAvg, FedProx, SCAFFOLD, etc)
        │  → publishes round N+1 snapshot
        ▼
[Round N+1 begins]
```

The substrate's role is per-organization-per-round attestation. The coordinator's role (and the federation's aggregation-strategy choice) is the customer's domain — Nanorix does not operate the coordinator.

## Multi-party trust composition

Federated learning's trust story is multi-party by construction. Per ADR-031 (BYO-HSM signing model), each participating organization's per-round AuditProof is signed under that organization's HSM-rooted authority — Nanorix sees no organization's private key. Composition shape:

```
[Org A's per-round AuditProof]    Signed by Org A's HSM
[Org B's per-round AuditProof]    Signed by Org B's HSM
[Org C's per-round AuditProof]    Signed by Org C's HSM
              │
              │  Coordinator's round-aggregate AuditProof
              │  references the participants' AuditProofs
              │  by capsule_id; coordinator signs the
              │  aggregate under coordinator's authority
              ▼
[Federation round N AuditProof]   Signed by federation coordinator
```

Auditors can:
- Independently verify each organization's per-round contribution (signature + chain) without trusting the coordinator
- Independently verify the coordinator's aggregation (signature + chain over the referenced participants' AuditProofs) without trusting any individual organization
- Reconstruct the full federation history round-by-round across all participants

This composition is the load-bearing audit grade for federations operating under multi-party regulatory frameworks (cross-academic IRB; pharma consortium ABOM; multi-payer arrangements; etc).

## Privacy-preservation primitives

The reference loop integrates with three privacy-preservation patterns customers commonly compose:

1. **Differential privacy (DP)** — `ENABLE_DIFFERENTIAL_PRIVACY=true` enables opacus PrivacyEngine wrapping. Customer authors `DP_EPSILON / DP_DELTA / DP_MAX_GRAD_NORM` per their threat model. The capsulefile's `enable_differential_privacy` flag binds into the AuditProof's training_metrics — auditors can confirm "DP was applied to this round's contribution with epsilon=X delta=Y." Production-grade epsilon budgets are workflow-dependent: rare-disease cohorts often run epsilon ≤ 1.0 per round with strict cumulative budgets; broader population-level studies may allow epsilon up to 8.0 per round.

2. **Secure aggregation** — when the coordinator implements secure aggregation (the coordinator only sees the SUM of N participants' deltas, never individual deltas), the per-organization weight-delta emission is a partial share. Substrate enforcement is identical (one capsule per organization per round, weight delta only egresses); the secure-aggregation cryptography is the federation framework's responsibility (NVIDIA FLARE, OpenFL, Flower, etc all implement this).

3. **Homomorphic encryption (HE)** — when the coordinator aggregates over encrypted deltas (FHE / SHE schemes via tenseal / Microsoft SEAL / IBM HElib), the per-organization weight delta is encrypted before egress. Substrate role unchanged; HE cryptography is the federation framework's choice.

Production deployments compose ONE OR MORE of these primitives per the federation's privacy-posture requirements + threat model + computational budget. The reference shape demonstrates DP integration; the secure-aggregation + HE primitives are wired through the federation framework the customer adopts.

## Why a Nanorix capsule

Federated learning's privacy posture is only as strong as the per-organization data-handling discipline. Without sealed isolation:

- The training host (your data-science workstation, your hospital's compute cluster, your CRO's training pipeline) sees the local PHI data — extending the federation's HIPAA / GDPR / data-residency scope to that host.
- The training host's logs, observability traces, debugger snapshots, swap files can leak training data.
- Cross-organization trust is asymmetric: a federation participant who can't show audit-grade evidence of "data never left my perimeter" is a trust-breaking participant for the federation.

With Nanorix:

- The local training data lives in volatile memory only, destroyed with the capsule. Per `feedback_strategic_positioning.md` zero-retention is the substrate property.
- Each coordinator HTTPS call (snapshot fetch, weight-delta emission) flows through sealed egress: TLS pin verification + per-request hash + duration recorded.
- The training metrics (epochs, samples seen, final loss, DP epsilon consumed) are bound into the AuditProof.
- The weight delta is hashed (SHA-512) before emission; the coordinator's response is hashed.
- AuditProof is signed Ed25519; when BYO-HSM is enabled, signed under the participating organization's HSM authority.

The federation gains: a per-participant cryptographic guarantee that the participant did exactly what they committed to do, on exactly the snapshot they committed to use, with exactly the privacy parameters they declared. No verbal trust required.

## What the AuditProof records

Activity-trail events for one round per organization:

1. **`SealedHttpRequest`** (snapshot metadata fetch) — `host: coordinator.federation.example.com`, `body_hash` (request), `response_body_hash` (round metadata).
2. **`SealedHttpRequest`** (snapshot bytes fetch) — `host: coordinator...`, `response_body_hash` (= `snapshot_hash` in metadata, verifiable cross-reference).
3. **`FederatedRoundStarted`** — `round_id`, `snapshot_id_in`, `aggregation_strategy`, `cohort_size`. Round entry binding into AuditProof's hash chain.
4. **`LocalTrainingCompleted`** — `epochs`, `samples_seen`, `final_loss`, `gradient_norm`, `dp_applied`, `dp_epsilon_consumed`, `training_duration_seconds`. Local training observability bound (without data leakage — only summary metrics).
5. **`SealedHttpRequest`** (weight delta emission) — `host: coordinator...`, `body_hash` (= `delta_hash`), `response_body_hash`.
6. **`WeightDeltaEmitted`** — `round_id`, `delta_hash`, `delta_size_bytes`, `coordinator_response_status: accepted | rejected | deferred`. Delta-emission record bound.
7. **`ExecutionCompleted`** — exit + duration.
8. **`FileDownloaded`** — `round-outcome.json` output bytes hashed.

When customer enables BYO-HSM signing (per `signing_config` block — STRONGLY RECOMMENDED for federated learning), each `WeightDeltaEmitted` event also produces a `per_event_attestations` entry signed by the customer organization's HSM. Round contribution is non-repudiable under the organization's signing authority.

## Compliance binding

```python
from nanorix import Client
from nanorix.compliance import HIPAAContext, GDPRContext, EHDSContext, AIActContext, NISTAIRMFContext

client = Client()
proof = client.proofs.get(capsule_id)

HIPAAContext().bind(proof)        # related_to §164.312 + §164.502
GDPRContext().bind(proof)         # GDPR Article 25 (privacy by design)
EHDSContext().bind(proof)         # EHDS Article 73 (secondary use, federated)
AIActContext().bind(proof)        # EU AI Act Article 13 + Article 10
NISTAIRMFContext().bind(proof)    # NIST AI RMF MAP + MEASURE
```

The `regulatory_context` block enumerates provisions this AuditProof's activity trail is `related_to`:

- **HIPAA Security Rule §164.312(a-e)** (framework_version: 2026-02) — access control, audit controls, integrity, transmission security
- **HIPAA Privacy Rule §164.502** (framework_version: 2026-02) — minimum-necessary disclosure; data-residency commitment supports the de-identification + minimum-disclosure posture
- **GDPR Article 25** (framework_version: 2026-02) — privacy by design + by default; weight-delta-only egress + DP integration support the design posture
- **GDPR Article 89** (framework_version: 2026-02) — research-purpose processing safeguards; federation pattern supports the safeguards
- **EHDS Article 73** (framework_version: 2026-02) — secondary use of health data; federation pattern is one of the EHDS-permitted modalities
- **EU AI Act Article 10** (framework_version: 2026-02) — data + data governance for high-risk AI; federation training pattern supports the data-governance discipline
- **EU AI Act Article 13** (framework_version: 2026-02) — transparency for high-risk AI; per-round AuditProof supports the technical-documentation requirement
- **NIST AI RMF MAP-1.1 + MEASURE-2.13** (framework_version: 2026-02) — deployment context + traceability
- **ISO 27701** (framework_version: 2026-02) — privacy information management; federation pattern supports PIM evidence

Nanorix uses `related_to` language only — never asserts compliance status. Customer's compliance officer makes the final determination. For federated workflows under SaMD classification (e.g., a federated-trained clinical model deployed as SaMD), customer's regulatory counsel determines FDA Class.

> **Disclaimer:** Nanorix is an evidence vendor (role 1 of three-role liability split per ADR-017). Customer remains responsible for their organizational GRC program. The local training loop, coordinator client, DP integration, and federation aggregation logic are illustrative reference implementations — production composes customer's licensed federated framework + customer's model architecture + customer's privacy parameters + customer's federation operator agreement + customer's regulatory-counsel-determined privacy posture + customer's IRB / ABOM / consortium-specific approvals.

## Running this bundle

```bash
nanorix build -f capsulefile.yaml
# → cf_<uuid>

# This organization's local data lives in input/ (volatile memory
# only inside the capsule; never persisted). Format is workflow-
# specific — typical: torch.utils.data.Dataset-loadable directory
# structure (images / tensors / records).
# (synthetic / consented research data only — no real PHI)
mkdir -p input/training_data
# Customer adds their organization's training data here

# Create capsule + execute (one capsule per round per organization)
CAPSULE=$(nanorix capsules create --capsulefile cf_<uuid> --jurisdiction US --data-classification PHI)
nanorix capsules upload $CAPSULE -f input/training_data
COORDINATOR_URL=https://coordinator.federation.example.com/v1 \
  FEDERATION_ROUND_ID=fed_round_2026_05_q2_001 \
  ENABLE_DIFFERENTIAL_PRIVACY=true \
  DP_EPSILON=4.0 \
  nanorix capsules exec $CAPSULE "python main.py"
nanorix capsules download $CAPSULE -f round-outcome.json
nanorix capsules destroy $CAPSULE
nanorix proofs get $CAPSULE | tee auditproof.json
nanorix verify auditproof.json
```

Multi-organization orchestration (federation operator's job, not Nanorix's):

```python
# Pseudo-code: federation coordinator issues round N to N
# participants; each participant runs the capsule lifecycle above
# in their own Nanorix tenant; coordinator aggregates the deltas.
# Each participant's AuditProof is independently verifiable.

# Round N kickoff
for org in federation_participants:
    notify(org, round_id="fed_round_2026_05_q2_001")

# Each participant runs the capsule lifecycle independently;
# coordinator collects N AuditProofs + N weight deltas.

# Round N+1 kickoff with aggregated snapshot
new_snapshot = aggregate(participant_deltas, strategy="fedavg")
publish_round(round_id="fed_round_2026_05_q2_002", snapshot=new_snapshot,
              referenced_round_auditproofs=[p.auditproof_id for p in participant_responses])
```

## Cross-links

- CLI reference: [`docs-site/docs/cli-reference.md`](../../docs-site/docs/cli-reference.md) — `nanorix build` / `capsules` / `proofs` / `verify`
- Compliance frameworks:
  - [`docs/compliance/hipaa-controls-mapping.md`](../../docs/compliance/hipaa-controls-mapping.md) — §164.312 + §164.502 mappings
  - [`docs/compliance/gdpr-controls.md`](../../docs/compliance/gdpr-controls.md) — Article 25 + Article 89 mappings
  - [`docs/compliance/ehds-article-73.md`](../../docs/compliance/ehds-article-73.md) — secondary-use safeguards
  - [`docs/compliance/eu-ai-act.md`](../../docs/compliance/eu-ai-act.md) — Article 10 + Article 13 mappings
- Adjacent bundles:
  - [`long-running-agent/`](../long-running-agent/) — Pattern 8; multi-session memory artifact pattern (composes for federations spanning >60min training rounds via parent_cdp_id chain)
  - [`multi-step-reasoning/`](../multi-step-reasoning/) — Pattern 1; reasoning workflows that consume federated-trained models for inference

## What this bundle does NOT cover

- **Federation coordinator** — Nanorix does not operate the coordinator; customer composes their preferred framework (NVIDIA FLARE / Flower / PySyft / OpenFL / Owkin Connect / Rhino Health platform). Coordinator is OUT OF SCOPE for the substrate.
- **Aggregation strategies** — FedAvg / FedProx / SCAFFOLD / FedNova / etc are framework concerns; Nanorix's role is per-participant attestation regardless of aggregation strategy.
- **Production differential-privacy parameters** — DP_EPSILON / DP_DELTA / DP_MAX_GRAD_NORM in the capsulefile are illustrative defaults. Customer authors per threat model + cumulative privacy budget + workflow class.
- **Production model architecture** — local training loop ships skeleton with PyTorch primitives; customer fills model.nn.Module + dataset loader + loss function + optimizer.
- **Production privacy posture** — secure aggregation + homomorphic encryption are framework-level concerns; substrate composes with these via the framework's published API (out-of-scope for the substrate's enforcement).
- **IRB / ABOM / consortium approvals** — federated workflows under research IRB or pharma ABOM oversight require customer-side approval cycles before any participant onboards.
- **Cross-jurisdictional federation** — when participants span jurisdictions (US + EU + UK + etc), data-residency commitment per jurisdiction (per ADR-018 region binding) is the participant's compliance counsel's domain.

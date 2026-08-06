"""Federated learning fragment reference workload — production pattern.

Pipeline shape (one round per participating organization per capsule):
  1. Receive global model snapshot from coordinator
  2. Train locally on this organization's PHI-bearing data
     (data NEVER leaves the capsule; only weight deltas leave)
  3. Optionally apply differential-privacy noise to weight deltas
  4. Return weight delta to coordinator
  5. Capsule destroyed → AuditProof emitted attesting to local
     processing + weight-delta-only egress

Industry signal: this is the production pattern for cross-
organization federated learning in regulated verticals (Owkin
across academic medical centers; Rhino Health for healthcare-AI
federations; Lifebit / Lifebit AI for genomic federations; NVIDIA
FLARE-based federations across hospital systems; financial-services
federations under bank-secrecy laws). Each participating
organization's AuditProof carries:

  - the global model snapshot used for the round (binds drift
    detection + reproducibility)
  - confirmation that local data was processed in volatile memory
    only (binds data-residency commitment)
  - confirmation that ONLY weight updates egressed (binds
    privacy commitment)
  - per-round signature under the participating organization's
    signing authority (BYO-HSM) — the federation
    coordinator's round-aggregate AuditProof composes from N
    participating organizations' per-round AuditProofs

Multi-party trust composition: each participant signs under their
HSM (no Nanorix-managed keys, no cross-organization key sharing);
the federation coordinator's AuditProof references the participants'
AuditProofs by capsule_id; auditors can independently verify
each participant's contribution.

Production-shape primitives (stubbed where remote service or
training data is required):

  1. Global snapshot retrieval. HTTPS GET against COORDINATOR_URL
     to fetch the round-N global model weights + the federation
     manifest (round_id, participating_orgs, aggregation_strategy).
     Each retrieval is one SealedHttpRequest in AuditProof.
  2. Local training loop. PyTorch primitives over local data
     (volatile capsule memory only). Reference shape iterates
     LOCAL_EPOCHS over the local dataset with LOCAL_BATCH_SIZE +
     LOCAL_LEARNING_RATE. Customer's production deployment fills
     the model architecture + dataset loader + loss function.
  3. Differential-privacy noise application. When enabled, the
     opacus PrivacyEngine wraps the optimizer; weight deltas are
     clipped + Gaussian-noised before egress. DP_EPSILON / DP_DELTA
     / DP_MAX_GRAD_NORM are customer-tuned per threat model.
  4. Weight-delta emission. HTTPS POST against COORDINATOR_URL
     with the (encrypted, optionally noised) weight delta.
     Customer's coordinator integrates the delta per the
     federation's aggregation strategy (FedAvg, FedProx, SCAFFOLD,
     etc).

The local training data lives in volatile capsule memory only —
destroyed with the capsule. The coordinator HTTPS calls' request +
response bytes are hashed (not retained) in the AuditProof.
The sealed daemon attests structural metadata only;
no payload retention.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

# Note: torch is imported in the local training loop only; this
# allows the bundle to demonstrate the federation lifecycle even
# in stub mode without a torch install.
try:
    import torch  # noqa: F401  # imported by reference; used in local_train_loop

    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

INPUT_DIR = Path("/data/input/uploads")
OUTPUT_DIR = Path("/data/output")
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "")
ROUND_ID = os.environ.get("FEDERATION_ROUND_ID", "fed_round_stub_2026")
LOCAL_EPOCHS = int(os.environ.get("LOCAL_EPOCHS", "1"))
LOCAL_BATCH_SIZE = int(os.environ.get("LOCAL_BATCH_SIZE", "32"))
LOCAL_LR = float(os.environ.get("LOCAL_LEARNING_RATE", "0.001"))
ENABLE_DP = os.environ.get("ENABLE_DIFFERENTIAL_PRIVACY", "false").lower() == "true"
DP_EPSILON = float(os.environ.get("DP_EPSILON", "8.0"))
DP_DELTA = float(os.environ.get("DP_DELTA", "1e-5"))
DP_MAX_GRAD_NORM = float(os.environ.get("DP_MAX_GRAD_NORM", "1.0"))


# -- Round artifact schemas ---------------------------------------------


class GlobalSnapshotMetadata(BaseModel):
    """Coordinator-published metadata for the round's starting point."""

    round_id: str
    snapshot_id: str  # coordinator-assigned snapshot identifier
    snapshot_hash: str  # SHA-512 of the canonical snapshot bytes
    aggregation_strategy: str  # "fedavg" | "fedprox" | "scaffold" | etc
    participating_organizations: list[str] = Field(default_factory=list)
    cohort_size: int = 0
    coordinator_signature_id: str | None = None  # coordinator's attestation key id


class LocalTrainingMetrics(BaseModel):
    """Local training observability — what was learned this round."""

    round_id: str
    snapshot_id_in: str  # global snapshot id at round start
    epochs: int
    samples_seen: int
    final_loss: float
    gradient_norm: float
    differential_privacy_applied: bool
    dp_epsilon_consumed: float | None = None  # cumulative DP epsilon
    training_duration_seconds: float


class WeightDeltaEmission(BaseModel):
    """The artifact that egresses to the coordinator."""

    round_id: str
    org_authority_id: str | None  # set when BYO-HSM is enabled
    snapshot_id_in: str
    delta_hash: str  # SHA-512 of the canonical delta bytes
    delta_size_bytes: int
    differential_privacy_applied: bool
    coordinator_response_status: Literal["accepted", "rejected", "deferred"]
    coordinator_response_hash: str | None = None
    emission_timestamp: str


class RoundOutcome(BaseModel):
    """Top-level round-completion record."""

    round_id: str
    snapshot_metadata: GlobalSnapshotMetadata
    training_metrics: LocalTrainingMetrics
    weight_delta_emission: WeightDeltaEmission
    federation_authority_context: str = (
        "Each participating organization's AuditProof carries their "
        "organization's signature. The federation coordinator's round-"
        "aggregate AuditProof composes from N participating "
        "organizations' per-round AuditProofs (multi-party "
        "signing-authority model). Auditors can independently verify "
        "each participant's contribution to the federated round."
    )


# -- Coordinator client (stub or production HTTPS) ---------------------


def fetch_global_snapshot() -> tuple[GlobalSnapshotMetadata, bytes]:
    """Fetch the current round's global model snapshot from coordinator.

    Production: HTTPS GET against COORDINATOR_URL/rounds/<round_id>/snapshot.
    The response is the canonical model-weight bytes (model-format
    dependent; commonly safetensors or PyTorch state_dict
    serialized canonically).

    Returns (metadata, raw_snapshot_bytes).
    """
    if not COORDINATOR_URL:
        # Stub mode — synthesize a minimal global-snapshot envelope
        # so the round lifecycle is exercisable without a live
        # coordinator.
        print("  [coordinator] STUB snapshot retrieval", file=sys.stderr)
        synthetic_bytes = (
            b"[STUB_GLOBAL_SNAPSHOT_BYTES - production deployment fetches "
            b"the canonical model state from COORDINATOR_URL]"
        )
        snapshot_hash = hashlib.sha512(synthetic_bytes).hexdigest()
        return (
            GlobalSnapshotMetadata(
                round_id=ROUND_ID,
                snapshot_id="stub-snapshot-001",
                snapshot_hash=snapshot_hash,
                aggregation_strategy="fedavg",
                participating_organizations=["org-stub-001", "org-stub-002", "org-stub-003"],
                cohort_size=3,
                coordinator_signature_id="stub-coordinator-key-2026-q2",
            ),
            synthetic_bytes,
        )

    try:
        with httpx.Client(timeout=300.0) as client:
            meta_response = client.get(
                f"{COORDINATOR_URL}/rounds/{ROUND_ID}/metadata"
            )
            meta_response.raise_for_status()
            meta_payload = meta_response.json()

            snapshot_response = client.get(
                f"{COORDINATOR_URL}/rounds/{ROUND_ID}/snapshot"
            )
            snapshot_response.raise_for_status()
            raw_bytes = snapshot_response.content
            computed_hash = hashlib.sha512(raw_bytes).hexdigest()

            if computed_hash != meta_payload.get("snapshot_hash"):
                print(
                    "  [coordinator] WARNING: snapshot hash mismatch — possible "
                    "transmission error or tampering; aborting round",
                    file=sys.stderr,
                )
                raise ValueError("snapshot_hash_mismatch")

            return (
                GlobalSnapshotMetadata(**meta_payload),
                raw_bytes,
            )
    except (httpx.HTTPError, ValueError) as e:
        print(f"  [coordinator] snapshot fetch failed: {e}", file=sys.stderr)
        raise


def emit_weight_delta(
    round_id: str,
    snapshot_id_in: str,
    delta_bytes: bytes,
    dp_applied: bool,
    org_authority_id: str | None,
) -> WeightDeltaEmission:
    """Emit weight delta to coordinator.

    Production: HTTPS POST against COORDINATOR_URL/rounds/<round_id>/contributions.
    The delta is the canonical weight-delta bytes (e.g., safetensors
    serialization of `local_state_dict - global_state_dict`).

    The coordinator integrates the delta per the federation's
    aggregation strategy.
    """
    delta_hash = hashlib.sha512(delta_bytes).hexdigest()
    timestamp = datetime.now(timezone.utc).isoformat()

    if not COORDINATOR_URL:
        # Stub mode — synthesize an accepted response.
        print(
            f"  [coordinator] STUB delta emission: round={round_id} "
            f"size={len(delta_bytes)}B dp={dp_applied}",
            file=sys.stderr,
        )
        synthetic_response = {
            "stub": True,
            "round_id": round_id,
            "status": "accepted",
            "aggregation_position": 1,
            "cohort_size": 3,
        }
        canonical_response = json.dumps(
            synthetic_response, sort_keys=True, separators=(",", ":")
        )
        return WeightDeltaEmission(
            round_id=round_id,
            org_authority_id=org_authority_id,
            snapshot_id_in=snapshot_id_in,
            delta_hash=delta_hash,
            delta_size_bytes=len(delta_bytes),
            differential_privacy_applied=dp_applied,
            coordinator_response_status="accepted",
            coordinator_response_hash=hashlib.sha512(
                canonical_response.encode()
            ).hexdigest(),
            emission_timestamp=timestamp,
        )

    try:
        with httpx.Client(timeout=300.0) as client:
            response = client.post(
                f"{COORDINATOR_URL}/rounds/{round_id}/contributions",
                content=delta_bytes,
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Snapshot-Id-In": snapshot_id_in,
                    "X-DP-Applied": str(dp_applied).lower(),
                    "X-Org-Authority-Id": org_authority_id or "anonymous",
                },
            )
            response.raise_for_status()
            response_payload = response.json()
            response_hash = hashlib.sha512(
                json.dumps(
                    response_payload, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            status_str = response_payload.get("status", "deferred")
            if status_str not in ("accepted", "rejected", "deferred"):
                status_str = "deferred"
            return WeightDeltaEmission(
                round_id=round_id,
                org_authority_id=org_authority_id,
                snapshot_id_in=snapshot_id_in,
                delta_hash=delta_hash,
                delta_size_bytes=len(delta_bytes),
                differential_privacy_applied=dp_applied,
                coordinator_response_status=status_str,
                coordinator_response_hash=response_hash,
                emission_timestamp=timestamp,
            )
    except httpx.HTTPError as e:
        print(f"  [coordinator] delta emission failed: {e}", file=sys.stderr)
        return WeightDeltaEmission(
            round_id=round_id,
            org_authority_id=org_authority_id,
            snapshot_id_in=snapshot_id_in,
            delta_hash=delta_hash,
            delta_size_bytes=len(delta_bytes),
            differential_privacy_applied=dp_applied,
            coordinator_response_status="rejected",
            emission_timestamp=timestamp,
        )


# -- Local training loop -------------------------------------------------


def local_train_loop(
    snapshot_bytes: bytes, snapshot_id_in: str
) -> tuple[bytes, LocalTrainingMetrics]:
    """Run local training over this organization's data.

    Returns (weight_delta_bytes, metrics).

    Production deployments fill:
      - Model architecture (PyTorch nn.Module)
      - Dataset loader (over /data/input/uploads — local PHI)
      - Loss function (task-specific)
      - Differential-privacy wrapper (opacus PrivacyEngine when
        ENABLE_DIFFERENTIAL_PRIVACY is true)

    Reference shape demonstrates the canonical structure:
      1. Deserialize global snapshot → model.load_state_dict
      2. Iterate LOCAL_EPOCHS over the local dataset
      3. Compute loss + backward + step
      4. Compute (local_state - global_state) → weight delta
      5. Optionally apply DP noise to delta
      6. Serialize delta → bytes
    """
    started = datetime.now(timezone.utc)

    if not TORCH_AVAILABLE:
        # Stub mode — synthesize a delta envelope shape.
        print(
            "  [local_train] STUB — torch unavailable; production "
            "deployment runs the local training loop here",
            file=sys.stderr,
        )
        synthetic_delta = (
            b"[STUB_WEIGHT_DELTA_BYTES for round " + ROUND_ID.encode() + b" - "
            b"production deployment serializes (local_state - global_state) "
            b"as canonical bytes here]"
        )
        duration = (datetime.now(timezone.utc) - started).total_seconds()
        metrics = LocalTrainingMetrics(
            round_id=ROUND_ID,
            snapshot_id_in=snapshot_id_in,
            epochs=LOCAL_EPOCHS,
            samples_seen=0,
            final_loss=0.0,
            gradient_norm=0.0,
            differential_privacy_applied=ENABLE_DP,
            dp_epsilon_consumed=DP_EPSILON if ENABLE_DP else None,
            training_duration_seconds=duration,
        )
        return synthetic_delta, metrics

    # Production-shape skeleton (stubbed body — customer fills).
    print(
        f"  [local_train] training over local data (epochs={LOCAL_EPOCHS}, "
        f"batch={LOCAL_BATCH_SIZE}, lr={LOCAL_LR}, dp={ENABLE_DP})",
        file=sys.stderr,
    )

    # Customer fills:
    #   model = CustomerModelArchitecture()
    #   model.load_state_dict(torch.load(io.BytesIO(snapshot_bytes)))
    #   global_state = {k: v.clone() for k, v in model.state_dict().items()}
    #   dataset = CustomerDataset(INPUT_DIR)
    #   loader = torch.utils.data.DataLoader(dataset, batch_size=LOCAL_BATCH_SIZE)
    #   optimizer = torch.optim.SGD(model.parameters(), lr=LOCAL_LR)
    #
    #   if ENABLE_DP:
    #       from opacus import PrivacyEngine
    #       privacy_engine = PrivacyEngine()
    #       model, optimizer, loader = privacy_engine.make_private(
    #           module=model,
    #           optimizer=optimizer,
    #           data_loader=loader,
    #           noise_multiplier=...,
    #           max_grad_norm=DP_MAX_GRAD_NORM,
    #       )
    #
    #   for epoch in range(LOCAL_EPOCHS):
    #       for batch in loader:
    #           loss = compute_loss(model, batch)
    #           loss.backward()
    #           optimizer.step()
    #           optimizer.zero_grad()
    #
    #   local_state = model.state_dict()
    #   delta = {k: local_state[k] - global_state[k] for k in local_state}
    #   delta_bytes = serialize_canonical(delta)
    #   final_metrics = ...

    placeholder_delta = b"[PRODUCTION_WEIGHT_DELTA_PLACEHOLDER]"
    duration = (datetime.now(timezone.utc) - started).total_seconds()
    metrics = LocalTrainingMetrics(
        round_id=ROUND_ID,
        snapshot_id_in=snapshot_id_in,
        epochs=LOCAL_EPOCHS,
        samples_seen=0,
        final_loss=0.0,
        gradient_norm=0.0,
        differential_privacy_applied=ENABLE_DP,
        dp_epsilon_consumed=DP_EPSILON if ENABLE_DP else None,
        training_duration_seconds=duration,
    )
    return placeholder_delta, metrics


# -- Pipeline orchestration ----------------------------------------------


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Federated round: round_id={ROUND_ID} dp_enabled={ENABLE_DP}")

    # 1. Fetch global snapshot from coordinator
    snapshot_metadata, snapshot_bytes = fetch_global_snapshot()
    print(
        f"  global snapshot: id={snapshot_metadata.snapshot_id} "
        f"strategy={snapshot_metadata.aggregation_strategy} "
        f"cohort={snapshot_metadata.cohort_size}"
    )

    # 2. Local training loop (PHI-bearing data stays in volatile memory)
    delta_bytes, metrics = local_train_loop(
        snapshot_bytes, snapshot_metadata.snapshot_id
    )
    print(
        f"  local training: epochs={metrics.epochs} "
        f"samples={metrics.samples_seen} "
        f"loss={metrics.final_loss:.4f} "
        f"dp_applied={metrics.differential_privacy_applied}"
    )

    # 3. Emit weight delta to coordinator (no raw data, no gradients-with-leakage)
    org_authority_id = None  # set when BYO-HSM signing_config is enabled
    emission = emit_weight_delta(
        round_id=ROUND_ID,
        snapshot_id_in=snapshot_metadata.snapshot_id,
        delta_bytes=delta_bytes,
        dp_applied=metrics.differential_privacy_applied,
        org_authority_id=org_authority_id,
    )
    print(
        f"  delta emission: status={emission.coordinator_response_status} "
        f"size={emission.delta_size_bytes}B"
    )

    # 4. Round-complete output (audit-grade record of this organization's
    #    contribution to the round)
    outcome = RoundOutcome(
        round_id=ROUND_ID,
        snapshot_metadata=snapshot_metadata,
        training_metrics=metrics,
        weight_delta_emission=emission,
    )
    output_path = OUTPUT_DIR / "round-outcome.json"
    output_path.write_text(
        json.dumps(
            {
                "schema_version": "federated-learning-v1",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "outcome": json.loads(outcome.model_dump_json()),
            },
            indent=2,
        )
    )
    print(f"Round outcome → {output_path}")


if __name__ == "__main__":
    main()

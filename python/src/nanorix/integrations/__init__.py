"""
Customer-facing integrations for Nanorix capsules.

This subpackage hosts thin adapters that bridge popular agent frameworks,
web frameworks, and LLM SDKs into Nanorix's existing in-capsule
ActivityEvent emission helpers (`nanorix.helpers.activity_events`). Each
integration MUST:

- Reuse the 4 existing `ActivityEvent` variants from
  `nanorix.helpers.activity_events` verbatim (Forever-Standard).
- Compute hashes via the shipped JCS-flavor `_canonical_hash` so wire-format
  byte-equivalence with the 4-language verifier corpus holds.
- Decline silent defaults for compliance-framing arguments — customers
  declare `framework=` explicitly.
- Refuse to retain raw payloads. Plaintext stays in the customer's process
  memory; only SHA-512 hex digests reach the AuditProof.

V1 ships LangChain (item 1 of the integration ship sequence). Subsequent
items in the sequence (`integrations/openai/`, `integrations/anthropic/`,
`integrations/fastapi/`, `integrations/flask/`) ship under their own ADR
review and the same Forever-Standard discipline.

Optional install per integration:

    pip install nanorix[langchain]

When the optional dependency is missing at import time, the integration
module raises `ImportError` with a remediation hint pointing the
customer to the relevant extras group.
"""

from __future__ import annotations

# Batch 9 (2026-05-28) — C2PA LLM-output provenance.
# Sibling primitive next to AuditProof; AuditProof byte-form unchanged.
from nanorix.integrations.c2pa_provenance import (
    AssertionType,
    C2PAClaimGeneratorInfo,
    C2PAManifest,
    C2PAManifestBuilder,
    C2PASignatureInfo,
    C2PAVerificationResult,
    NARROW_CLAIM_DISCLAIMER as C2PA_NARROW_CLAIM_DISCLAIMER,
    SIGNATURE_ALGORITHM as C2PA_SIGNATURE_ALGORITHM,
    VALID_ASSERTION_TYPES as C2PA_VALID_ASSERTION_TYPES,
    verify_c2pa_manifest,
)

# Batch 13 (2026-05-28) — data-pipeline orchestrator operators.
# The framework-agnostic core is safe to import eagerly (no framework
# dependency). The Airflow operator + Temporal activity shells are NOT
# imported here — they import their framework (apache-airflow /
# temporalio) and would fail without it installed. Import the shell you
# need by its submodule path:
#
#     from nanorix.integrations.airflow import NanorixCapsuleOperator
#     from nanorix.integrations.temporal import make_capsule_activity
#
# Both shells delegate to run_capsule_job; Dagster / Prefect / Argo can be
# wired against the same core (see docs/integrations/orchestrators.md).
from nanorix.integrations._orchestrator_core import (
    CapsuleJobParams,
    CapsuleJobResult,
    OrchestratorError,
    NARROW_CLAIM_DISCLAIMER as ORCHESTRATOR_NARROW_CLAIM_DISCLAIMER,
    VALID_DATA_CLASSIFICATIONS as ORCHESTRATOR_VALID_DATA_CLASSIFICATIONS,
    run_capsule_job,
)

# Batch 16 (2026-05-28) — Jupyter integration (FINAL gap-closing batch).
# The IPython-free core (_notebook_core) is safe to import eagerly: it
# reuses run_capsule_job and the offline verifier, and has NO IPython
# dependency. The IPython-facing shell (nanorix.integrations.jupyter) is
# NOT imported here — it would need IPython installed. Load it the
# notebook way:
#
#     %load_ext nanorix.integrations.jupyter
#
# or import it explicitly:
#
#     from nanorix.integrations.jupyter import NanorixMagics, NanorixDisplay
#
# The pure helpers below render the AuditProof + parse the magic line and
# run a cell as a capsule job without IPython.
from nanorix.integrations._notebook_core import (
    MagicArgsError,
    parse_magic_args,
    render_auditproof_html,
    render_cell_result_html,
    render_verification_html,
    run_capsule_cell,
)

__all__: list[str] = [
    "AssertionType",
    "C2PAClaimGeneratorInfo",
    "C2PAManifest",
    "C2PAManifestBuilder",
    "C2PASignatureInfo",
    "C2PAVerificationResult",
    "C2PA_NARROW_CLAIM_DISCLAIMER",
    "C2PA_SIGNATURE_ALGORITHM",
    "C2PA_VALID_ASSERTION_TYPES",
    "verify_c2pa_manifest",
    # Batch 13 orchestrator core (framework-agnostic).
    "CapsuleJobParams",
    "CapsuleJobResult",
    "OrchestratorError",
    "ORCHESTRATOR_NARROW_CLAIM_DISCLAIMER",
    "ORCHESTRATOR_VALID_DATA_CLASSIFICATIONS",
    "run_capsule_job",
    # Batch 16 Jupyter integration — IPython-free helpers (the magic shell
    # is in nanorix.integrations.jupyter, imported lazily).
    "MagicArgsError",
    "parse_magic_args",
    "run_capsule_cell",
    "render_auditproof_html",
    "render_verification_html",
    "render_cell_result_html",
]

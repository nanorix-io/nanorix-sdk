"""Observability adapters for Nanorix telemetry (Batch 17).

Batch 17 (2026-05-29) — real, installable observability adapter modules.
Export Nanorix telemetry to your observability backend:

- **Datadog** — AuditProof events as Datadog Logs + billing-meter as custom
  metrics (``nanorix.observability.datadog``);
- **Splunk** — AuditProof + cost-meter events via HEC
  (``nanorix.observability.splunk``);
- **Honeycomb** — AuditProof events as OTel spans via OTLP
  (``nanorix.observability.honeycomb``);
- **Sentry** — AuditProof verification FAILURES as tagged Sentry events
  (``nanorix.observability.sentry``).

## Architecture (framework-agnostic-core + guarded-shell)

The pure shaping engine lives in :mod:`nanorix.observability._core` and has
NO vendor-SDK dependency — every transform turns an AuditProof (+ optional
offline-verification result) into a vendor wire shape using plain dicts. It
is safe to import eagerly and fully unit-testable without any vendor SDK:

    from nanorix.observability import (
        auditproof_to_datadog_event,
        auditproof_to_splunk_event,
        auditproof_to_honeycomb_span,
        auditproof_to_sentry_event,
        cost_meter_to_datadog_series,
        cost_meter_to_splunk_event,
        assert_structural_facts_only,
    )

The per-vendor pusher shells live behind optional extras. Import the one you
need by its submodule path; the vendor SDK (when any is needed at all) is
guarded so a missing dependency fails with a clear, actionable hint instead
of at package import:

    from nanorix.observability.datadog import DatadogObservabilityAdapter   # pip install nanorix[datadog]
    from nanorix.observability.splunk import SplunkObservabilityAdapter     # pip install nanorix[splunk]
    from nanorix.observability.honeycomb import HoneycombObservabilityAdapter  # pip install nanorix[honeycomb]
    from nanorix.observability.sentry import SentryObservabilityAdapter     # pip install nanorix[sentry]

Datadog + Splunk POST via the SDK's own httpx transport and need NO vendor
SDK; Honeycomb (OTel) + Sentry guard their vendor import and raise only on
use. New Relic, Dynatrace, Grafana Loki, etc. follow the SAME transforms —
they are documentation, not new engines (see
``docs/integrations/observability-adapters/``).

## Discipline (NON-NEGOTIABLE)

Every emitted metric / tag / attribute / log message / event field is a
STRUCTURAL FACT — a count, a lifecycle outcome, a cryptographic
verification result. The forbidden verdict vocabulary (``COMPLIANT`` /
``SATISFIED`` / ``PASSED`` / ``MEETS``) is rejected by
:func:`assert_structural_facts_only`, which every shell runs before emit.
The auditor remains the certifying authority
(``feedback_narrow_signed_claim_auditor_certifies.md``). Tags carry
customer-facing nouns only — never internal engine names.
"""

from __future__ import annotations

from nanorix.observability._core import (
    FORBIDDEN_VOCAB,
    STRUCTURAL_FACTS_NOTE,
    ObservabilityError,
    ProofFields,
    assert_structural_facts_only,
    auditproof_to_datadog_event,
    auditproof_to_honeycomb_span,
    auditproof_to_sentry_event,
    auditproof_to_splunk_event,
    cost_meter_to_datadog_series,
    cost_meter_to_splunk_event,
    read_proof_fields,
)

__all__ = [
    "FORBIDDEN_VOCAB",
    "STRUCTURAL_FACTS_NOTE",
    "ObservabilityError",
    "ProofFields",
    "assert_structural_facts_only",
    "read_proof_fields",
    "auditproof_to_datadog_event",
    "auditproof_to_splunk_event",
    "auditproof_to_honeycomb_span",
    "auditproof_to_sentry_event",
    "cost_meter_to_datadog_series",
    "cost_meter_to_splunk_event",
]

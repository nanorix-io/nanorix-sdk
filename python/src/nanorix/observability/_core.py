"""Framework-agnostic core for observability adapters (Batch 17).

Batch 17 (2026-05-29) — gap-closing arc: real, installable observability
adapter modules. This module is the pure, vendor-SDK-free engine that the
Datadog / Splunk / Honeycomb / Sentry shells
(``nanorix.observability.{datadog,splunk,honeycomb,sentry}``) all call to
SHAPE Nanorix telemetry into each vendor's wire form. It has NO dependency
on ``datadog`` / ``datadog-api-client`` / ``sentry-sdk`` / any
``opentelemetry`` package and is fully unit-testable with plain dicts (see
``tests/test_observability_adapters.py``).

The contract is a set of pure transforms:

    auditproof_to_datadog_event(proof, verification) -> dict
    auditproof_to_splunk_event(proof, verification) -> dict
    auditproof_to_honeycomb_span(proof, verification) -> dict
    auditproof_to_sentry_event(proof, verification) -> dict (failures only)
    cost_meter_to_datadog_series(usage, forecast, env) -> list[dict]
    cost_meter_to_splunk_event(usage, forecast) -> dict

plus the AuditProof field-reader (:func:`read_proof_fields`) and the
forbidden-vocabulary gate (:func:`assert_structural_facts_only`).

## Trust-boundary discipline (NON-NEGOTIABLE)

- **STRUCTURAL FACTS only.** Every emitted metric name, tag, attribute,
  log message and event field is a structural fact — a count, an outcome
  ("pass"/"fail"), a closed-enum ``pattern_tag``, a canonical
  ``failure_reason``. NEVER a regulatory adjudication. The forbidden
  vocabulary (``COMPLIANT`` / ``SATISFIED`` / ``PASSED`` / ``MEETS``) is
  rejected by :func:`assert_structural_facts_only`, which every shell runs
  before it emits. (``feedback_narrow_signed_claim_auditor_certifies.md``.)
- **No internal engine names.** Tags / attributes / field names carry
  customer-facing nouns only (``capsule_id``, ``pattern_tag``,
  ``failure_reason``, ``signing_authority_id``) — never internal subsystem
  names (ADR-014 + ``feedback_ip_protection.md``).
- **No new signed primitive.** These transforms read the existing
  AuditProof + the existing offline verification result; they sign
  nothing and add no field to the signed proof.
- **Customer-sole-declarer (ADR-013).** ``pattern_tag`` /
  ``data_classification`` are read verbatim from the proof; never
  inferred here.

## Why a framework-agnostic core

The vendor shells genuinely require the vendor's transport at runtime
(``datadog.statsd`` / ``sentry_sdk.capture_message`` / an OTLP exporter /
an HEC POST). Keeping all shaping logic here means:

- the shells stay thin (read the proof -> call a transform -> hand the
  dict to the vendor transport);
- the test suite imports only this module + plain dicts and never needs a
  vendor SDK installed;
- a future backend (New Relic, Dynatrace, Grafana Loki, ...) is wired with
  the SAME transforms — it is documentation, not a new engine.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "FORBIDDEN_VOCAB",
    "STRUCTURAL_FACTS_NOTE",
    "ObservabilityError",
    "ProofFields",
    "read_proof_fields",
    "assert_structural_facts_only",
    "auditproof_to_datadog_event",
    "auditproof_to_splunk_event",
    "auditproof_to_honeycomb_span",
    "auditproof_to_sentry_event",
    "cost_meter_to_datadog_series",
    "cost_meter_to_splunk_event",
]

# Forbidden adjudication vocabulary. Mirrors the same tuple the docs +
# billing module + GRC core pin. Any emitted metric/tag/attr/field that
# contains one of these (case-insensitively) is rejected — observability
# events are STRUCTURAL FACTS, never compliance verdicts.
FORBIDDEN_VOCAB: Tuple[str, ...] = ("COMPLIANT", "SATISFIED", "PASSED", "MEETS")

# Carried as a module constant so any serializer / doc generator reads the
# same one-line discipline statement.
STRUCTURAL_FACTS_NOTE = (
    "Observability events emitted from Nanorix telemetry are STRUCTURAL "
    "FACTS — counts, lifecycle outcomes, and cryptographic verification "
    "results. They are NOT regulatory adjudication; the auditor remains "
    "the certifying authority for any compliance finding."
)


class ObservabilityError(Exception):
    """Raised for observability-adapter shaping / discipline failures.

    Forbidden-vocabulary violations and missing-required-field errors
    raise this class so the failure surfaces in the adapter author's stack
    trace with an actionable message rather than silently emitting a
    non-structural event.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Forbidden-vocabulary gate
# ─────────────────────────────────────────────────────────────────────────────


def assert_structural_facts_only(payload: Any) -> None:
    """Reject any payload carrying compliance-adjudication vocabulary.

    Walks strings (and the keys + values of mappings / sequences) looking
    for the forbidden verdict words. Every shell runs this on the dict it
    is about to hand the vendor transport, so a non-structural event can
    never leave the SDK.

    Args:
        payload: A string, mapping, or sequence to scan. Non-string scalars
            are ignored.

    Raises:
        ObservabilityError: When a forbidden word appears anywhere in the
            payload (case-insensitive).
    """
    for token in _iter_strings(payload):
        upper = token.upper()
        for forbidden in FORBIDDEN_VOCAB:
            if forbidden in upper:
                raise ObservabilityError(
                    f"observability payload contains forbidden vocabulary "
                    f"{forbidden!r}: {token!r}. Per "
                    "feedback_narrow_signed_claim_auditor_certifies.md, "
                    "observability events are STRUCTURAL FACTS only — never "
                    "a compliance verdict. The auditor adjudicates."
                )


def _iter_strings(payload: Any) -> List[str]:
    """Collect every string in a nested payload (keys + values)."""
    out: List[str] = []
    if isinstance(payload, str):
        out.append(payload)
    elif isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(key, str):
                out.append(key)
            out.extend(_iter_strings(value))
    elif isinstance(payload, (list, tuple, set)):
        for item in payload:
            out.extend(_iter_strings(item))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AuditProof field reader
# ─────────────────────────────────────────────────────────────────────────────


class ProofFields:
    """Normalized, structural-fact view of one AuditProof.

    The shells read an AuditProof — which may be a dict (``GET
    /v1/auditproofs`` JSON), a ``CdpAccess`` / ``VerificationCdp`` model,
    or any object exposing the same attributes — through this reader so the
    transforms never reach into vendor- or model-specific shapes. Only the
    customer-facing, structural fields are exposed; no internal engine name
    is ever surfaced.

    Attributes:
        capsule_id: The capsule the proof attests. Required.
        proof_id: The proof's own id, when present (``None`` otherwise).
        pattern_tag: The receipt's signed closed-enum ``pattern_tag`` (ADR-039),
            or ``None`` when the proof carries no tag.
        data_classification: The capsule's customer-declared classification
            (ADR-013), or ``None``.
        signing_authority_id: The signing authority id (e.g.
            ``us-kms-nanorix-v1``), or ``None``.
        terminated_at: The capsule termination timestamp, or ``None``.
    """

    __slots__ = (
        "capsule_id",
        "proof_id",
        "pattern_tag",
        "data_classification",
        "signing_authority_id",
        "terminated_at",
    )

    def __init__(
        self,
        capsule_id: str,
        *,
        proof_id: Optional[str] = None,
        pattern_tag: Optional[str] = None,
        data_classification: Optional[str] = None,
        signing_authority_id: Optional[str] = None,
        terminated_at: Optional[str] = None,
    ) -> None:
        self.capsule_id = capsule_id
        self.proof_id = proof_id
        self.pattern_tag = pattern_tag
        self.data_classification = data_classification
        self.signing_authority_id = signing_authority_id
        self.terminated_at = terminated_at

    def base_tags(self) -> Dict[str, str]:
        """Return the structural-fact tag set common to every backend.

        Only non-``None`` fields are included so a backend never receives a
        ``"none"`` literal it would index as a real value.
        """
        tags: Dict[str, str] = {"capsule_id": self.capsule_id}
        if self.pattern_tag is not None:
            tags["pattern_tag"] = self.pattern_tag
        if self.data_classification is not None:
            tags["data_classification"] = self.data_classification
        if self.signing_authority_id is not None:
            tags["signing_authority_id"] = self.signing_authority_id
        return tags


def _get(proof: Any, key: str) -> Any:
    """Read ``key`` from a dict-like or attribute-like proof; ``None`` if absent."""
    if isinstance(proof, Mapping):
        return proof.get(key)
    return getattr(proof, key, None)


def read_proof_fields(proof: Any) -> ProofFields:
    """Extract the structural-fact fields from any AuditProof shape.

    Accepts a dict (API JSON), a ``CdpAccess`` / ``VerificationCdp`` model,
    or any object exposing the same attributes. Reads only customer-facing
    structural fields (ADR-014 — no internal engine names are read or
    surfaced).

    Args:
        proof: The AuditProof (dict or object).

    Returns:
        A :class:`ProofFields` view.

    Raises:
        ObservabilityError: When no ``capsule_id`` can be read — the one
            required structural anchor.
    """
    capsule_id = _get(proof, "capsule_id")
    if not capsule_id:
        raise ObservabilityError(
            "AuditProof is missing the required 'capsule_id' field; cannot "
            "shape a structural observability event without the capsule "
            "anchor."
        )
    proof_id = _get(proof, "id") or _get(proof, "proof_id")
    return ProofFields(
        capsule_id=str(capsule_id),
        proof_id=str(proof_id) if proof_id is not None else None,
        pattern_tag=_opt_str(_get(proof, "pattern_tag")),
        data_classification=_opt_str(_get(proof, "data_classification")),
        signing_authority_id=_opt_str(_get(proof, "signing_authority_id")),
        terminated_at=_opt_str(_get(proof, "terminated_at")),
    )


def _opt_str(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _verification_outcome(verification: Any) -> Tuple[bool, Optional[str], Optional[int]]:
    """Normalize a verification result into (valid, failure_reason, step_failed).

    Accepts the SDK ``VerifyResult`` dataclass (``.ok`` + ``.failure_reason``
    + ``.failed_step``), a ``valid``/``failure_reason``/``step_failed`` shape
    (as the docs describe the API verify helper), or a plain dict carrying
    any of those keys.
    """
    if verification is None:
        return True, None, None
    valid = _get(verification, "ok")
    if valid is None:
        valid = _get(verification, "valid")
    failure_reason = _opt_str(_get(verification, "failure_reason"))
    step_failed = _get(verification, "failed_step")
    if step_failed is None:
        step_failed = _get(verification, "step_failed")
    step_int = int(step_failed) if isinstance(step_failed, int) else None
    return bool(valid), failure_reason, step_int


# ─────────────────────────────────────────────────────────────────────────────
# Datadog transforms
# ─────────────────────────────────────────────────────────────────────────────


def auditproof_to_datadog_event(
    proof: Any,
    verification: Any = None,
    *,
    env: str = "production",
) -> Dict[str, Any]:
    """Shape an AuditProof + verification outcome into a Datadog Logs event.

    The returned dict matches Datadog's HTTP Logs intake item shape
    (``ddsource`` / ``ddtags`` / ``message`` / ``service`` / ``status``).
    ``status`` is ``"info"`` when verification passed (or no verification
    was supplied) and ``"warn"`` when it failed.

    All tags + the message are STRUCTURAL FACTS; the gate is run before the
    dict is returned, so any non-structural content raises here.

    Args:
        proof: The AuditProof (dict or model).
        verification: Optional offline-verification result.
        env: The deployment environment tag value.

    Returns:
        A Datadog Logs intake item dict.
    """
    fields = read_proof_fields(proof)
    valid, failure_reason, _ = _verification_outcome(verification)

    if valid:
        message = f"AuditProof emitted for capsule {fields.capsule_id}"
        status = "info"
    else:
        message = (
            f"AuditProof verification failed for capsule {fields.capsule_id} "
            f"— {failure_reason or 'unknown'}"
        )
        status = "warn"

    tag_pairs = [f"env:{env}"]
    tag_pairs.extend(f"nanorix.{k}:{v}" for k, v in fields.base_tags().items())
    if not valid:
        tag_pairs.append(f"nanorix.failure_reason:{failure_reason or 'unknown'}")
        tag_pairs.append("nanorix.verification_outcome:fail")
    else:
        tag_pairs.append("nanorix.verification_outcome:pass")

    event = {
        "ddsource": "nanorix",
        "ddtags": ",".join(tag_pairs),
        "hostname": "nanorix-adapter",
        "message": message,
        "service": "nanorix",
        "status": status,
    }
    assert_structural_facts_only(event)
    return event


def cost_meter_to_datadog_series(
    usage: Any,
    forecast: Any = None,
    *,
    env: str = "production",
    timestamp: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Shape billing-meter usage (+ optional forecast) into Datadog series.

    Each returned item is a simplified ``MetricSeries`` dict
    (``metric`` / ``type`` / ``points`` / ``tags``) the Datadog shell maps
    onto the ``datadog-api-client`` ``MetricSeries`` model — or submits via
    HTTP intake directly. All counts are STRUCTURAL FACTS.

    Args:
        usage: A ``BillingMeterUsage`` (or dict) with ``auditproof_count`` /
            ``capsule_count`` / ``tier`` / ``by_pattern`` / ``by_shape``.
        forecast: Optional ``CostForecast`` (or dict) with
            ``current_cycle_spend_cents`` / ``forecasted_cycle_spend_cents``.
        env: The deployment environment tag value.
        timestamp: Optional unix timestamp for the points; defaults to now.

    Returns:
        A list of metric-series dicts.
    """
    ts = int(timestamp if timestamp is not None else time.time())
    tier = _opt_str(_get(usage, "tier")) or "unknown"
    series: List[Dict[str, Any]] = [
        _gauge(
            "nanorix.billing.auditproof_count",
            _num(_get(usage, "auditproof_count")),
            ts,
            [f"env:{env}", f"tier:{tier}"],
        ),
        _gauge(
            "nanorix.billing.capsule_count",
            _num(_get(usage, "capsule_count")),
            ts,
            [f"env:{env}", f"tier:{tier}"],
        ),
    ]

    for row in _get(usage, "by_pattern") or []:
        series.append(
            _gauge(
                "nanorix.billing.auditproof_by_pattern",
                _num(_get(row, "auditproof_count")),
                ts,
                [f"env:{env}", f"pattern_tag:{_opt_str(_get(row, 'pattern_tag'))}"],
            )
        )
    for row in _get(usage, "by_shape") or []:
        series.append(
            _gauge(
                "nanorix.billing.capsules_by_shape",
                _num(_get(row, "capsule_count")),
                ts,
                [f"env:{env}", f"shape:{_opt_str(_get(row, 'shape'))}"],
            )
        )

    if forecast is not None:
        f_tier = _opt_str(_get(forecast, "tier")) or tier
        series.append(
            _gauge(
                "nanorix.billing.current_spend_cents",
                _num(_get(forecast, "current_cycle_spend_cents")),
                ts,
                [f"env:{env}", f"tier:{f_tier}"],
            )
        )
        series.append(
            _gauge(
                "nanorix.billing.forecasted_spend_cents",
                _num(_get(forecast, "forecasted_cycle_spend_cents")),
                ts,
                [f"env:{env}", f"tier:{f_tier}"],
            )
        )

    assert_structural_facts_only(series)
    return series


def _gauge(metric: str, value: float, ts: int, tags: Sequence[str]) -> Dict[str, Any]:
    return {
        "metric": metric,
        "type": "gauge",
        "points": [{"timestamp": ts, "value": float(value)}],
        "tags": list(tags),
    }


def _num(value: Any) -> float:
    return float(value) if value is not None else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Splunk (HEC) transforms
# ─────────────────────────────────────────────────────────────────────────────


def auditproof_to_splunk_event(
    proof: Any,
    verification: Any = None,
) -> Dict[str, Any]:
    """Shape an AuditProof + verification outcome into a Splunk HEC event body.

    Returns the inner ``event`` dict (the HEC envelope — ``time`` / ``host``
    / ``sourcetype`` / ``index`` — is added by the shell). Every field is a
    STRUCTURAL FACT.

    Args:
        proof: The AuditProof (dict or model).
        verification: Optional offline-verification result.

    Returns:
        A Splunk event dict.
    """
    fields = read_proof_fields(proof)
    valid, failure_reason, step_failed = _verification_outcome(verification)
    event = {
        "event_type": "auditproof_emitted",
        "capsule_id": fields.capsule_id,
        "proof_id": fields.proof_id,
        "pattern_tag": fields.pattern_tag,
        "signing_authority_id": fields.signing_authority_id,
        "data_classification": fields.data_classification,
        "terminated_at": fields.terminated_at,
        "verification_outcome": "pass" if valid else "fail",
        "failure_reason": None if valid else (failure_reason or "unknown"),
        "step_failed": None if valid else step_failed,
        "adapter_version": "splunk-adapter@v1",
    }
    assert_structural_facts_only(event)
    return event


def cost_meter_to_splunk_event(
    usage: Any,
    forecast: Any = None,
) -> Dict[str, Any]:
    """Shape billing-meter usage (+ optional forecast) into a Splunk HEC event.

    Returns the inner ``event`` dict for a ``nanorix:cost_meter`` sourcetype.
    All counts are STRUCTURAL FACTS.
    """
    event: Dict[str, Any] = {
        "event_type": "cost_meter_snapshot",
        "tier": _opt_str(_get(usage, "tier")),
        "from": _opt_str(_get(usage, "from_")) or _opt_str(_get(usage, "from")),
        "to": _opt_str(_get(usage, "to")),
        "capsule_count": _int(_get(usage, "capsule_count")),
        "auditproof_count": _int(_get(usage, "auditproof_count")),
        "by_pattern": [
            {
                "pattern_tag": _opt_str(_get(r, "pattern_tag")),
                "count": _int(_get(r, "auditproof_count")),
            }
            for r in _get(usage, "by_pattern") or []
        ],
        "by_shape": [
            {
                "shape": _opt_str(_get(r, "shape")),
                "capsule_count": _int(_get(r, "capsule_count")),
                "auditproof_count": _int(_get(r, "auditproof_count")),
            }
            for r in _get(usage, "by_shape") or []
        ],
    }
    if forecast is not None:
        event["current_spend_cents"] = _int(_get(forecast, "current_cycle_spend_cents"))
        event["forecasted_spend_cents"] = _int(
            _get(forecast, "forecasted_cycle_spend_cents")
        )
    assert_structural_facts_only(event)
    return event


def _int(value: Any) -> int:
    return int(value) if value is not None else 0


# ─────────────────────────────────────────────────────────────────────────────
# Honeycomb (OTel span) transform
# ─────────────────────────────────────────────────────────────────────────────


def auditproof_to_honeycomb_span(
    proof: Any,
    verification: Any = None,
) -> Dict[str, Any]:
    """Shape an AuditProof into an OTel-span description for Honeycomb.

    Returns a vendor-neutral span dict (``name`` / ``attributes`` /
    ``status_code`` / ``status_description``) the Honeycomb shell replays
    onto a real OTel span. ``status_code`` is ``"OK"`` when verification
    passed and ``"ERROR"`` when it failed (drives Honeycomb's BubbleUp +
    triggers). Every attribute is a STRUCTURAL FACT.

    Args:
        proof: The AuditProof (dict or model).
        verification: Optional offline-verification result.

    Returns:
        A span-description dict.
    """
    fields = read_proof_fields(proof)
    valid, failure_reason, step_failed = _verification_outcome(verification)

    attributes: Dict[str, Any] = {
        f"nanorix.{k}": v for k, v in fields.base_tags().items()
    }
    if fields.proof_id is not None:
        attributes["nanorix.proof_id"] = fields.proof_id

    if valid:
        attributes["nanorix.verification_outcome"] = "pass"
        status_code = "OK"
        status_description: Optional[str] = None
    else:
        attributes["nanorix.verification_outcome"] = "fail"
        if failure_reason is not None:
            attributes["nanorix.failure_reason"] = failure_reason
        if step_failed is not None:
            attributes["nanorix.step_failed"] = step_failed
        status_code = "ERROR"
        status_description = failure_reason or "verification_failed"

    span = {
        "name": "nanorix.auditproof.emitted",
        "attributes": attributes,
        "status_code": status_code,
        "status_description": status_description,
    }
    assert_structural_facts_only(span)
    return span


# ─────────────────────────────────────────────────────────────────────────────
# Sentry transform (failures only)
# ─────────────────────────────────────────────────────────────────────────────


def auditproof_to_sentry_event(
    proof: Any,
    verification: Any,
) -> Optional[Dict[str, Any]]:
    """Shape a FAILED AuditProof verification into a Sentry event description.

    Sentry is an alerting backend; only verification FAILURES warrant an
    event. When the verification passed (or is ``None``) this returns
    ``None`` so the shell emits nothing. Otherwise it returns a dict
    (``message`` / ``level`` / ``tags`` / ``context``) the Sentry shell
    replays onto ``sentry_sdk.capture_message``. The message + tags are
    STRUCTURAL FACTS.

    Args:
        proof: The AuditProof (dict or model).
        verification: The offline-verification result. Required (Sentry only
            fires on a known outcome).

    Returns:
        A Sentry event-description dict, or ``None`` when verification
        passed.
    """
    valid, failure_reason, step_failed = _verification_outcome(verification)
    if valid:
        return None

    fields = read_proof_fields(proof)
    message = (
        f"AuditProof verification failed for capsule {fields.capsule_id} "
        f"— {failure_reason or 'unknown'}"
    )
    tags = {f"nanorix.{k}": v for k, v in fields.base_tags().items()}
    tags["nanorix.failure_reason"] = failure_reason or "unknown"

    context = {
        "proof_id": fields.proof_id,
        "step_failed": step_failed,
        "terminated_at": fields.terminated_at,
    }
    event = {
        "message": message,
        "level": "warning",
        "tags": tags,
        "context": context,
    }
    assert_structural_facts_only(event)
    return event

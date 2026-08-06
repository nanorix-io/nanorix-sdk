"""Tests for observability adapters.

These tests import ONLY the pure core
(``nanorix.observability._core``) + the guarded shells with
dependency-injected transports (``submit=`` / ``capture=`` / ``tracer=``).
They do NOT import datadog / sentry-sdk / opentelemetry / requests —
verifying the architecture goal that the observability core + shells are
exercisable without any vendor SDK installed.

Invariants asserted:

- transforms emit STRUCTURAL FACTS only (the forbidden-vocab gate fires);
- no internal engine name leaks into any emitted field;
- the shells push the shaped payload to the injected transport unchanged;
- Sentry emits only on verification FAILURE;
- adapter objects retain NO vendor credential after a push
  (no-credential-retention, mirroring the connectors' discipline).
"""

from __future__ import annotations

import pytest

from nanorix.observability import (
    FORBIDDEN_VOCAB,
    ObservabilityError,
    assert_structural_facts_only,
    auditproof_to_datadog_event,
    auditproof_to_honeycomb_span,
    auditproof_to_sentry_event,
    auditproof_to_splunk_event,
    cost_meter_to_datadog_series,
    cost_meter_to_splunk_event,
    read_proof_fields,
)
from nanorix.observability._core import STRUCTURAL_FACTS_NOTE
from nanorix.observability.datadog import DatadogObservabilityAdapter
from nanorix.observability.honeycomb import HoneycombObservabilityAdapter
from nanorix.observability.sentry import SentryObservabilityAdapter
from nanorix.observability.splunk import SplunkObservabilityAdapter


# ── Fixtures ──────────────────────────────────────────────────────────


def _proof_dict(**overrides):
    base = {
        "capsule_id": "cap_a1b2c3d4",
        "id": "proof_xyz",
        "pattern_tag": "rcm_claim",
        "data_classification": "PHI",
        "signing_authority_id": "us-kms-nanorix-v1",
        "terminated_at": "2026-05-29T12:00:00Z",
    }
    base.update(overrides)
    return base


class _Proof:
    """Attribute-shaped proof (mirrors a CdpAccess / VerificationCdp model)."""

    def __init__(self, **kw):
        for k, v in _proof_dict(**kw).items():
            setattr(self, k, v)


class _VerifyResultOk:
    ok = True
    valid = True
    failure_reason = ""
    failed_step = None


class _VerifyResultFail:
    ok = False
    valid = False
    failure_reason = "signature_invalid"
    failed_step = 4


class _Usage:
    tier = "Business"
    from_ = "2026-05-01"
    to = "2026-05-31"
    capsule_count = 12
    auditproof_count = 42

    class _Row:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    by_pattern = [_Row(pattern_tag="rcm_claim", auditproof_count=30)]
    by_shape = [_Row(shape="medium", capsule_count=8, auditproof_count=24)]


class _Forecast:
    tier = "Business"
    current_cycle_spend_cents = 1500
    forecasted_cycle_spend_cents = 4500


class FakeOTelSpan:
    """Records set_attribute / status calls; no opentelemetry import."""

    def __init__(self, log):
        self._log = log
        self.attributes = {}
        self.status = None

    def set_attribute(self, key, value):
        self.attributes[key] = value
        self._log.append(("attr", key, value))

    def set_status_code(self, code, description):
        self.status = (code, description)
        self._log.append(("status", code, description))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeTracer:
    def __init__(self):
        self.log = []
        self.spans = []

    def start_as_current_span(self, name):
        self.log.append(("span", name))
        span = FakeOTelSpan(self.log)
        self.spans.append(span)
        return span


# ── Core: field reading ───────────────────────────────────────────────


def test_read_proof_fields_from_dict():
    fields = read_proof_fields(_proof_dict())
    assert fields.capsule_id == "cap_a1b2c3d4"
    assert fields.proof_id == "proof_xyz"
    assert fields.pattern_tag == "rcm_claim"
    assert fields.data_classification == "PHI"
    assert fields.signing_authority_id == "us-kms-nanorix-v1"


def test_read_proof_fields_from_object():
    fields = read_proof_fields(_Proof())
    assert fields.capsule_id == "cap_a1b2c3d4"
    assert fields.pattern_tag == "rcm_claim"


def test_read_proof_fields_missing_capsule_id_raises():
    with pytest.raises(ObservabilityError):
        read_proof_fields({"pattern_tag": "pa"})


def test_base_tags_omit_none_fields():
    fields = read_proof_fields(
        {"capsule_id": "cap_x", "pattern_tag": None, "data_classification": None}
    )
    tags = fields.base_tags()
    assert tags == {"capsule_id": "cap_x"}
    assert "pattern_tag" not in tags


# ── Core: forbidden-vocabulary gate ───────────────────────────────────


@pytest.mark.parametrize("word", FORBIDDEN_VOCAB)
def test_gate_rejects_each_forbidden_word(word):
    with pytest.raises(ObservabilityError):
        assert_structural_facts_only({"message": f"this workload {word} the control"})


def test_gate_rejects_lowercase_forbidden_word():
    with pytest.raises(ObservabilityError):
        assert_structural_facts_only("the proof satisfied SOC 2")


def test_gate_scans_nested_keys_and_values():
    with pytest.raises(ObservabilityError):
        assert_structural_facts_only({"tags": ["a", {"verdict": "PASSED"}]})


def test_gate_allows_structural_facts():
    # No exception.
    assert_structural_facts_only(
        {"message": "AuditProof emitted for capsule cap_x", "count": 5}
    )
    assert "STRUCTURAL" in STRUCTURAL_FACTS_NOTE


# ── Core: Datadog transforms ──────────────────────────────────────────


def test_datadog_event_pass():
    event = auditproof_to_datadog_event(_proof_dict(), _VerifyResultOk(), env="prod")
    assert event["status"] == "info"
    assert event["ddsource"] == "nanorix"
    assert "nanorix.verification_outcome:pass" in event["ddtags"]
    assert "env:prod" in event["ddtags"]
    assert "nanorix.capsule_id:cap_a1b2c3d4" in event["ddtags"]


def test_datadog_event_fail():
    event = auditproof_to_datadog_event(_proof_dict(), _VerifyResultFail())
    assert event["status"] == "warn"
    assert "signature_invalid" in event["message"]
    assert "nanorix.failure_reason:signature_invalid" in event["ddtags"]


def test_datadog_event_no_verification_is_info():
    event = auditproof_to_datadog_event(_proof_dict())
    assert event["status"] == "info"


def test_datadog_cost_series_shape():
    series = cost_meter_to_datadog_series(_Usage(), _Forecast(), env="prod")
    metrics = {s["metric"] for s in series}
    assert "nanorix.billing.auditproof_count" in metrics
    assert "nanorix.billing.capsule_count" in metrics
    assert "nanorix.billing.auditproof_by_pattern" in metrics
    assert "nanorix.billing.capsules_by_shape" in metrics
    assert "nanorix.billing.current_spend_cents" in metrics
    assert "nanorix.billing.forecasted_spend_cents" in metrics
    for s in series:
        assert s["type"] == "gauge"
        assert s["points"][0]["value"] is not None


def test_datadog_cost_series_without_forecast():
    series = cost_meter_to_datadog_series(_Usage())
    metrics = {s["metric"] for s in series}
    assert "nanorix.billing.current_spend_cents" not in metrics


# ── Core: Splunk transforms ───────────────────────────────────────────


def test_splunk_event_pass():
    event = auditproof_to_splunk_event(_proof_dict(), _VerifyResultOk())
    assert event["event_type"] == "auditproof_emitted"
    assert event["verification_outcome"] == "pass"
    assert event["failure_reason"] is None
    assert event["pattern_tag"] == "rcm_claim"


def test_splunk_event_fail_carries_reason_and_step():
    event = auditproof_to_splunk_event(_proof_dict(), _VerifyResultFail())
    assert event["verification_outcome"] == "fail"
    assert event["failure_reason"] == "signature_invalid"
    assert event["step_failed"] == 4


def test_splunk_cost_meter_event():
    event = cost_meter_to_splunk_event(_Usage(), _Forecast())
    assert event["event_type"] == "cost_meter_snapshot"
    assert event["auditproof_count"] == 42
    assert event["from"] == "2026-05-01"
    assert event["by_pattern"][0]["pattern_tag"] == "rcm_claim"
    assert event["forecasted_spend_cents"] == 4500


# ── Core: Honeycomb transform ─────────────────────────────────────────


def test_honeycomb_span_pass_status_ok():
    span = auditproof_to_honeycomb_span(_proof_dict(), _VerifyResultOk())
    assert span["name"] == "nanorix.auditproof.emitted"
    assert span["status_code"] == "OK"
    assert span["attributes"]["nanorix.capsule_id"] == "cap_a1b2c3d4"
    assert span["attributes"]["nanorix.verification_outcome"] == "pass"


def test_honeycomb_span_fail_status_error():
    span = auditproof_to_honeycomb_span(_proof_dict(), _VerifyResultFail())
    assert span["status_code"] == "ERROR"
    assert span["status_description"] == "signature_invalid"
    assert span["attributes"]["nanorix.failure_reason"] == "signature_invalid"
    assert span["attributes"]["nanorix.step_failed"] == 4


# ── Core: Sentry transform (failures only) ────────────────────────────


def test_sentry_event_none_when_verification_passes():
    assert auditproof_to_sentry_event(_proof_dict(), _VerifyResultOk()) is None


def test_sentry_event_none_when_no_verification():
    assert auditproof_to_sentry_event(_proof_dict(), None) is None


def test_sentry_event_on_failure():
    event = auditproof_to_sentry_event(_proof_dict(), _VerifyResultFail())
    assert event is not None
    assert event["level"] == "warning"
    assert "signature_invalid" in event["message"]
    assert event["tags"]["nanorix.failure_reason"] == "signature_invalid"
    assert event["context"]["step_failed"] == 4


# ── Invariant: no internal engine names in any emitted field ──────────

def _flatten_strings(payload):
    out = []
    if isinstance(payload, str):
        out.append(payload)
    elif isinstance(payload, dict):
        for k, v in payload.items():
            out.append(str(k))
            out.extend(_flatten_strings(v))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            out.extend(_flatten_strings(item))
    return out


def test_no_internal_engine_names_in_emitted_payloads(engine_names):
    payloads = [
        auditproof_to_datadog_event(_proof_dict(), _VerifyResultFail()),
        auditproof_to_splunk_event(_proof_dict(), _VerifyResultFail()),
        auditproof_to_honeycomb_span(_proof_dict(), _VerifyResultFail()),
        auditproof_to_sentry_event(_proof_dict(), _VerifyResultFail()),
        cost_meter_to_datadog_series(_Usage(), _Forecast()),
        cost_meter_to_splunk_event(_Usage(), _Forecast()),
    ]
    for payload in payloads:
        for token in _flatten_strings(payload):
            # Word-boundary check: pattern_tag values like "rcm_claim" are
            # fine; we only flag standalone engine identifiers.
            tokens = token.replace(".", " ").replace("_", " ").replace(":", " ").split()
            for engine in engine_names:
                assert engine not in tokens, f"engine name {engine} leaked in {token!r}"


# ── Shell: Datadog pusher (injected submit=) ──────────────────────────


def test_datadog_adapter_pushes_event_via_submit():
    sent = []
    adapter = DatadogObservabilityAdapter(
        submit=lambda path, body: sent.append((path, body)),
        env="prod",
    )
    event = adapter.send_auditproof(_proof_dict(), _VerifyResultFail())
    assert sent[0][0] == "/api/v2/logs"
    assert sent[0][1] == [event]
    assert event["status"] == "warn"


def test_datadog_adapter_pushes_cost_via_submit():
    sent = []
    adapter = DatadogObservabilityAdapter(
        submit=lambda path, body: sent.append((path, body)),
    )
    series = adapter.send_cost_meter(_Usage(), _Forecast())
    assert sent[0][0] == "/api/v2/series"
    assert sent[0][1] == {"series": series}


def test_datadog_adapter_requires_key_or_submit():
    with pytest.raises(ObservabilityError):
        DatadogObservabilityAdapter(api_key=None)


# ── Shell: Splunk pusher (injected submit=) ───────────────────────────


def test_splunk_adapter_wraps_hec_envelope():
    sent = []
    adapter = SplunkObservabilityAdapter(
        submit=lambda envelope: sent.append(envelope),
        index="myindex",
    )
    event = adapter.send_auditproof(_proof_dict(), _VerifyResultOk())
    envelope = sent[0]
    assert envelope["index"] == "myindex"
    assert envelope["sourcetype"] == "nanorix:auditproof"
    assert envelope["event"] == event


def test_splunk_adapter_cost_meter_sourcetype():
    sent = []
    adapter = SplunkObservabilityAdapter(submit=lambda e: sent.append(e))
    adapter.send_cost_meter(_Usage(), _Forecast())
    assert sent[0]["sourcetype"] == "nanorix:cost_meter"


def test_splunk_adapter_requires_url_token_or_submit():
    with pytest.raises(ObservabilityError):
        SplunkObservabilityAdapter(hec_url=None, hec_token=None)


# ── Shell: Honeycomb pusher (injected tracer=) ────────────────────────


def test_honeycomb_adapter_replays_span_onto_tracer():
    tracer = FakeTracer()
    adapter = HoneycombObservabilityAdapter(tracer=tracer)
    adapter.send_auditproof(_proof_dict(), _VerifyResultFail())
    span = tracer.spans[0]
    assert span.attributes["nanorix.capsule_id"] == "cap_a1b2c3d4"
    assert span.attributes["nanorix.verification_outcome"] == "fail"
    assert span.status == ("ERROR", "signature_invalid")


def test_honeycomb_adapter_pass_sets_ok_status():
    tracer = FakeTracer()
    adapter = HoneycombObservabilityAdapter(tracer=tracer)
    adapter.send_auditproof(_proof_dict(), _VerifyResultOk())
    assert tracer.spans[0].status == ("OK", None)


def test_honeycomb_adapter_requires_key_or_tracer():
    with pytest.raises(ObservabilityError):
        HoneycombObservabilityAdapter(ingest_key=None)


# ── Shell: Sentry pusher (injected capture=) ──────────────────────────


def test_sentry_adapter_emits_on_failure():
    captured = []
    adapter = SentryObservabilityAdapter(
        capture=lambda msg, level, tags, ctx: captured.append((msg, level, tags, ctx)),
    )
    result = adapter.send_auditproof(_proof_dict(), _VerifyResultFail())
    assert result is not None
    assert captured[0][1] == "warning"
    assert "signature_invalid" in captured[0][0]


def test_sentry_adapter_silent_on_pass():
    captured = []
    adapter = SentryObservabilityAdapter(capture=lambda *a: captured.append(a))
    result = adapter.send_auditproof(_proof_dict(), _VerifyResultOk())
    assert result is None
    assert captured == []


def test_sentry_adapter_requires_dsn_or_capture():
    with pytest.raises(ObservabilityError):
        SentryObservabilityAdapter(dsn=None)


# ── No-credential-retention (mirrors connectors' discipline) ──────────


def test_datadog_adapter_retains_no_credential_attribute():
    secret = "dd_super_secret_key_value"
    adapter = DatadogObservabilityAdapter(api_key=secret)
    adapter._post = lambda *a, **k: None  # neutralize real network
    # No attribute on the object equals the secret after construction.
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"


def test_splunk_adapter_retains_no_token_attribute():
    secret = "splunk_hec_secret_token"
    adapter = SplunkObservabilityAdapter(
        hec_url="https://hec.example:8088/services/collector",
        hec_token=secret,
    )
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"


def test_honeycomb_adapter_drops_ingest_key_after_tracer_build():
    # With an injected tracer the key is never stored at all — and after a
    # real push completes, no attribute on the object equals the secret.
    secret = "honeycomb_ingest_secret"
    adapter = HoneycombObservabilityAdapter(ingest_key=secret, tracer=FakeTracer())
    adapter.send_auditproof(_proof_dict(), _VerifyResultFail())
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"
    # The lazy private holder is cleared once the tracer is in place.
    assert getattr(adapter, "_ingest_key", None) is None


def test_sentry_adapter_drops_dsn_after_emit():
    # With an injected capture the DSN is never stored — and after a push
    # completes, no attribute on the object equals the secret.
    secret = "https://key@o0.ingest.sentry.io/0"
    adapter = SentryObservabilityAdapter(dsn=secret, capture=lambda *a: None)
    adapter.send_auditproof(_proof_dict(), _VerifyResultFail())
    for name in vars(adapter):
        assert getattr(adapter, name) != secret, f"secret leaked into {name}"
    assert getattr(adapter, "_dsn", None) is None

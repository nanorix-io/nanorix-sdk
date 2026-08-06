"""Honeycomb observability shell — guarded OTel-span pusher.

Forwards Nanorix AuditProof emission + verification outcomes to Honeycomb
as OpenTelemetry spans (OTLP). All shaping lives in
:mod:`nanorix.observability._core` (pure, vendor-SDK-free, dict-tested) as a
vendor-neutral span description; this module is a THIN pusher that REPLAYS
that description onto a real OTel span.

## Transport

A real OTel span requires the ``opentelemetry`` packages at runtime, so
this shell guards that import: it is imported lazily inside the push path
and raises a clear, actionable :class:`ImportError` only when the OTel
transport is actually needed and absent. The ``[honeycomb]`` extra declares
the OTLP exporter packages. A customer who manages their own tracer can
pass a ``tracer=`` so no OTel import happens in this module at all.

Critically, ``import nanorix.observability.honeycomb`` and importing the
core succeed with NO ``opentelemetry`` installed — the guard fires only on
use.

## Credential discipline

The customer supplies their Honeycomb ingest key (constructor arg or
``HONEYCOMB_INGEST_KEY`` env). It is passed to the OTLP exporter headers
and is **never** retained as a readable attribute on this object after the
tracer is built — mirroring the connectors' no-credential-retention
discipline.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from nanorix.observability._core import (
    ObservabilityError,
    assert_structural_facts_only,
    auditproof_to_honeycomb_span,
)

__all__ = ["HoneycombObservabilityAdapter"]

_DEFAULT_ENDPOINT = "https://api.honeycomb.io/v1/traces"
_DEFAULT_DATASET = "nanorix-auditproofs"
_DEFAULT_SERVICE = "nanorix-adapter"

_IMPORT_HINT = (
    "The Honeycomb adapter emits OpenTelemetry spans, which require the "
    "optional 'opentelemetry' packages. They are not installed. Install "
    "them with:\n\n    pip install nanorix[honeycomb]\n\n"
    "Alternatively, pass tracer=<your opentelemetry Tracer> so this adapter "
    "does not build one itself."
)


def _require_otel() -> Any:
    """Import the OTel trace API lazily; raise an actionable ImportError if absent."""
    try:
        from opentelemetry import trace
    except ImportError as err:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(_IMPORT_HINT) from err
    return trace


class HoneycombObservabilityAdapter:
    """Push Nanorix AuditProof events to Honeycomb as OTel spans.

    Args:
        ingest_key: The customer's Honeycomb ingest key. Falls back to
            ``HONEYCOMB_INGEST_KEY``. Passed to the OTLP exporter headers;
            never retained on this object after the tracer is built.
        dataset: The Honeycomb dataset spans land in. Falls back to
            ``HONEYCOMB_DATASET``.
        endpoint: The OTLP traces endpoint. Falls back to
            ``HONEYCOMB_ENDPOINT``; use the ``api.eu1.honeycomb.io`` host for
            EU residency.
        service_name: The ``service.name`` resource attribute.
        tracer: Optional pre-built OpenTelemetry ``Tracer``. When supplied no
            ``opentelemetry`` import happens in this module — the customer
            fully owns tracer configuration. In tests a fake tracer is
            passed so no OTel SDK is required.
    """

    def __init__(
        self,
        ingest_key: Optional[str] = None,
        *,
        dataset: Optional[str] = None,
        endpoint: Optional[str] = None,
        service_name: str = _DEFAULT_SERVICE,
        tracer: Optional[Any] = None,
    ) -> None:
        resolved_key = ingest_key or os.environ.get("HONEYCOMB_INGEST_KEY")
        if tracer is None and not resolved_key:
            raise ObservabilityError(
                "Honeycomb ingest key is required (pass ingest_key=... or set "
                "HONEYCOMB_INGEST_KEY), or supply a pre-built tracer=. Nanorix "
                "never stores your Honeycomb credential."
            )
        self.dataset = dataset or os.environ.get("HONEYCOMB_DATASET", _DEFAULT_DATASET)
        self.endpoint = endpoint or os.environ.get(
            "HONEYCOMB_ENDPOINT", _DEFAULT_ENDPOINT
        )
        self.service_name = service_name
        # If the customer passes a tracer we use it verbatim and import no
        # OTel here. Otherwise the tracer is built lazily on first send so
        # importing this module needs no opentelemetry. The ingest key is
        # consumed at build time and not stored as a readable attribute.
        self._tracer = tracer
        self._ingest_key = resolved_key if tracer is None else None
        self._tracer_built = tracer is not None

    def _ensure_tracer(self) -> Any:
        if self._tracer_built:
            return self._tracer
        _require_otel()
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
        )

        resource = Resource.create({"service.name": self.service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(
            endpoint=self.endpoint,
            headers={
                "x-honeycomb-team": self._ingest_key or "",
                "x-honeycomb-dataset": self.dataset,
            },
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        self._tracer = provider.get_tracer("nanorix.honeycomb-adapter")
        # No-credential-retention: the key has been handed to the exporter;
        # drop our reference so nothing reads it back off this object.
        self._ingest_key = None
        self._tracer_built = True
        return self._tracer

    def send_auditproof(self, proof: Any, verification: Any = None) -> Dict[str, Any]:
        """Emit one OTel span describing an AuditProof + verification outcome.

        Shapes the span via the core (STRUCTURAL FACTS only), then replays
        the description onto a real OTel span: sets every attribute and the
        span status (``OK`` on pass, ``ERROR`` on fail). Returns the
        vendor-neutral span-description dict.
        """
        span_desc = auditproof_to_honeycomb_span(proof, verification)
        assert_structural_facts_only(span_desc)
        tracer = self._ensure_tracer()
        self._replay_span(tracer, span_desc)
        return span_desc

    @staticmethod
    def _replay_span(tracer: Any, span_desc: Dict[str, Any]) -> None:
        """Replay a vendor-neutral span description onto a real OTel span."""
        from contextlib import contextmanager

        # Use the tracer's start_as_current_span; tolerate the fake tracer in
        # tests which returns a simple context manager.
        cm = tracer.start_as_current_span(span_desc["name"])
        if not hasattr(cm, "__enter__"):  # pragma: no cover - defensive
            @contextmanager
            def _noop() -> Any:
                yield None

            cm = _noop()
        with cm as span:
            if span is None:  # pragma: no cover - defensive
                return
            for key, value in span_desc["attributes"].items():
                span.set_attribute(key, value)
            status_code = span_desc.get("status_code")
            description = span_desc.get("status_description")
            _set_status(span, status_code, description)


def _set_status(span: Any, status_code: Optional[str], description: Optional[str]) -> None:
    """Set OTel span status, importing the status types lazily.

    Tolerates spans (test fakes) that expose a plain ``set_status_code``
    instead of the OTel ``Status``/``StatusCode`` API.
    """
    if hasattr(span, "set_status_code"):
        span.set_status_code(status_code, description)
        return
    try:
        from opentelemetry.trace import Status, StatusCode
    except ImportError:  # pragma: no cover - defensive
        return
    code = StatusCode.ERROR if status_code == "ERROR" else StatusCode.OK
    if description:
        span.set_status(Status(code, description))
    else:
        span.set_status(Status(code))

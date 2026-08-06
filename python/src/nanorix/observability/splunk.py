"""Splunk observability shell — guarded HEC pusher.

Forwards Nanorix telemetry to Splunk via HEC (HTTP Event Collector):
AuditProof emission + verification outcomes, and (optionally) a
billing-meter snapshot. All shaping lives in
:mod:`nanorix.observability._core` (pure, vendor-SDK-free, dict-tested);
this module is a THIN pusher.

## Transport

Splunk HEC is a plain HTTP endpoint — there is NO Splunk SDK to import.
This shell POSTs the HEC envelope via the SDK's own ``httpx``-based
transport, so no vendor dependency is required at all. (The ``[splunk]``
extra declares ``requests`` only for parity with the documented sample; it
is not imported here.)

## Credential discipline

The customer supplies their HEC token + URL (constructor args or
``SPLUNK_HEC_TOKEN`` / ``SPLUNK_HEC_URL`` env). The token authenticates the
POST and is **never** retained as a readable attribute on this object after
a push — mirroring the connectors' no-credential-retention discipline.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, Optional

from nanorix._http import HttpTransport
from nanorix.observability._core import (
    ObservabilityError,
    assert_structural_facts_only,
    auditproof_to_splunk_event,
    cost_meter_to_splunk_event,
)

__all__ = ["SplunkObservabilityAdapter"]

_DEFAULT_INDEX = "nanorix"
_DEFAULT_SOURCETYPE = "nanorix:auditproof"
_COST_SOURCETYPE = "nanorix:cost_meter"


class SplunkObservabilityAdapter:
    """Push Nanorix telemetry to Splunk via HEC.

    Args:
        hec_url: The HEC ingestion URL (e.g.
            ``https://splunk-hec.example.com:8088/services/collector``).
            Falls back to ``SPLUNK_HEC_URL``.
        hec_token: The HEC token. Falls back to ``SPLUNK_HEC_TOKEN``. Used
            only to authenticate each POST; never retained on this object.
        index: The Splunk index events land in. Falls back to
            ``SPLUNK_INDEX``; defaults to ``nanorix``.
        sourcetype: The default sourcetype for AuditProof events.
        submit: Optional callable ``(envelope: dict) -> None`` replacing the
            default HTTP transport (e.g. a ``requests``-based POST). When
            omitted, an httpx POST to the HEC URL is used.
    """

    def __init__(
        self,
        hec_url: Optional[str] = None,
        hec_token: Optional[str] = None,
        *,
        index: Optional[str] = None,
        sourcetype: str = _DEFAULT_SOURCETYPE,
        submit: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        resolved_url = hec_url or os.environ.get("SPLUNK_HEC_URL")
        resolved_token = hec_token or os.environ.get("SPLUNK_HEC_TOKEN")
        if submit is None and (not resolved_url or not resolved_token):
            raise ObservabilityError(
                "Splunk HEC URL + token are required (pass hec_url=/hec_token= "
                "or set SPLUNK_HEC_URL / SPLUNK_HEC_TOKEN), or supply a custom "
                "submit= callable. Nanorix never stores your HEC token."
            )
        self.hec_url = resolved_url
        self.index = index or os.environ.get("SPLUNK_INDEX", _DEFAULT_INDEX)
        self.sourcetype = sourcetype
        self._submit = submit
        # No-credential-retention: the token is captured by the default
        # transport closure, not stored as a readable attribute.
        self._post_hec = (
            self._build_transport(resolved_url, resolved_token)
            if submit is None
            else None
        )

    def _build_transport(
        self, hec_url: Optional[str], hec_token: Optional[str]
    ) -> Callable[[Dict[str, Any]], None]:
        def _post(envelope: Dict[str, Any]) -> None:
            transport = HttpTransport(api_key="unused", base_url=hec_url or "")
            try:
                transport.request(
                    "POST",
                    "",
                    json=envelope,
                    headers={"Authorization": f"Splunk {hec_token or ''}"},
                )
            finally:
                transport.close()

        return _post

    def send_auditproof(self, proof: Any, verification: Any = None) -> Dict[str, Any]:
        """Forward one AuditProof + verification outcome to Splunk HEC.

        Returns the inner event dict (the HEC envelope wrapping it is also
        gated for STRUCTURAL FACTS before the POST).
        """
        event = auditproof_to_splunk_event(proof, verification)
        self._send(event, self.sourcetype)
        return event

    def send_cost_meter(self, usage: Any, forecast: Any = None) -> Dict[str, Any]:
        """Forward a billing-meter snapshot to Splunk HEC.

        Returns the inner event dict.
        """
        event = cost_meter_to_splunk_event(usage, forecast)
        self._send(event, _COST_SOURCETYPE)
        return event

    def _send(self, event: Dict[str, Any], sourcetype: str) -> None:
        """Wrap the event in the HEC envelope and hand it to the transport."""
        envelope = {
            "time": time.time(),
            "host": "nanorix-adapter",
            "source": "nanorix-adapter",
            "sourcetype": sourcetype,
            "index": self.index,
            "event": event,
        }
        assert_structural_facts_only(envelope)
        if self._submit is not None:
            self._submit(envelope)
            return
        assert self._post_hec is not None
        self._post_hec(envelope)

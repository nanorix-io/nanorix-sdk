"""Datadog observability shell — guarded pusher.

Forwards Nanorix telemetry to Datadog: AuditProof emission + verification
outcomes as Datadog Logs events, and billing-meter usage + cost forecast
as Datadog custom metrics. All shaping lives in
:mod:`nanorix.observability._core` (pure, vendor-SDK-free, dict-tested);
this module is a THIN pusher.

## Transport

By default this shell POSTs to Datadog's HTTP intake using the SDK's own
``httpx``-based transport — so **no Datadog SDK is required**. The
``datadog`` / ``datadog-api-client`` packages are declared as the
``[datadog]`` extra but are NOT imported by the default path; a customer
who prefers the dogstatsd / ``datadog-api-client`` route can pass a
pre-built submit callable.

## Credential discipline

The customer supplies their Datadog API key (constructor arg or
``DD_API_KEY`` env). It is used only to authenticate the call and is
**never** retained as an attribute on this object after a push — mirroring
the connectors' no-credential-retention discipline. The Nanorix API key
lives in the customer's :class:`nanorix.Client`, not here.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from nanorix._http import HttpTransport
from nanorix.observability._core import (
    ObservabilityError,
    assert_structural_facts_only,
    auditproof_to_datadog_event,
    cost_meter_to_datadog_series,
)

__all__ = ["DatadogObservabilityAdapter"]

# Datadog site -> intake host. The customer's account region MUST match;
# cross-region intake is rejected by Datadog.
_DEFAULT_SITE = "datadoghq.com"

_IMPORT_HINT = (
    "The optional 'datadog' / 'datadog-api-client' packages are not "
    "installed. They are NOT required for the default HTTP-intake path "
    "(this adapter POSTs via the Nanorix SDK's own httpx transport). "
    "Install them only if you want the dogstatsd / datadog-api-client "
    "route:\n\n    pip install nanorix[datadog]\n"
)


def _require_datadog() -> Any:
    """Import the datadog package lazily; raise an actionable hint if absent.

    Only the optional dogstatsd path calls this; the default HTTP-intake
    path needs no vendor SDK.
    """
    try:
        import datadog
    except ImportError as err:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(_IMPORT_HINT) from err
    return datadog


class DatadogObservabilityAdapter:
    """Push Nanorix telemetry to Datadog (Logs events + custom metrics).

    Args:
        api_key: The customer's Datadog API key. Falls back to ``DD_API_KEY``
            from the environment. Used only to authenticate each push; never
            retained on this object after a call.
        site: The Datadog site (``datadoghq.com`` / ``datadoghq.eu`` /
            ``us3.datadoghq.com`` / ...). Falls back to ``DD_SITE``.
        env: The deployment-environment tag value applied to emitted events
            and metrics.
        submit: Optional callable ``(endpoint: str, body: dict) -> None`` that
            replaces the default HTTP-intake transport (e.g. a thin wrapper
            over ``datadog-api-client``). When omitted, an httpx POST to the
            Datadog intake host is used and no vendor SDK is required.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        site: Optional[str] = None,
        env: str = "production",
        submit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        resolved_key = api_key or os.environ.get("DD_API_KEY")
        if not resolved_key and submit is None:
            raise ObservabilityError(
                "Datadog API key is required (pass api_key=... or set "
                "DD_API_KEY), or supply a custom submit= callable. Nanorix "
                "never stores your Datadog credential."
            )
        self.site = site or os.environ.get("DD_SITE", _DEFAULT_SITE)
        self.env = env
        self._submit = submit
        # No-credential-retention: the key is captured by the closure the
        # default transport uses to build request headers, NOT stored as a
        # plain attribute. After a push nothing on this object reads it back;
        # the no-credential-retention test asserts no attribute equals it.
        self._intake = (
            self._build_intake(resolved_key) if submit is None else None
        )

    def _build_intake(
        self, api_key: Optional[str]
    ) -> Callable[[str, str, Any], None]:
        site = self.site

        def _post(subdomain: str, path: str, body: Any) -> None:
            transport = HttpTransport(
                api_key="unused",  # Datadog auth is via the DD-API-KEY header.
                base_url=f"https://{subdomain}.{site}",
            )
            try:
                transport.request(
                    "POST",
                    path,
                    json=body,
                    headers={"DD-API-KEY": api_key or ""},
                )
            finally:
                transport.close()

        return _post

    def send_auditproof(self, proof: Any, verification: Any = None) -> Dict[str, Any]:
        """Forward one AuditProof + verification outcome to Datadog Logs.

        Shapes the event via the core (STRUCTURAL FACTS only), then POSTs it
        to the Datadog Logs intake (a bare JSON array of log items). Returns
        the emitted event dict.
        """
        event = auditproof_to_datadog_event(proof, verification, env=self.env)
        self._post("http-intake.logs", "/api/v2/logs", [event])
        return event

    def send_cost_meter(self, usage: Any, forecast: Any = None) -> List[Dict[str, Any]]:
        """Forward billing-meter usage (+ optional forecast) as DD metrics.

        Shapes the metric series via the core, then POSTs them to the
        Datadog metrics intake (``{"series": [...]}``). Returns the emitted
        series list.
        """
        series = cost_meter_to_datadog_series(usage, forecast, env=self.env)
        self._post("api", "/api/v2/series", {"series": series})
        return series

    def _post(self, subdomain: str, path: str, body: Any) -> None:
        """Hand the shaped payload to the configured transport.

        The structural-facts gate runs once more here as defense-in-depth
        before anything leaves the process. A custom ``submit=`` callable
        (e.g. wrapping ``datadog-api-client``) receives ``(path, body)``;
        the default httpx transport POSTs to the Datadog intake host.
        """
        assert_structural_facts_only(body)
        if self._submit is not None:
            self._submit(path, body)
            return
        assert self._intake is not None
        self._intake(subdomain, path, body)

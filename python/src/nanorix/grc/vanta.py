"""Vanta GRC shell — guarded evidence-filing pusher (Batch 17).

Files the signed AuditProof — per-record receipts (ADR-039) + outer
envelope — into Vanta as compliance EVIDENCE, landed against the canonical
control IDs the ADR-040 mapping resolves at adapter time. All mapping +
evidence-shaping lives in :mod:`nanorix.grc._core` (pure, vendor-SDK-free,
dict-tested); this module is a THIN pusher.

## Transport

Vanta's evidence API is a plain HTTP REST surface (Bearer-token auth). This
shell POSTs via the SDK's own ``httpx``-based transport, so **no Vanta SDK
is required**. The ``[vanta]`` extra declares ``vanta-api`` for parity with
the documented sample; it is not imported here. A customer who prefers the
``vanta-api`` SDK can pass a ``post=`` callable.

## Narrow-claim discipline (NON-NEGOTIABLE)

Every evidence body the core builds carries ``"relationship": "references"``
+ the ADR-040 mapping disclaimer, and is run through the reference-only gate
(no ``COMPLIANT`` / ``SATISFIED`` / ``PASSED`` / ``MEETS``). Nanorix files a
factual reference; the auditor adjudicates (ADR-017 three-role).

## Credential discipline (ADR-013)

The customer supplies their Vanta token + workspace id (constructor args or
``VANTA_API_TOKEN`` / ``VANTA_WORKSPACE_ID`` env). The token authenticates
each POST and is **never** retained as a readable attribute on this object
after a call — mirroring the connectors' no-credential-retention discipline.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, cast

from nanorix._http import HttpTransport
from nanorix.control_map import Mapping as ControlMapping
from nanorix.grc._core import (
    GrcError,
    assert_reference_only,
    auditproof_to_evidence_bodies,
)

__all__ = ["VantaEvidenceAdapter"]

_DEFAULT_BASE = "https://api.vanta.com/v1"


class VantaEvidenceAdapter:
    """File Nanorix AuditProof receipts into Vanta as evidence.

    Args:
        token: The customer's Vanta API token (Bearer). Falls back to
            ``VANTA_API_TOKEN``. Used only to authenticate each POST; never
            retained on this object after a call.
        workspace_id: The target Vanta workspace id. Falls back to
            ``VANTA_WORKSPACE_ID``.
        framework_version: The ADR-040 mapping framework version to pin for
            this audit period (e.g. ``"2026-02"``). Defaults to the current
            published version. Ignored when ``mapping`` is supplied.
        mapping: Optional pre-loaded :class:`nanorix.control_map.Mapping`
            (recommended — load once at startup; the SDK embeds the mapping
            so no network call is needed).
        base_url: The Vanta API base URL. Falls back to ``VANTA_BASE_URL``.
        post: Optional callable ``(path: str, body: dict) -> dict`` replacing
            the default HTTP transport (e.g. a ``vanta-api`` SDK call). When
            omitted, an httpx POST is used and no vendor SDK is required.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        workspace_id: Optional[str] = None,
        *,
        framework_version: Optional[str] = None,
        mapping: Optional[ControlMapping] = None,
        base_url: Optional[str] = None,
        post: Optional[Callable[[str, Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> None:
        resolved_token = token or os.environ.get("VANTA_API_TOKEN")
        resolved_workspace = workspace_id or os.environ.get("VANTA_WORKSPACE_ID")
        if post is None and (not resolved_token or not resolved_workspace):
            raise GrcError(
                "Vanta token + workspace id are required (pass token=/"
                "workspace_id= or set VANTA_API_TOKEN / VANTA_WORKSPACE_ID), "
                "or supply a custom post= callable. Nanorix never stores your "
                "Vanta credential."
            )
        self.workspace_id = resolved_workspace
        self.framework_version = framework_version
        self.mapping = mapping
        self.base_url = base_url or os.environ.get("VANTA_BASE_URL", _DEFAULT_BASE)
        self._post = post
        # No-credential-retention: the token is captured by the default
        # transport closure, not stored as a readable attribute.
        self._post_http = (
            self._build_transport(resolved_token) if post is None else None
        )

    def _build_transport(self, token: Optional[str]) -> Callable[[str, Dict[str, Any]], Dict[str, Any]]:
        base = self.base_url

        def _post(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
            transport = HttpTransport(api_key="unused", base_url=base)
            try:
                return cast(Dict[str, Any], transport.request(
                                         "POST",
                                         path,
                                         json=body,
                                         headers={"Authorization": f"Bearer {token or ''}"},
                                     ))
            finally:
                transport.close()

        return _post

    def file_auditproof(
        self,
        proof: Any,
        *,
        jurisdiction: str,
        classification: str,
        additional_control_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """File every receipt in an AuditProof to Vanta as evidence.

        The core resolves the control IDs each receipt references (ADR-040
        mapping, applied at adapter time) and shapes one evidence body per
        ``(receipt × control_id)``; this shell POSTs each to Vanta's
        evidence API. Every body carries ``relationship: "references"`` + the
        mapping disclaimer (reference-only discipline).

        Args:
            proof: The AuditProof / FullCdp (dict or model).
            jurisdiction: Customer-declared jurisdiction (read verbatim; ADR-013).
            classification: Customer-declared data_classification (read verbatim).
            additional_control_ids: Optional customer-namespace control IDs
                appended to every receipt's resolved set.

        Returns:
            The list of Vanta evidence-filing confirmations (one per body).
        """
        bodies = auditproof_to_evidence_bodies(
            proof,
            jurisdiction=jurisdiction,
            classification=classification,
            framework_version=self.framework_version,
            mapping=self.mapping,
            additional_control_ids=additional_control_ids,
            extra_fields={"workspace_id": self.workspace_id},
        )
        path = f"/workspaces/{self.workspace_id}/evidence"
        confirmations: List[Dict[str, Any]] = []
        for body in bodies:
            assert_reference_only(body)
            confirmations.append(self._send(path, body))
        return confirmations

    def _send(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        if self._post is not None:
            return self._post(path, body)
        assert self._post_http is not None
        return self._post_http(path, body)

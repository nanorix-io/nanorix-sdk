"""Egress profiles resource — sync and async implementations."""
from __future__ import annotations
from typing import Any, Dict, List, Optional, cast

from nanorix._http import AsyncHttpTransport, HttpTransport


class EgressProfiles:
    """Sync egress profile operations."""

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def create(
        self,
        name: str,
        *,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a named egress profile. Business+ only."""
        body: Dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], self._http.request("POST", "/v1/egress/profiles", json=body))

    def list(self) -> List[Dict[str, Any]]:
        """List all egress profiles for the account."""
        return cast(List[Dict[str, Any]], self._http.request("GET", "/v1/egress/profiles"))

    def get(self, profile_id: str) -> Dict[str, Any]:
        """Get a profile with its rules."""
        return cast(Dict[str, Any], self._http.request("GET", f"/v1/egress/profiles/{profile_id}"))

    def update(
        self,
        profile_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a profile's name or description."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], self._http.request("PUT", f"/v1/egress/profiles/{profile_id}", json=body))

    def delete(self, profile_id: str) -> None:
        """Delete a profile. Fails if active capsules reference it."""
        self._http.request("DELETE", f"/v1/egress/profiles/{profile_id}")

    def add_rule(
        self,
        profile_id: str,
        domain_pattern: str,
        *,
        port: int = 443,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a rule to a profile."""
        body: Dict[str, Any] = {"domain_pattern": domain_pattern, "port": port}
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], self._http.request(
                                 "POST", f"/v1/egress/profiles/{profile_id}/rules", json=body
                             ))

    def list_rules(self, profile_id: str) -> List[Dict[str, Any]]:
        """List rules in a profile."""
        return cast(List[Dict[str, Any]], self._http.request("GET", f"/v1/egress/profiles/{profile_id}/rules"))

    def remove_rule(self, profile_id: str, rule_id: str) -> None:
        """Remove a rule from a profile."""
        self._http.request(
            "DELETE", f"/v1/egress/profiles/{profile_id}/rules/{rule_id}"
        )


class AsyncEgressProfiles:
    """Async egress profile operations."""

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def create(
        self,
        name: str,
        *,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a named egress profile. Business+ only."""
        body: Dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], await self._http.request("POST", "/v1/egress/profiles", json=body))

    async def list(self) -> List[Dict[str, Any]]:
        """List all egress profiles for the account."""
        return cast(List[Dict[str, Any]], await self._http.request("GET", "/v1/egress/profiles"))

    async def get(self, profile_id: str) -> Dict[str, Any]:
        """Get a profile with its rules."""
        return cast(Dict[str, Any], await self._http.request("GET", f"/v1/egress/profiles/{profile_id}"))

    async def update(
        self,
        profile_id: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a profile's name or description."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], await self._http.request(
                                 "PUT", f"/v1/egress/profiles/{profile_id}", json=body
                             ))

    async def delete(self, profile_id: str) -> None:
        """Delete a profile. Fails if active capsules reference it."""
        await self._http.request("DELETE", f"/v1/egress/profiles/{profile_id}")

    async def add_rule(
        self,
        profile_id: str,
        domain_pattern: str,
        *,
        port: int = 443,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a rule to a profile."""
        body: Dict[str, Any] = {"domain_pattern": domain_pattern, "port": port}
        if description is not None:
            body["description"] = description
        return cast(Dict[str, Any], await self._http.request(
                                 "POST", f"/v1/egress/profiles/{profile_id}/rules", json=body
                             ))

    async def list_rules(self, profile_id: str) -> List[Dict[str, Any]]:
        """List rules in a profile."""
        return cast(List[Dict[str, Any]], await self._http.request(
                                       "GET", f"/v1/egress/profiles/{profile_id}/rules"
                                   ))

    async def remove_rule(self, profile_id: str, rule_id: str) -> None:
        """Remove a rule from a profile."""
        await self._http.request(
            "DELETE", f"/v1/egress/profiles/{profile_id}/rules/{rule_id}"
        )

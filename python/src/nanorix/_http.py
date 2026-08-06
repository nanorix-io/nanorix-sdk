"""
HTTP transport layer.

Handles:
- Sync and async clients from one codebase
- Bearer token auth on every request
- User-Agent header
- Automatic retry with exponential backoff on 429 and 5xx
- JSON parsing with error raising
- File upload (raw body, not multipart — matches API design)

API endpoints (VERIFIED against actual Rust handlers):
  POST   /v1/capsulefiles                      -> Create capsulefile metadata
  POST   /v1/capsulefiles/:id/files?filename=  -> Upload capsulefile file (raw body)
  POST   /v1/capsulefiles/:id/finalize         -> Finalize capsulefile
  GET    /v1/capsulefiles                      -> List capsulefiles
  GET    /v1/capsulefiles/:id                  -> Get capsulefile
  DELETE /v1/capsulefiles/:id                  -> Delete capsulefile
  POST   /v1/capsules                       -> Create capsule
  POST   /v1/capsules/:id/upload?filename=  -> Upload file to capsule (raw body)
  POST   /v1/capsules/:id/exec              -> Execute command
  GET    /v1/capsules/:id/output            -> List output files
  GET    /v1/capsules/:id/output/:filename  -> Download output file
  DELETE /v1/capsules/:id                   -> Destroy capsule -> returns CDP
  GET    /v1/capsules/:id/cdp               -> AuditRecord (auth required)
  GET    /v1/capsules/:id/cdp/verify        -> AuditProof (public)
  GET    /v1/capsules                       -> List capsules
  GET    /v1/capsules/:id                   -> Get capsule
"""
import time
import random
from typing import Any, Dict, Optional

import httpx

from nanorix._constants import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    MAX_RETRIES,
    RETRY_STATUS_CODES,
    USER_AGENT,
)
from nanorix._exceptions import raise_for_status, NanorixError


class HttpTransport:
    """Synchronous HTTP transport."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": USER_AGENT,
            },
            timeout=httpx.Timeout(timeout),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        raw_response: bool = False,
    ) -> Any:
        """Make an HTTP request with retry logic."""
        merged_headers = dict(headers or {})

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(
                    method,
                    path,
                    json=json,
                    content=data,
                    headers=merged_headers,
                    params=params,
                    timeout=timeout or self._timeout,
                )

                if response.status_code in RETRY_STATUS_CODES and attempt < self._max_retries:
                    self._backoff(attempt, response)
                    continue

                if raw_response:
                    if response.status_code >= 400:
                        try:
                            body = response.json()
                        except Exception:
                            body = {"error": response.text}
                        raise_for_status(response.status_code, body)
                    return response.content

                if response.status_code == 204:
                    return None

                try:
                    body = response.json()
                except Exception:
                    body = {"error": response.text}

                raise_for_status(response.status_code, body)
                return body

            except httpx.TimeoutException:
                if attempt < self._max_retries:
                    self._backoff(attempt)
                    continue
                raise NanorixError(
                    f"Request timed out after {timeout or self._timeout}s",
                    status_code=None,
                )
            except NanorixError:
                raise
            except httpx.HTTPError as e:
                if attempt < self._max_retries:
                    self._backoff(attempt)
                    continue
                raise NanorixError(f"HTTP error: {e}", status_code=None)

        raise NanorixError("Max retries exceeded", status_code=None)

    def _backoff(self, attempt: int, response: Optional[httpx.Response] = None) -> None:
        """Exponential backoff with jitter."""
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                time.sleep(float(retry_after))
                return
        base = 0.5 * (2 ** attempt)
        jitter = random.uniform(0, base * 0.5)
        time.sleep(base + jitter)

    def close(self) -> None:
        self._client.close()


class AsyncHttpTransport:
    """Asynchronous HTTP transport. Same logic as HttpTransport but async."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": USER_AGENT,
            },
            timeout=httpx.Timeout(timeout),
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
        raw_response: bool = False,
    ) -> Any:
        """Make an async HTTP request with retry logic."""
        merged_headers = dict(headers or {})

        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    json=json,
                    content=data,
                    headers=merged_headers,
                    params=params,
                    timeout=timeout or self._timeout,
                )

                if response.status_code in RETRY_STATUS_CODES and attempt < self._max_retries:
                    await self._async_backoff(attempt, response)
                    continue

                if raw_response:
                    if response.status_code >= 400:
                        try:
                            body = response.json()
                        except Exception:
                            body = {"error": response.text}
                        raise_for_status(response.status_code, body)
                    return response.content

                if response.status_code == 204:
                    return None

                try:
                    body = response.json()
                except Exception:
                    body = {"error": response.text}

                raise_for_status(response.status_code, body)
                return body

            except httpx.TimeoutException:
                if attempt < self._max_retries:
                    await self._async_backoff(attempt)
                    continue
                raise NanorixError(
                    f"Request timed out after {timeout or self._timeout}s",
                    status_code=None,
                )
            except NanorixError:
                raise
            except httpx.HTTPError as e:
                if attempt < self._max_retries:
                    await self._async_backoff(attempt)
                    continue
                raise NanorixError(f"HTTP error: {e}", status_code=None)

        raise NanorixError("Max retries exceeded", status_code=None)

    async def _async_backoff(
        self, attempt: int, response: Optional[httpx.Response] = None
    ) -> None:
        import asyncio
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                await asyncio.sleep(float(retry_after))
                return
        base = 0.5 * (2 ** attempt)
        jitter = random.uniform(0, base * 0.5)
        await asyncio.sleep(base + jitter)

    async def close(self) -> None:
        await self._client.aclose()

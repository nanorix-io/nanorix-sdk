"""Capsulefiles resource — sync and async implementations."""
from __future__ import annotations
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Union

from nanorix._constants import DEFAULT_UPLOAD_TIMEOUT
from nanorix._exceptions import NanorixError
from nanorix._http import AsyncHttpTransport, HttpTransport
from nanorix._models import Capsulefile, CapsulefileList


class Capsulefiles:
    """Synchronous capsulefile operations."""

    def __init__(self, http: HttpTransport) -> None:
        self._http = http

    def create(
        self,
        *,
        name: str,
        runtime: str,
        entrypoint: str,
        files: Union[List[str], Dict[str, bytes], Dict[str, BinaryIO]],
        description: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
        capsule_memory_mb: Optional[int] = None,
        input_path: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Capsulefile:
        """
        Create a capsulefile with files. Handles the full three-step flow:
        1. POST /v1/capsulefiles — create capsulefile metadata
        2. POST /v1/capsulefiles/:id/files?filename= — upload each file (raw body)
        3. POST /v1/capsulefiles/:id/finalize — finalize capsulefile

        ``files`` accepts:
        - List[str]: file paths on disk (reads and uploads each)
        - Dict[str, bytes]: filename -> content bytes
        - Dict[str, BinaryIO]: filename -> file-like object
        """
        body: Dict[str, Any] = {"name": name, "runtime": runtime, "entrypoint": entrypoint}
        if description is not None:
            body["description"] = description
        if labels is not None:
            body["labels"] = labels
        if env_vars is not None:
            body["env_vars"] = env_vars
        if capsule_memory_mb is not None:
            body["capsule_memory_mb"] = capsule_memory_mb
        if input_path is not None:
            body["input_path"] = input_path
        if output_path is not None:
            body["output_path"] = output_path

        result = self._http.request("POST", "/v1/capsulefiles", json=body)
        capsulefile_id = result["id"]

        # Step 2: Upload each file (raw body, not multipart)
        file_map = self._resolve_files(files)
        for filename, content in file_map.items():
            self._http.request(
                "POST",
                f"/v1/capsulefiles/{capsulefile_id}/files",
                data=content,
                headers={"Content-Type": "application/octet-stream"},
                params={"filename": filename},
                timeout=DEFAULT_UPLOAD_TIMEOUT,
            )

        # Step 3: Finalize
        result = self._http.request("POST", f"/v1/capsulefiles/{capsulefile_id}/finalize")
        return Capsulefile.model_validate(result)

    def list(self, **kwargs: Any) -> CapsulefileList:
        """List capsulefiles. Supports ?status=finalized, ?cursor=, ?limit= query params."""
        result = self._http.request("GET", "/v1/capsulefiles", params=kwargs or None)
        return CapsulefileList.model_validate(result)

    def get(self, capsulefile_id: str) -> Capsulefile:
        """Get a capsulefile by ID."""
        result = self._http.request("GET", f"/v1/capsulefiles/{capsulefile_id}")
        return Capsulefile.model_validate(result)

    def delete(self, capsulefile_id: str) -> None:
        """Delete a capsulefile (crypto-shreds encryption key, deletes files)."""
        self._http.request("DELETE", f"/v1/capsulefiles/{capsulefile_id}")

    def update(self, capsulefile_id: str, **kwargs: Any) -> Capsulefile:
        """Update a capsulefile. Not yet implemented."""
        raise NotImplementedError(
            "PUT /v1/capsulefiles/:id not yet implemented. Coming in v1.1."
        )

    def wait_until_finalized(
        self,
        capsulefile_id: str,
        *,
        timeout: float = 120.0,
        poll_interval: float = 2.0,
    ) -> Capsulefile:
        """Poll capsulefile status until 'finalized' or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cf = self.get(capsulefile_id)
            if cf.status == "finalized":
                return cf
            if cf.status == "failed":
                raise NanorixError(
                    f"Capsulefile build failed: {cf.error_message}",
                    status_code=None,
                )
            time.sleep(poll_interval)
        raise NanorixError(
            f"Capsulefile {capsulefile_id} not finalized after {timeout}s",
            status_code=None,
        )

    @staticmethod
    def _resolve_files(
        files: Union[List[str], Dict[str, bytes], Dict[str, BinaryIO]],
    ) -> Dict[str, bytes]:
        if isinstance(files, list):
            result: Dict[str, bytes] = {}
            for path_str in files:
                p = Path(path_str)
                result[p.name] = p.read_bytes()
            return result
        elif isinstance(files, dict):
            result = {}
            for name, content in files.items():
                if isinstance(content, bytes):
                    result[name] = content
                else:
                    result[name] = content.read()
            return result
        raise TypeError(
            f"files must be list, dict[str,bytes], or dict[str,BinaryIO], got {type(files)}"
        )

    def upload_dir(self, capsulefile_id: str, dir_path: str) -> int:
        """
        Upload all files in a directory (recursively) to a capsulefile.

        Walks the directory tree and uploads each file via
        ``POST /v1/capsulefiles/:id/files?filename=<relative_path>`` with raw bytes.

        Args:
            capsulefile_id: The capsulefile to upload into.
            dir_path: Path to the directory on disk.

        Returns:
            The number of files uploaded.
        """
        root = Path(dir_path)
        if not root.is_dir():
            raise NanorixError(f"Not a directory: {dir_path}", status_code=None)

        count = 0
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(root).as_posix()
            content = file_path.read_bytes()
            self._http.request(
                "POST",
                f"/v1/capsulefiles/{capsulefile_id}/files",
                data=content,
                headers={"Content-Type": "application/octet-stream"},
                params={"filename": relative},
                timeout=DEFAULT_UPLOAD_TIMEOUT,
            )
            count += 1
        return count


class AsyncCapsulefiles:
    """Asynchronous capsulefile operations."""

    def __init__(self, http: AsyncHttpTransport) -> None:
        self._http = http

    async def create(
        self,
        *,
        name: str,
        runtime: str,
        entrypoint: str,
        files: Union[List[str], Dict[str, bytes], Dict[str, BinaryIO]],
        description: Optional[str] = None,
        labels: Optional[Dict[str, str]] = None,
        env_vars: Optional[Dict[str, str]] = None,
        capsule_memory_mb: Optional[int] = None,
        input_path: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Capsulefile:
        """Create a capsulefile with files (async). Same three-step flow as sync."""
        body: Dict[str, Any] = {"name": name, "runtime": runtime, "entrypoint": entrypoint}
        if description is not None:
            body["description"] = description
        if labels is not None:
            body["labels"] = labels
        if env_vars is not None:
            body["env_vars"] = env_vars
        if capsule_memory_mb is not None:
            body["capsule_memory_mb"] = capsule_memory_mb
        if input_path is not None:
            body["input_path"] = input_path
        if output_path is not None:
            body["output_path"] = output_path

        result = await self._http.request("POST", "/v1/capsulefiles", json=body)
        capsulefile_id = result["id"]

        file_map = Capsulefiles._resolve_files(files)
        for filename, content in file_map.items():
            await self._http.request(
                "POST",
                f"/v1/capsulefiles/{capsulefile_id}/files",
                data=content,
                headers={"Content-Type": "application/octet-stream"},
                params={"filename": filename},
                timeout=DEFAULT_UPLOAD_TIMEOUT,
            )

        result = await self._http.request("POST", f"/v1/capsulefiles/{capsulefile_id}/finalize")
        return Capsulefile.model_validate(result)

    async def list(self, **kwargs: Any) -> CapsulefileList:
        """List capsulefiles (async)."""
        result = await self._http.request("GET", "/v1/capsulefiles", params=kwargs or None)
        return CapsulefileList.model_validate(result)

    async def get(self, capsulefile_id: str) -> Capsulefile:
        """Get a capsulefile by ID (async)."""
        result = await self._http.request("GET", f"/v1/capsulefiles/{capsulefile_id}")
        return Capsulefile.model_validate(result)

    async def delete(self, capsulefile_id: str) -> None:
        """Delete a capsulefile (async)."""
        await self._http.request("DELETE", f"/v1/capsulefiles/{capsulefile_id}")

    async def update(self, capsulefile_id: str, **kwargs: Any) -> Capsulefile:
        """Update a capsulefile. Not yet implemented."""
        raise NotImplementedError(
            "PUT /v1/capsulefiles/:id not yet implemented. Coming in v1.1."
        )

    async def wait_until_finalized(
        self,
        capsulefile_id: str,
        *,
        timeout: float = 120.0,
        poll_interval: float = 2.0,
    ) -> Capsulefile:
        """Poll capsulefile status until 'finalized' or timeout (async)."""
        import asyncio
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cf = await self.get(capsulefile_id)
            if cf.status == "finalized":
                return cf
            if cf.status == "failed":
                raise NanorixError(
                    f"Capsulefile build failed: {cf.error_message}",
                    status_code=None,
                )
            await asyncio.sleep(poll_interval)
        raise NanorixError(
            f"Capsulefile {capsulefile_id} not finalized after {timeout}s",
            status_code=None,
        )

    async def upload_dir(self, capsulefile_id: str, dir_path: str) -> int:
        """
        Upload all files in a directory (recursively) to a capsulefile (async).

        Walks the directory tree and uploads each file via
        ``POST /v1/capsulefiles/:id/files?filename=<relative_path>`` with raw bytes.

        Args:
            capsulefile_id: The capsulefile to upload into.
            dir_path: Path to the directory on disk.

        Returns:
            The number of files uploaded.
        """
        root = Path(dir_path)
        if not root.is_dir():
            raise NanorixError(f"Not a directory: {dir_path}", status_code=None)

        count = 0
        for file_path in sorted(root.rglob("*")):
            if not file_path.is_file():
                continue
            relative = file_path.relative_to(root).as_posix()
            content = file_path.read_bytes()
            await self._http.request(
                "POST",
                f"/v1/capsulefiles/{capsulefile_id}/files",
                data=content,
                headers={"Content-Type": "application/octet-stream"},
                params={"filename": relative},
                timeout=DEFAULT_UPLOAD_TIMEOUT,
            )
            count += 1
        return count

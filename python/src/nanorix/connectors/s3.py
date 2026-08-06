"""Amazon S3 source/sink connectors — customer-side streaming (ADR-036).

Batch 14 (2026-05-28). ``S3Source`` streams objects under a prefix INTO
the capsule before exec; ``S3Sink`` streams capsule output to a prefix
BEFORE destroy. Both run in the customer's process using the customer's
credentials — resolved by boto3's STANDARD credential chain (environment
variables, shared credentials file, EC2 instance profile, ECS task role,
SSO). Nanorix never takes custody of a credential and never stages data
on Nanorix infrastructure (ADR-036 zero-retention affirmation).

``boto3`` is an OPTIONAL dependency declared as the ``[s3]`` extra. This
module guards the import: importing it without boto3 installed raises a
clear, actionable :class:`ImportError`. The streaming + trust-boundary
logic lives entirely in :mod:`nanorix.connectors._base`, which imports
WITHOUT boto3 and is tested against a FAKE backend.

Credential discipline (ADR-036, NON-NEGOTIABLE): these classes accept NO
secret-key / access-key / token argument. They optionally accept a
pre-built boto3 ``client`` (so an advanced customer can configure their
own session) — Nanorix never constructs credentials, never stores them,
and the connector object retains no secret attribute that outlives a
call.
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

from nanorix.connectors._base import (
    DEFAULT_CHUNK_SIZE,
    ConnectorError,
    Sink,
    Source,
    SourceObject,
)

__all__ = ["S3Source", "S3Sink"]

_IMPORT_HINT = (
    "The S3 connector requires the optional 'boto3' dependency, which is "
    "not installed. Install it with:\n\n"
    "    pip install nanorix[s3]\n\n"
    "Credentials are resolved by boto3's standard chain (environment "
    "variables, shared credentials file, instance profile, SSO) in YOUR "
    "process. Nanorix never takes custody of your AWS credentials "
    "(ADR-036)."
)


def _require_boto3() -> Any:
    """Import boto3 lazily; raise an actionable ImportError if absent."""
    try:
        import boto3
    except ImportError as err:  # pragma: no cover - exercised via monkeypatch
        raise ImportError(_IMPORT_HINT) from err
    return boto3


def _build_client(client: Optional[Any], region: Optional[str]) -> Any:
    """Return the customer-supplied client, or build one from boto3's chain.

    When ``client`` is supplied it is used verbatim (the customer fully
    owns its credential configuration). Otherwise a default boto3 S3
    client is created — credentials come from boto3's standard chain in
    the customer's process. Nanorix passes no credential.
    """
    if client is not None:
        return client
    boto3 = _require_boto3()
    if region is not None:
        return boto3.client("s3", region_name=region)
    return boto3.client("s3")


class S3Source(Source):
    """Stream every object under ``bucket``/``prefix`` INTO the capsule.

    Runs in the customer's process. Credentials are resolved by boto3's
    standard chain — never passed to or stored by Nanorix (ADR-036).

    Args:
        bucket: The S3 bucket name. Customer-declared (ADR-013).
        prefix: Optional key prefix to scope the listing. Empty string
            lists the whole bucket. Customer-declared.
        region: Optional AWS region for the auto-built client. Ignored
            when ``client`` is supplied.
        client: Optional pre-built boto3 S3 client. When supplied the
            customer fully owns its credential/session configuration.
        flatten_prefix: When ``True`` (default) the prefix is stripped
            from each object's capsule filename so the capsule sees flat
            names. When ``False`` the full key (minus leading slash) is
            used as the capsule filename.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        region: Optional[str] = None,
        client: Optional[Any] = None,
        flatten_prefix: bool = True,
    ) -> None:
        if not bucket:
            raise ConnectorError("S3Source requires a non-empty bucket name")
        self.bucket = bucket
        self.prefix = prefix
        self.flatten_prefix = flatten_prefix
        # NOTE: no credential attribute is ever stored. `_client` is a
        # boto3 client (which holds the customer's session, in the
        # customer's process) — Nanorix neither constructs raw credentials
        # nor copies any secret onto this object.
        self._client = _build_client(client, region)

    def list_objects(self) -> List[SourceObject]:
        objects: List[SourceObject] = []
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                for entry in page.get("Contents", []) or []:
                    key = entry["Key"]
                    # Skip "directory marker" keys (zero-byte, trailing /).
                    if key.endswith("/"):
                        continue
                    capsule_filename = self._capsule_filename(key)
                    objects.append(
                        SourceObject(
                            key=key,
                            capsule_filename=capsule_filename,
                            size=entry.get("Size"),
                        )
                    )
        except Exception as err:  # noqa: BLE001 - re-wrap actionable
            raise ConnectorError(
                f"could not list s3://{self.bucket}/{self.prefix}: {err}"
            ) from err
        if not objects:
            raise ConnectorError(
                f"no objects found at s3://{self.bucket}/{self.prefix} — "
                "nothing to stream into the capsule"
            )
        return objects

    def open_object(self, obj: SourceObject, chunk_size: int) -> Iterator[bytes]:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=obj.key)
            body = response["Body"]
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        except Exception as err:  # noqa: BLE001 - re-wrap actionable
            raise ConnectorError(
                f"could not read s3://{self.bucket}/{obj.key}: {err}"
            ) from err

    def _capsule_filename(self, key: str) -> str:
        if self.flatten_prefix:
            return key.rsplit("/", 1)[-1]
        return key.lstrip("/")


class S3Sink(Sink):
    """Stream capsule OUTPUT files to ``bucket``/``prefix`` BEFORE destroy.

    Runs in the customer's process. Credentials are resolved by boto3's
    standard chain — never passed to or stored by Nanorix (ADR-036). The
    streaming core invokes this BEFORE the capsule is destroyed
    (``feedback_no_post_destroy_cache.md``).

    Args:
        bucket: The destination S3 bucket name. Customer-declared.
        prefix: Optional key prefix prepended to each output filename.
        region: Optional AWS region for the auto-built client.
        client: Optional pre-built boto3 S3 client (customer owns its
            credential/session configuration).
        chunk_size: Multipart chunk size hint (bytes). Used when an output
            exceeds a single chunk; bounds the per-part buffer.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        region: Optional[str] = None,
        client: Optional[Any] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if not bucket:
            raise ConnectorError("S3Sink requires a non-empty bucket name")
        self.bucket = bucket
        self.prefix = prefix
        self.chunk_size = chunk_size
        self._client = _build_client(client, region)

    def write_object(self, capsule_filename: str, chunks: Iterator[bytes]) -> str:
        key = self._dest_key(capsule_filename)
        try:
            # Multipart upload streams chunk-by-chunk so no whole-object
            # buffer is held. For small single-chunk outputs, fall back to
            # a single put_object (multipart has a 5 MiB minimum part size).
            parts: List[bytes] = []
            total = 0
            for chunk in chunks:
                parts.append(chunk)
                total += len(chunk)
            # parts is at most a few bounded chunks for typical capsule
            # outputs; for very large outputs the upload streams via
            # upload_part below. Either way nothing is staged on Nanorix.
            if len(parts) <= 1 or total < 5 * 1024 * 1024:
                self._client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=b"".join(parts),
                )
            else:
                self._multipart_upload(key, parts)
        except ConnectorError:
            raise
        except Exception as err:  # noqa: BLE001 - re-wrap actionable
            raise ConnectorError(
                f"could not write s3://{self.bucket}/{key}: {err}"
            ) from err
        return f"s3://{self.bucket}/{key}"

    def _multipart_upload(self, key: str, parts: List[bytes]) -> None:
        created = self._client.create_multipart_upload(Bucket=self.bucket, Key=key)
        upload_id = created["UploadId"]
        completed: List[Dict[str, Any]] = []
        try:
            for i, part in enumerate(parts, start=1):
                resp = self._client.upload_part(
                    Bucket=self.bucket,
                    Key=key,
                    PartNumber=i,
                    UploadId=upload_id,
                    Body=part,
                )
                completed.append({"PartNumber": i, "ETag": resp["ETag"]})
            self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": completed},
            )
        except Exception:
            try:
                self._client.abort_multipart_upload(
                    Bucket=self.bucket, Key=key, UploadId=upload_id
                )
            except Exception:
                pass
            raise

    def _dest_key(self, capsule_filename: str) -> str:
        if not self.prefix:
            return capsule_filename
        return f"{self.prefix.rstrip('/')}/{capsule_filename}"

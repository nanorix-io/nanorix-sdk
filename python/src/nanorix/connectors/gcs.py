"""Google Cloud Storage source/sink connectors — customer-side streaming.

Batch 14 (2026-05-28). ``GCSSource`` streams objects under a prefix INTO
the capsule before exec; ``GCSSink`` streams capsule output to a prefix
BEFORE destroy. Both run in the customer's process using the customer's
credentials — resolved by google-cloud-storage's STANDARD Application
Default Credentials chain (GOOGLE_APPLICATION_CREDENTIALS, gcloud auth,
workload identity, GCE metadata service). Nanorix never takes custody of
a credential and never stages data on Nanorix infrastructure.

``google-cloud-storage`` is an OPTIONAL dependency declared as the
``[gcs]`` extra. This module guards the import: importing it without the
library installed raises a clear, actionable :class:`ImportError`. All
streaming + trust-boundary logic lives in
:mod:`nanorix.connectors._base`, which imports WITHOUT any cloud SDK and
is tested against a FAKE backend.

Credential discipline (NON-NEGOTIABLE): these classes accept NO
service-account-key / token argument. They optionally accept a pre-built
``client`` (so an advanced customer can configure their own credentials)
— Nanorix never constructs credentials, never stores them, and the
connector object retains no secret attribute that outlives a call.
"""

from __future__ import annotations

from typing import Any, Iterator, List, Optional

from nanorix.connectors._base import (
    DEFAULT_CHUNK_SIZE,
    ConnectorError,
    Sink,
    Source,
    SourceObject,
)

__all__ = ["GCSSource", "GCSSink"]

_IMPORT_HINT = (
    "The GCS connector requires the optional 'google-cloud-storage' "
    "dependency, which is not installed. Install it with:\n\n"
    "    pip install nanorix[gcs]\n\n"
    "Credentials are resolved by Application Default Credentials "
    "(GOOGLE_APPLICATION_CREDENTIALS, gcloud auth, workload identity) in "
    "YOUR process. Nanorix never takes custody of your GCP credentials."
)


def _require_gcs() -> Any:
    """Import google.cloud.storage lazily; raise actionable ImportError if absent."""
    try:
        from google.cloud import storage
    except ImportError as err:
        raise ImportError(_IMPORT_HINT) from err
    # A ``None`` sentinel in sys.modules (Python's "import was blocked"
    # marker) resolves to ``storage is None`` without raising — treat it
    # as absence so the actionable hint still fires.
    if storage is None:
        raise ImportError(_IMPORT_HINT)
    return storage


def _build_client(client: Optional[Any], project: Optional[str]) -> Any:
    """Return the customer-supplied client, or build one from ADC.

    When ``client`` is supplied it is used verbatim (customer owns its
    credential configuration). Otherwise a default storage Client is
    created — credentials come from Application Default Credentials in the
    customer's process. Nanorix passes no credential.
    """
    if client is not None:
        return client
    storage = _require_gcs()
    if project is not None:
        return storage.Client(project=project)
    return storage.Client()


class GCSSource(Source):
    """Stream every object under ``bucket``/``prefix`` INTO the capsule.

    Runs in the customer's process. Credentials are resolved by
    Application Default Credentials — never passed to or stored by
    Nanorix.

    Args:
        bucket: The GCS bucket name. Customer-declared.
        prefix: Optional object-name prefix to scope the listing. Empty
            string lists the whole bucket. Customer-declared.
        project: Optional GCP project for the auto-built client. Ignored
            when ``client`` is supplied.
        client: Optional pre-built ``google.cloud.storage.Client``.
        flatten_prefix: When ``True`` (default) the capsule filename is
            the object basename; when ``False`` the full object name is
            used.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        project: Optional[str] = None,
        client: Optional[Any] = None,
        flatten_prefix: bool = True,
    ) -> None:
        if not bucket:
            raise ConnectorError("GCSSource requires a non-empty bucket name")
        self.bucket = bucket
        self.prefix = prefix
        self.flatten_prefix = flatten_prefix
        # No credential attribute is ever stored on this object.
        self._client = _build_client(client, project)

    def list_objects(self) -> List[SourceObject]:
        objects: List[SourceObject] = []
        try:
            blobs = self._client.list_blobs(self.bucket, prefix=self.prefix)
            for blob in blobs:
                name = blob.name
                if name.endswith("/"):
                    continue
                objects.append(
                    SourceObject(
                        key=name,
                        capsule_filename=self._capsule_filename(name),
                        size=getattr(blob, "size", None),
                    )
                )
        except Exception as err:  # noqa: BLE001 - re-wrap actionable
            raise ConnectorError(
                f"could not list gs://{self.bucket}/{self.prefix}: {err}"
            ) from err
        if not objects:
            raise ConnectorError(
                f"no objects found at gs://{self.bucket}/{self.prefix} — "
                "nothing to stream into the capsule"
            )
        return objects

    def open_object(self, obj: SourceObject, chunk_size: int) -> Iterator[bytes]:
        try:
            bucket = self._client.bucket(self.bucket)
            blob = bucket.blob(obj.key)
            # open() in binary read mode streams in bounded chunks; the
            # library never materializes the whole object beyond the
            # read buffer we request.
            with blob.open("rb") as stream:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except Exception as err:  # noqa: BLE001 - re-wrap actionable
            raise ConnectorError(
                f"could not read gs://{self.bucket}/{obj.key}: {err}"
            ) from err

    def _capsule_filename(self, name: str) -> str:
        if self.flatten_prefix:
            return name.rsplit("/", 1)[-1]
        return name.lstrip("/")


class GCSSink(Sink):
    """Stream capsule OUTPUT files to ``bucket``/``prefix`` BEFORE destroy.

    Runs in the customer's process. Credentials are resolved by
    Application Default Credentials — never passed to or stored by
    Nanorix. The streaming core invokes this BEFORE the capsule
    is destroyed (``feedback_no_post_destroy_cache.md``).

    Args:
        bucket: The destination GCS bucket name. Customer-declared.
        prefix: Optional object-name prefix prepended to each output.
        project: Optional GCP project for the auto-built client.
        client: Optional pre-built ``google.cloud.storage.Client``.
        chunk_size: Streaming write chunk size (bytes).
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        project: Optional[str] = None,
        client: Optional[Any] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if not bucket:
            raise ConnectorError("GCSSink requires a non-empty bucket name")
        self.bucket = bucket
        self.prefix = prefix
        self.chunk_size = chunk_size
        self._client = _build_client(client, project)

    def write_object(self, capsule_filename: str, chunks: Iterator[bytes]) -> str:
        name = self._dest_name(capsule_filename)
        try:
            bucket = self._client.bucket(self.bucket)
            blob = bucket.blob(name)
            # Stream chunk-by-chunk via the blob's writable stream so no
            # whole-object buffer is held; nothing is staged on Nanorix.
            with blob.open("wb") as stream:
                for chunk in chunks:
                    stream.write(chunk)
        except Exception as err:  # noqa: BLE001 - re-wrap actionable
            raise ConnectorError(
                f"could not write gs://{self.bucket}/{name}: {err}"
            ) from err
        return f"gs://{self.bucket}/{name}"

    def _dest_name(self, capsule_filename: str) -> str:
        if not self.prefix:
            return capsule_filename
        return f"{self.prefix.rstrip('/')}/{capsule_filename}"

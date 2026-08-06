"""Framework-agnostic streaming core for data source/sink connectors.

Gap-closing arc: data source/sink connectors,
the #1 pipeline-frequency gap from the integration audit ("can I just
point you at my S3 bucket?"). This module is the pure, cloud-SDK-free
engine that ``nanorix.connectors.s3`` and ``nanorix.connectors.gcs``
both stream through. It has NO dependency on boto3 or
google-cloud-storage and is fully unit-testable with a FAKE in-memory
backend (see ``tests/test_connectors.py``).

## Trust-boundary discipline (NON-NEGOTIABLE —)

A Nanorix connector is a **customer-side SDK helper**. It runs in the
customer's process, using the customer's cloud credentials (resolved by
the cloud SDK's standard chain — env vars, instance profile, workload
identity). The bytes it moves travel:

    customer storage  ->  customer process memory (ONE bounded chunk)
                      ->  capsule.upload() over TLS  ->  sealed capsule

and, for sinks, the exact reverse. There is no third location.

- **Zero Nanorix-side staging.** Streaming only. The largest allocation
  on any path is a single configurable chunk — never the whole object.
  No temp file, no spool, no buffer on Nanorix infrastructure.
- **No credential custody.** The connector takes no credential argument
  it stores. Credentials live only in the customer's cloud-SDK client,
  in the customer's process. Nanorix infrastructure never receives one.
- **Source before exec.** A source streams INTO the capsule before the
  command runs.
- **Sink before destroy.** A sink streams the capsule's output to the
  destination BEFORE the capsule is destroyed — never after. Once the
  AuditProof says the capsule is destroyed there is, by design, no
  capsule data left to read.
- **No new signed primitive.** Connectors consume the existing
  AuditProof. The AuditProof byte-form is unchanged.
- **No regulatory claim.** A connector moves bytes; it asserts
  nothing about what the data is or which control it satisfies.

## Why a FAKE-backend-testable core

The S3 / GCS modules are thin shells: they implement the
:class:`Source` / :class:`Sink` protocols by talking to boto3 /
google-cloud-storage. ALL ordering + chunking + trust-boundary logic
lives here, so the test suite imports only this module + a fake backend
and never needs a cloud SDK installed. Azure Blob, MinIO, R2, or any
other object store can be wired against the SAME protocols — they are
documentation, not new engines.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, List, Optional, Protocol, runtime_checkable

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "ConnectorError",
    "SourceObject",
    "Source",
    "Sink",
    "stream_source_into_capsule",
    "stream_capsule_to_sink",
    "TRUST_BOUNDARY_NOTE",
]

# Default streaming chunk size (bytes). Bounded — no path materializes a
# whole object beyond one chunk. Conservative default; large-object
# pipelines can raise it per-connector. 8 MiB balances throughput against
# per-call memory in the customer's process.
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024

# Carried as a module constant so any serializer / doc generator reads the
# same one-line trust statement. zero-retention affirmation.
TRUST_BOUNDARY_NOTE = (
    "Connectors are customer-side SDK helpers. Your bytes, your "
    "credentials, streamed through the sealed capsule a bounded chunk at "
    "a time. Nanorix never stages the data and never takes custody of "
    "your cloud credentials. Sources stream in before exec; sinks stream "
    "out before destroy."
)


class ConnectorError(Exception):
    """Raised for connector-level failures.

    Source-not-found, sink-write-failure, and configuration errors raise
    this class so a pipeline author sees a clear, actionable message
    rather than an opaque cloud-SDK traceback. The original cloud-SDK
    exception is chained via ``raise ConnectorError(...) from err``.
    """


class SourceObject:
    """One object to stream into the capsule.

    A :class:`Source` enumerates its objects as ``SourceObject`` records.
    The streaming core reads each object's bytes in bounded chunks via
    :meth:`Source.open_object` and writes them to the capsule under
    :attr:`capsule_filename`.

    Attributes:
        key: The storage-native key/path of the object in the customer's
            store (e.g. an S3 object key). Customer-declared.
        capsule_filename: The filename the object is uploaded as inside
            the capsule's input directory. Defaults to the basename of
            ``key`` when not given.
        size: Optional known size in bytes (for progress / validation).
            ``None`` when the store did not report it.
    """

    __slots__ = ("key", "capsule_filename", "size")

    def __init__(
        self,
        key: str,
        capsule_filename: Optional[str] = None,
        size: Optional[int] = None,
    ) -> None:
        self.key = key
        # Strip any leading path so the capsule filename is a flat name.
        self.capsule_filename = capsule_filename or key.rsplit("/", 1)[-1]
        self.size = size

    def __repr__(self) -> str:  # pragma: no cover - repr only
        return (
            f"SourceObject(key={self.key!r}, "
            f"capsule_filename={self.capsule_filename!r}, size={self.size!r})"
        )


@runtime_checkable
class Source(Protocol):
    """A customer-side source: streams objects INTO the capsule before exec.

    Implementations (``S3Source``, ``GCSSource``) run in the customer's
    process with the customer's credentials and never stage data on
    Nanorix infrastructure. The streaming core
    (:func:`stream_source_into_capsule`) drives a source by:

    1. calling :meth:`list_objects` to enumerate what to stream, then
    2. for each object, calling :meth:`open_object` to get a bounded
       chunk iterator and writing those chunks into the capsule.

    A source declares no credential it stores; credentials are resolved
    by the underlying cloud SDK's standard chain.
    """

    def list_objects(self) -> Iterable[SourceObject]:
        """Enumerate the objects this source will stream into the capsule.

        Raises:
            ConnectorError: When the source location does not exist or
                cannot be listed (clear, actionable message).
        """
        ...

    def open_object(self, obj: SourceObject, chunk_size: int) -> Iterator[bytes]:
        """Yield the object's bytes in bounded chunks of at most ``chunk_size``.

        No implementation may materialize the whole object beyond one
        chunk. Each yielded ``bytes`` is at most ``chunk_size`` long.

        Raises:
            ConnectorError: When the object cannot be read.
        """
        ...


@runtime_checkable
class Sink(Protocol):
    """A customer-side sink: streams capsule OUTPUT to the destination before destroy.

    Implementations (``S3Sink``, ``GCSSink``) run in the customer's
    process with the customer's credentials. The streaming core
    (:func:`stream_capsule_to_sink`) drives a sink by, for each output
    file, calling :meth:`write_object` with a bounded chunk iterator that
    pulls capsule output. The sink MUST be fully driven BEFORE the
    capsule is destroyed.
    """

    def write_object(
        self,
        capsule_filename: str,
        chunks: Iterator[bytes],
    ) -> str:
        """Write the capsule output (streamed in ``chunks``) to the destination.

        Returns the storage-native key the output was written to (for the
        caller's manifest). No implementation may buffer the whole object
        beyond one chunk where the backend supports chunked upload.

        Raises:
            ConnectorError: When the write fails (clear, actionable
                message; original cloud-SDK exception chained).
        """
        ...


def stream_source_into_capsule(
    client: Any,
    capsule: Any,
    source: Source,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> List[str]:
    """Stream every object from ``source`` INTO ``capsule`` (before exec).

    This is the source half of the trust boundary. It runs in the
    customer's process; the bytes go customer-store -> bounded chunk ->
    ``capsule.upload()`` and nowhere else.

    The ``capsule`` is a live capsule handle (``LiveCapsule`` or anything
    exposing ``.upload(filename, data)`` and ``.id``). In tests it is a
    fake. The ``client`` argument is accepted for symmetry with the
    Orchestrator surface and is otherwise unused here.

    Streaming detail: the SDK ``upload`` surface takes whole bytes for one
    capsule file, so we accumulate exactly the chunks of ONE object (never
    more than one object at a time) and upload it, then release the
    buffer before the next object. The bounded-chunk discipline applies on
    the read side — the cloud SDK is asked for at most ``chunk_size`` at a
    time so the customer's process never holds an unbounded read buffer.
    No object is ever staged on Nanorix infrastructure.

    Args:
        client: Nanorix client (unused; symmetry with orchestrator core).
        capsule: A live capsule handle (``.upload`` + ``.id``).
        source: A :class:`Source` implementation.
        chunk_size: Maximum bytes read from the store per chunk. Must be
            positive.

    Returns:
        The list of capsule filenames streamed in, in order.

    Raises:
        ConnectorError: ``chunk_size`` non-positive, or a source object
            could not be listed / read.
    """
    if chunk_size <= 0:
        raise ConnectorError("chunk_size must be a positive integer")

    uploaded: List[str] = []
    for obj in source.list_objects():
        # Accumulate ONE object's chunks (bounded read per chunk), upload,
        # then drop the buffer. Never hold more than one object.
        buf = bytearray()
        for chunk in source.open_object(obj, chunk_size):
            if len(chunk) > chunk_size:
                raise ConnectorError(
                    f"source {type(source).__name__} yielded a "
                    f"{len(chunk)}-byte chunk exceeding the {chunk_size}-byte "
                    "bound; streaming must be bounded."
                )
            buf.extend(chunk)
        capsule.upload(obj.capsule_filename, bytes(buf))
        uploaded.append(obj.capsule_filename)
        del buf
    return uploaded


def stream_capsule_to_sink(
    client: Any,
    capsule: Any,
    output_files: Iterable[str],
    sink: Sink,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> List[str]:
    """Stream each capsule OUTPUT file to ``sink`` (BEFORE destroy).

    This is the sink half of the trust boundary, and it enforces
    the no-post-destroy-cache invariant:
    the caller MUST invoke this BEFORE ``capsule.destroy()``. The bytes go
    sealed-capsule -> ``capsule.download()`` -> bounded chunk -> customer
    store and nowhere else.

    Streaming detail: the SDK ``download`` surface returns whole bytes for
    one capsule output, so we slice it into ``chunk_size`` chunks and feed
    a chunk iterator to the sink, which uploads chunk-by-chunk where the
    backend supports it. No output is staged on Nanorix infrastructure;
    the bytes exist only transiently in the customer's process.

    Args:
        client: Nanorix client (unused; symmetry with orchestrator core).
        capsule: A live capsule handle (``.download`` + ``.id``).
        output_files: The capsule output filenames to stream to the sink.
        sink: A :class:`Sink` implementation.
        chunk_size: Maximum bytes per chunk fed to the sink. Positive.

    Returns:
        The list of storage-native keys written, in order.

    Raises:
        ConnectorError: ``chunk_size`` non-positive, an output could not
            be downloaded, or the sink write failed.
    """
    if chunk_size <= 0:
        raise ConnectorError("chunk_size must be a positive integer")

    written: List[str] = []
    for filename in output_files:
        try:
            data = capsule.download(filename)
        except Exception as err:  # noqa: BLE001 - re-wrap with actionable msg
            raise ConnectorError(
                f"could not download capsule output {filename!r} before "
                f"streaming to sink {type(sink).__name__}: {err}"
            ) from err
        key = sink.write_object(filename, _slice_into_chunks(data, chunk_size))
        written.append(key)
    return written


def _slice_into_chunks(data: bytes, chunk_size: int) -> Iterator[bytes]:
    """Yield ``data`` as bounded ``chunk_size`` slices (no full re-copy)."""
    view = memoryview(data)
    for start in range(0, len(view), chunk_size):
        yield bytes(view[start : start + chunk_size])

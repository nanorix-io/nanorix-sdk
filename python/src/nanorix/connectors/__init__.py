"""Data source/sink connectors for Nanorix capsules (ADR-036).

Batch 14 (2026-05-28) — the #1 pipeline-frequency gap: "can I just point
you at my S3 bucket?". Connectors are **customer-side SDK helpers** that
stream bytes through volatile capsule memory:

- a **source** streams objects INTO the capsule BEFORE exec,
- a **sink** streams capsule output to the destination BEFORE destroy.

## The trust boundary (ADR-036, NON-NEGOTIABLE)

A connector runs in the CUSTOMER's process, using the CUSTOMER's cloud
credentials (resolved by the cloud SDK's standard chain). It streams a
bounded chunk at a time directly between the customer's storage and the
sealed capsule over TLS. Nanorix never stages the data, never holds the
credentials, and never sees the bytes outside the sealed capsule. A sink
streams out BEFORE destroy — never after (``feedback_no_post_destroy_cache``).
Connectors consume the existing AuditProof and add NO new signed
primitive (narrowness preserved). They move bytes; they make no
regulatory claim (ADR-017).

## Imports

The framework-agnostic streaming core is safe to import eagerly — it has
NO cloud-SDK dependency:

    from nanorix.connectors import (
        Source, Sink, SourceObject, ConnectorError,
        stream_source_into_capsule, stream_capsule_to_sink,
    )

The cloud-specific connectors live behind optional extras. Import the one
you need by its submodule path so a missing cloud SDK fails with a clear,
actionable hint instead of at package import:

    from nanorix.connectors.s3 import S3Source, S3Sink     # pip install nanorix[s3]
    from nanorix.connectors.gcs import GCSSource, GCSSink   # pip install nanorix[gcs]

Azure Blob, MinIO, Cloudflare R2, and any other object store follow the
SAME ``Source`` / ``Sink`` protocols — they are documentation, not new
engines (see ``docs/integrations/connectors.md``).
"""

from __future__ import annotations

from nanorix.connectors._base import (
    DEFAULT_CHUNK_SIZE,
    TRUST_BOUNDARY_NOTE,
    ConnectorError,
    Sink,
    Source,
    SourceObject,
    stream_capsule_to_sink,
    stream_source_into_capsule,
)

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "TRUST_BOUNDARY_NOTE",
    "ConnectorError",
    "Sink",
    "Source",
    "SourceObject",
    "stream_capsule_to_sink",
    "stream_source_into_capsule",
]

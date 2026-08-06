"""Tests for data source/sink connectors (Batch 14).

These tests import ONLY ``nanorix.connectors._base`` (the framework- and
cloud-SDK-free streaming core) plus a hand-rolled FAKE in-memory backend.
They do NOT import boto3 or google-cloud-storage — verifying the
architecture goal that the connector core is testable without any cloud
SDK installed.

The fakes record the order of every capsule operation and every chunk
size into shared logs so the load-bearing invariants can be asserted
directly:

- a SOURCE streams INTO the capsule BEFORE exec,
- a SINK streams capsule output to the destination BEFORE destroy
  (the no-post-destroy-cache invariant),
- streaming is bounded-chunk (no full-object buffer beyond a chunk),
- credentials are pass-through (no secret retained on the connector).
"""

from __future__ import annotations

import sys
import types

import pytest

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
from nanorix.integrations._orchestrator_core import (
    CapsuleJobParams,
    run_capsule_job,
)


# ── Fakes ─────────────────────────────────────────────────────────────


class FakeExecResult:
    def __init__(self, exit_code: int = 0, stdout: str = "ok", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class FakeAuditProof:
    def __init__(self, capsule_id: str):
        self.id = capsule_id

    def verification(self):
        return {"capsule_id": self.id, "kind": "verification"}


class FakeCapsule:
    """Records every operation into a shared log; serves fake outputs."""

    def __init__(self, capsule_id: str, log: list, outputs: dict | None = None):
        self.id = capsule_id
        self._log = log
        self.uploaded: dict[str, bytes] = {}
        self.outputs_store = outputs if outputs is not None else {}

    def upload(self, filename: str, data: bytes):
        self._log.append(("upload", filename, len(data)))
        self.uploaded[filename] = data
        return {"filename": filename}

    def execute(self, command=None, timeout=None):
        self._log.append(("execute", command))
        return FakeExecResult()

    def download(self, filename: str) -> bytes:
        self._log.append(("download", filename))
        if filename not in self.outputs_store:
            raise RuntimeError(f"no such output {filename}")
        return self.outputs_store[filename]

    def destroy(self):
        self._log.append(("destroy", self.id))
        return FakeAuditProof(self.id)


class FakeCapsules:
    def __init__(self, log: list, outputs: dict | None = None):
        self._log = log
        self._outputs = outputs
        self.last_create_kwargs: dict | None = None

    def create(self, *, capsulefile=None, **kwargs):
        self.last_create_kwargs = {"capsulefile": capsulefile, **kwargs}
        self._log.append(("create", capsulefile))
        return FakeCapsule("cap_test", self._log, outputs=self._outputs)


class FakeClient:
    def __init__(self, log: list, outputs: dict | None = None):
        self.capsules = FakeCapsules(log, outputs=outputs)


class FakeSource(Source):
    """In-memory source — NO cloud SDK. Records every chunk size requested.

    `store` maps capsule_filename -> bytes. `chunk_log` records the size of
    every chunk the streaming core pulled, so the bounded-chunk invariant
    can be asserted. Holds NO credential attribute.
    """

    def __init__(self, store: dict[str, bytes], chunk_log: list | None = None,
                 fail_list: bool = False):
        self.store = store
        self.chunk_log = chunk_log if chunk_log is not None else []
        self._fail_list = fail_list

    def list_objects(self):
        if self._fail_list:
            raise ConnectorError("source not found: fake bucket missing")
        return [SourceObject(key=name, capsule_filename=name, size=len(data))
                for name, data in self.store.items()]

    def open_object(self, obj: SourceObject, chunk_size: int):
        data = self.store[obj.capsule_filename]
        for start in range(0, len(data), chunk_size):
            chunk = data[start:start + chunk_size]
            self.chunk_log.append(len(chunk))
            yield chunk


class FakeSink(Sink):
    """In-memory sink — NO cloud SDK. Records chunk sizes + final bytes.

    Holds NO credential attribute. `written` maps capsule_filename ->
    reassembled bytes; `chunk_log` records every chunk size received.
    """

    def __init__(self, chunk_log: list | None = None, fail_write: bool = False):
        self.written: dict[str, bytes] = {}
        self.chunk_log = chunk_log if chunk_log is not None else []
        self._fail_write = fail_write

    def write_object(self, capsule_filename: str, chunks) -> str:
        if self._fail_write:
            raise ConnectorError(f"sink write failed for {capsule_filename}")
        buf = bytearray()
        for chunk in chunks:
            self.chunk_log.append(len(chunk))
            buf.extend(chunk)
        self.written[capsule_filename] = bytes(buf)
        return f"fake://dest/{capsule_filename}"


class MisbehavingSource(Source):
    """Yields a chunk LARGER than the bound — must be rejected by the core."""

    def list_objects(self):
        return [SourceObject(key="big.bin", capsule_filename="big.bin")]

    def open_object(self, obj, chunk_size):
        yield b"x" * (chunk_size + 10)


# ── Source streams INTO capsule BEFORE exec ──────────────────────────────


def test_source_streams_in_before_exec_ordering():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    src = FakeSource({"a.csv": b"hello", "b.csv": b"world"})
    stream_source_into_capsule(FakeClient(log), cap, src)
    cap.execute()
    op_names = [e[0] for e in log]
    # every upload precedes the execute
    assert op_names.index("execute") > max(
        i for i, n in enumerate(op_names) if n == "upload"
    )


def test_source_uploads_all_objects():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    src = FakeSource({"a.csv": b"hello", "b.csv": b"world"})
    uploaded = stream_source_into_capsule(FakeClient(log), cap, src)
    assert set(uploaded) == {"a.csv", "b.csv"}
    assert cap.uploaded["a.csv"] == b"hello"
    assert cap.uploaded["b.csv"] == b"world"


def test_source_uploaded_bytes_roundtrip():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    payload = b"\x00\x01\x02" * 1000
    src = FakeSource({"blob.bin": payload})
    stream_source_into_capsule(FakeClient(log), cap, src)
    assert cap.uploaded["blob.bin"] == payload


# ── Sink streams OUT BEFORE destroy (no-post-destroy-cache) ──────────────


def test_sink_streams_out_before_destroy_ordering():
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={"out.json": b"{}"})
    sink = FakeSink()
    stream_capsule_to_sink(FakeClient(log), cap, ["out.json"], sink)
    cap.destroy()
    op_names = [e[0] for e in log]
    # the download (sink read) precedes destroy — no post-destroy cache
    assert op_names.index("download") < op_names.index("destroy")


def test_sink_writes_capsule_output():
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={"out.json": b'{"k":1}'})
    sink = FakeSink()
    keys = stream_capsule_to_sink(FakeClient(log), cap, ["out.json"], sink)
    assert keys == ["fake://dest/out.json"]
    assert sink.written["out.json"] == b'{"k":1}'


def test_sink_never_reads_after_destroy():
    """If we (incorrectly) destroyed first, download must already be done."""
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={"out.json": b"{}"})
    sink = FakeSink()
    stream_capsule_to_sink(FakeClient(log), cap, ["out.json"], sink)
    # snapshot: no destroy has occurred yet during sink streaming
    assert ("destroy", "cap_test") not in log
    cap.destroy()
    # after destroy, the sink already holds the bytes — nothing re-read
    downloads = [e for e in log if e[0] == "download"]
    assert len(downloads) == 1


# ── Chunked streaming (bounded) ──────────────────────────────────────────


def test_source_streams_in_bounded_chunks():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    chunk_log: list = []
    payload = b"z" * 25
    src = FakeSource({"big.bin": payload}, chunk_log=chunk_log)
    stream_source_into_capsule(FakeClient(log), cap, src, chunk_size=10)
    # 25 bytes / 10 → chunks of 10, 10, 5; none exceeds the bound
    assert chunk_log == [10, 10, 5]
    assert all(c <= 10 for c in chunk_log)
    assert cap.uploaded["big.bin"] == payload


def test_sink_streams_out_bounded_chunks():
    log: list = []
    payload = b"q" * 23
    cap = FakeCapsule("cap_test", log, outputs={"out.bin": payload})
    chunk_log: list = []
    sink = FakeSink(chunk_log=chunk_log)
    stream_capsule_to_sink(FakeClient(log), cap, ["out.bin"], sink, chunk_size=10)
    assert chunk_log == [10, 10, 3]
    assert all(c <= 10 for c in chunk_log)
    assert sink.written["out.bin"] == payload


def test_large_object_streamed_in_bounded_chunks_only():
    """A large object never produces a chunk larger than chunk_size."""
    log: list = []
    cap = FakeCapsule("cap_test", log)
    chunk_log: list = []
    big = b"a" * (1024 * 1024 + 7)  # > 1 MiB, not a multiple of chunk
    src = FakeSource({"large.dat": big}, chunk_log=chunk_log)
    stream_source_into_capsule(FakeClient(log), cap, src, chunk_size=64 * 1024)
    assert max(chunk_log) <= 64 * 1024
    assert sum(chunk_log) == len(big)
    assert cap.uploaded["large.dat"] == big


def test_misbehaving_source_oversized_chunk_rejected():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    with pytest.raises(ConnectorError, match="exceeding"):
        stream_source_into_capsule(FakeClient(log), cap, MisbehavingSource(),
                                   chunk_size=16)


def test_default_chunk_size_is_bounded_and_positive():
    assert DEFAULT_CHUNK_SIZE > 0
    assert DEFAULT_CHUNK_SIZE == 8 * 1024 * 1024


def test_zero_chunk_size_rejected_source():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    src = FakeSource({"a": b"x"})
    with pytest.raises(ConnectorError, match="positive"):
        stream_source_into_capsule(FakeClient(log), cap, src, chunk_size=0)


def test_negative_chunk_size_rejected_sink():
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={"o": b"x"})
    sink = FakeSink()
    with pytest.raises(ConnectorError, match="positive"):
        stream_capsule_to_sink(FakeClient(log), cap, ["o"], sink, chunk_size=-1)


# ── Multiple sources / multiple sinks ────────────────────────────────────


def test_multiple_objects_one_source():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    src = FakeSource({"a": b"1", "b": b"2", "c": b"3"})
    uploaded = stream_source_into_capsule(FakeClient(log), cap, src)
    assert len(uploaded) == 3


def test_multiple_sinks_each_receive_output():
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={"out.json": b"data"})
    sink_a = FakeSink()
    sink_b = FakeSink()
    stream_capsule_to_sink(FakeClient(log), cap, ["out.json"], sink_a)
    stream_capsule_to_sink(FakeClient(log), cap, ["out.json"], sink_b)
    assert sink_a.written["out.json"] == b"data"
    assert sink_b.written["out.json"] == b"data"


def test_sink_multiple_output_files():
    log: list = []
    cap = FakeCapsule("cap_test", log,
                      outputs={"a.json": b"1", "b.json": b"2"})
    sink = FakeSink()
    keys = stream_capsule_to_sink(FakeClient(log), cap, ["a.json", "b.json"], sink)
    assert keys == ["fake://dest/a.json", "fake://dest/b.json"]
    assert sink.written["a.json"] == b"1"
    assert sink.written["b.json"] == b"2"


# ── Error paths ──────────────────────────────────────────────────────────


def test_source_not_found_raises_connector_error():
    log: list = []
    cap = FakeCapsule("cap_test", log)
    src = FakeSource({}, fail_list=True)
    with pytest.raises(ConnectorError, match="not found"):
        stream_source_into_capsule(FakeClient(log), cap, src)


def test_sink_write_failure_raises_connector_error():
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={"out.json": b"{}"})
    sink = FakeSink(fail_write=True)
    with pytest.raises(ConnectorError, match="sink write failed"):
        stream_capsule_to_sink(FakeClient(log), cap, ["out.json"], sink)


def test_sink_download_failure_raises_connector_error():
    log: list = []
    cap = FakeCapsule("cap_test", log, outputs={})  # no outputs
    sink = FakeSink()
    with pytest.raises(ConnectorError, match="could not download"):
        stream_capsule_to_sink(FakeClient(log), cap, ["missing.json"], sink)


# ── Credentials are pass-through — no secret retained on the connector ───


def test_source_holds_no_credential_attribute():
    src = FakeSource({"a": b"x"})
    secret_like = [a for a in vars(src) if any(
        t in a.lower() for t in ("secret", "key", "token", "password", "cred"))]
    assert secret_like == []


def test_sink_holds_no_credential_attribute():
    sink = FakeSink()
    secret_like = [a for a in vars(sink) if any(
        t in a.lower() for t in ("secret", "token", "password", "cred"))]
    assert secret_like == []


def test_source_object_basename_default():
    obj = SourceObject(key="some/nested/path/file.csv")
    assert obj.capsule_filename == "file.csv"


def test_source_object_explicit_filename():
    obj = SourceObject(key="x/y.csv", capsule_filename="renamed.csv", size=10)
    assert obj.capsule_filename == "renamed.csv"
    assert obj.size == 10


def test_trust_boundary_note_is_explicit():
    assert "customer-side" in TRUST_BOUNDARY_NOTE.lower()
    assert "never stages" in TRUST_BOUNDARY_NOTE.lower()
    assert "before destroy" in TRUST_BOUNDARY_NOTE.lower()


# ── Protocol conformance ─────────────────────────────────────────────────


def test_fake_source_satisfies_source_protocol():
    assert isinstance(FakeSource({}), Source)


def test_fake_sink_satisfies_sink_protocol():
    assert isinstance(FakeSink(), Sink)


# ── Integration with run_capsule_job (Batch 13 core) ─────────────────────


def test_run_capsule_job_streams_source_before_exec():
    log: list = []
    client = FakeClient(log)
    src = FakeSource({"in.csv": b"rows"})
    params = CapsuleJobParams(capsulefile="cf_x", sources=[src])
    run_capsule_job(client, params)
    op_names = [e[0] for e in log]
    # upload (from source) happens before execute
    upload_idx = op_names.index("upload")
    exec_idx = op_names.index("execute")
    assert upload_idx < exec_idx


def test_run_capsule_job_streams_sink_before_destroy():
    log: list = []
    client = FakeClient(log, outputs={"out.json": b"result"})
    sink = FakeSink()
    params = CapsuleJobParams(
        capsulefile="cf_x", output_files=["out.json"], sinks=[sink]
    )
    run_capsule_job(client, params)
    op_names = [e[0] for e in log]
    # download (sink read) precedes destroy
    assert op_names.index("download") < op_names.index("destroy")
    assert sink.written["out.json"] == b"result"


def test_run_capsule_job_end_to_end_source_and_sink():
    log: list = []
    client = FakeClient(log, outputs={"out.json": b"processed"})
    src = FakeSource({"in.csv": b"raw"})
    sink = FakeSink()
    params = CapsuleJobParams(
        capsulefile="cf_pipeline",
        sources=[src],
        output_files=["out.json"],
        sinks=[sink],
    )
    result = run_capsule_job(client, params)
    op_names = [e[0] for e in log]
    # Ordering: create → upload(source, before exec) → execute →
    # download(s) → destroy. The source upload precedes exec; every
    # download (the orchestrator's own output download AND the sink read)
    # precedes destroy — the no-post-destroy-cache invariant.
    assert op_names[0] == "create"
    assert op_names[-1] == "destroy"
    assert op_names.index("upload") < op_names.index("execute")
    assert max(i for i, n in enumerate(op_names) if n == "download") < op_names.index(
        "destroy"
    )
    assert "destroy" not in op_names[: op_names.index("execute")]
    assert result.destroyed is True
    assert result.audit_proof is not None
    assert sink.written["out.json"] == b"processed"


def test_run_capsule_job_no_connectors_is_backward_compatible():
    """B13 behavior unchanged when no sources/sinks are declared."""
    log: list = []
    client = FakeClient(log, outputs={"out.json": b"{}"})
    params = CapsuleJobParams(capsulefile="cf_x", output_files=["out.json"])
    result = run_capsule_job(client, params)
    assert result.destroyed is True
    assert result.outputs["out.json"] == b"{}"
    # no source upload occurred (no input_files, no sources)
    assert ("upload", "out.json", 2) not in log


def test_run_capsule_job_multiple_sources():
    log: list = []
    client = FakeClient(log)
    src1 = FakeSource({"a.csv": b"1"})
    src2 = FakeSource({"b.csv": b"2"})
    params = CapsuleJobParams(capsulefile="cf_x", sources=[src1, src2])
    run_capsule_job(client, params)
    uploads = [e for e in log if e[0] == "upload"]
    assert len(uploads) == 2


def test_run_capsule_job_connector_chunk_size_forwarded():
    log: list = []
    client = FakeClient(log)
    chunk_log: list = []
    src = FakeSource({"big.bin": b"x" * 25}, chunk_log=chunk_log)
    params = CapsuleJobParams(
        capsulefile="cf_x", sources=[src], connector_chunk_size=10
    )
    run_capsule_job(client, params)
    assert chunk_log == [10, 10, 5]


def test_run_capsule_job_source_failure_still_destroys():
    log: list = []
    client = FakeClient(log)
    src = FakeSource({}, fail_list=True)
    params = CapsuleJobParams(capsulefile="cf_x", sources=[src])
    with pytest.raises(ConnectorError):
        run_capsule_job(client, params)
    # cleanup-on-failure: capsule still destroyed
    assert ("destroy", "cap_test") in log


# ── Import guard: connector core imports WITHOUT cloud SDKs ───────────────


def test_connector_core_imports_without_cloud_sdks():
    import importlib
    mod = importlib.import_module("nanorix.connectors._base")
    assert hasattr(mod, "stream_source_into_capsule")
    assert hasattr(mod, "stream_capsule_to_sink")


def test_s3_import_without_boto3_raises_actionable_error(monkeypatch):
    """Constructing S3Source without boto3 raises a clear, actionable error."""
    # Simulate boto3 absence by blocking its import.
    monkeypatch.setitem(sys.modules, "boto3", None)
    from nanorix.connectors import s3
    with pytest.raises(ImportError, match=r"pip install nanorix\[s3\]"):
        s3.S3Source("my-bucket", "prefix/")


def test_gcs_import_without_library_raises_actionable_error(monkeypatch):
    """Constructing GCSSource without google-cloud-storage raises clearly."""
    # Block the google.cloud.storage import path.
    fake_google = types.ModuleType("google")
    fake_cloud = types.ModuleType("google.cloud")
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", None)
    from nanorix.connectors import gcs
    with pytest.raises(ImportError, match=r"pip install nanorix\[gcs\]"):
        gcs.GCSSource("my-bucket", "prefix/")


def test_s3_source_empty_bucket_rejected(monkeypatch):
    # boto3 not needed — the empty-bucket guard fires before client build.
    from nanorix.connectors import s3
    with pytest.raises(ConnectorError, match="non-empty bucket"):
        s3.S3Source("")


def test_gcs_sink_empty_bucket_rejected(monkeypatch):
    from nanorix.connectors import gcs
    with pytest.raises(ConnectorError, match="non-empty bucket"):
        gcs.GCSSink("")


def test_s3_source_with_injected_client_lists_and_streams():
    """An advanced customer can inject a pre-built client (their creds)."""
    from nanorix.connectors import s3

    class FakeS3Body:
        def __init__(self, data: bytes):
            self._data = data
            self._pos = 0

        def read(self, n: int) -> bytes:
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk

    class FakeBoto3Client:
        def __init__(self):
            self.objects = {"prefix/a.csv": b"hello", "prefix/b.csv": b"world"}

        def get_paginator(self, op):
            objects = self.objects
            class _P:
                def paginate(self, Bucket, Prefix):
                    yield {"Contents": [
                        {"Key": k, "Size": len(v)}
                        for k, v in objects.items() if k.startswith(Prefix)
                    ]}
            return _P()

        def get_object(self, Bucket, Key):
            return {"Body": FakeS3Body(self.objects[Key])}

    fake_client = FakeBoto3Client()
    source = s3.S3Source("my-bucket", "prefix/", client=fake_client)
    # connector retains no credential attribute
    secret_like = [a for a in vars(source) if any(
        t in a.lower() for t in ("secret", "token", "password"))]
    assert secret_like == []
    objects = source.list_objects()
    assert {o.capsule_filename for o in objects} == {"a.csv", "b.csv"}
    chunks = list(source.open_object(objects[0], 3))
    assert b"".join(chunks) == fake_client.objects["prefix/a.csv"]
    assert all(len(c) <= 3 for c in chunks)


def test_s3_sink_with_injected_client_writes():
    from nanorix.connectors import s3

    class FakeBoto3Client:
        def __init__(self):
            self.puts: dict = {}

        def put_object(self, Bucket, Key, Body):
            self.puts[(Bucket, Key)] = Body

    fake_client = FakeBoto3Client()
    sink = s3.S3Sink("dest-bucket", "out/", client=fake_client)

    def chunks():
        yield b"part1"
        yield b"part2"

    key = sink.write_object("result.json", chunks())
    assert key == "s3://dest-bucket/out/result.json"
    assert fake_client.puts[("dest-bucket", "out/result.json")] == b"part1part2"


def test_gcs_source_with_injected_client_lists_and_streams():
    from nanorix.connectors import gcs

    class FakeBlobStream:
        def __init__(self, data: bytes):
            self._data = data
            self._pos = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n: int) -> bytes:
            chunk = self._data[self._pos:self._pos + n]
            self._pos += len(chunk)
            return chunk

    class FakeBlob:
        def __init__(self, name, data):
            self.name = name
            self.size = len(data)
            self._data = data

        def open(self, mode):
            return FakeBlobStream(self._data)

    class FakeBucket:
        def __init__(self, blobs):
            self._blobs = blobs

        def blob(self, name):
            return self._blobs[name]

    class FakeGcsClient:
        def __init__(self):
            self._blobs = {
                "p/a.txt": FakeBlob("p/a.txt", b"alpha"),
                "p/b.txt": FakeBlob("p/b.txt", b"beta"),
            }

        def list_blobs(self, bucket, prefix=""):
            return [b for n, b in self._blobs.items() if n.startswith(prefix)]

        def bucket(self, name):
            return FakeBucket(self._blobs)

    src = gcs.GCSSource("my-bucket", "p/", client=FakeGcsClient())
    objects = src.list_objects()
    assert {o.capsule_filename for o in objects} == {"a.txt", "b.txt"}
    chunks = list(src.open_object(objects[0], 2))
    assert b"".join(chunks) == b"alpha"
    assert all(len(c) <= 2 for c in chunks)


def test_gcs_sink_with_injected_client_writes():
    from nanorix.connectors import gcs

    class FakeWriteStream:
        def __init__(self, sink_store, name):
            self._store = sink_store
            self._name = name
            self._buf = bytearray()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._store[self._name] = bytes(self._buf)
            return False

        def write(self, data):
            self._buf.extend(data)

    class FakeBlob:
        def __init__(self, store, name):
            self._store = store
            self._name = name

        def open(self, mode):
            return FakeWriteStream(self._store, self._name)

    class FakeBucket:
        def __init__(self, store):
            self._store = store

        def blob(self, name):
            return FakeBlob(self._store, name)

    class FakeGcsClient:
        def __init__(self):
            self.store: dict = {}

        def bucket(self, name):
            return FakeBucket(self.store)

    client = FakeGcsClient()
    sink = gcs.GCSSink("dest", "out/", client=client)

    def chunks():
        yield b"x"
        yield b"y"

    key = sink.write_object("r.json", chunks())
    assert key == "gs://dest/out/r.json"
    assert client.store["out/r.json"] == b"xy"


def test_s3_dest_key_no_prefix():
    from nanorix.connectors import s3

    class FakeBoto3Client:
        def put_object(self, **kw):
            pass

    sink = s3.S3Sink("b", client=FakeBoto3Client())
    assert sink._dest_key("file.json") == "file.json"


def test_s3_dest_key_with_prefix():
    from nanorix.connectors import s3

    class FakeBoto3Client:
        def put_object(self, **kw):
            pass

    sink = s3.S3Sink("b", "nested/path", client=FakeBoto3Client())
    assert sink._dest_key("file.json") == "nested/path/file.json"


def test_no_regulatory_claim_language_in_trust_note():
    """The connector trust note carries NO pass/fail compliance language."""
    forbidden = ["COMPLIANT", "SATISFIED", "PASSED", "MEETS"]
    upper = TRUST_BOUNDARY_NOTE.upper()
    for word in forbidden:
        assert word not in upper

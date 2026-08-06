"""Capsulefile resource tests."""
import httpx
import pytest
from nanorix import ConflictError, PayloadTooLargeError

TEST_API_KEY = "nrx_test_sk_test_1234567890abcdef"
TEST_BASE_URL = "https://api.nanorix.io"

CAPSULEFILE_RESPONSE = {
    "id": "cf_abc12345",
    "name": "test-model",
    "runtime": "python3.11",
    "entrypoint": "python model.py",
    "status": "finalized",
    "version": 1,
    "total_size_bytes": 1024,
    "content_hash": "abc123",
    "created_at": "2026-03-25T00:00:00Z",
    "files": [
        {"id": "cff_001", "filename": "model.py", "size_bytes": 512, "sha256": "sha_a"},
        {"id": "cff_002", "filename": "requirements.txt", "size_bytes": 512, "sha256": "sha_b"},
    ],
}


def test_create_capsulefile_three_step(mock_api, client):
    """Mock all 3 API calls and verify order."""
    mock_api.post("/v1/capsulefiles").mock(
        return_value=httpx.Response(200, json={"id": "cf_abc12345"})
    )
    mock_api.post("/v1/capsulefiles/cf_abc12345/files").mock(
        return_value=httpx.Response(200, json={"filename": "model.py", "size_bytes": 100, "sha256": "abc"})
    )
    mock_api.post("/v1/capsulefiles/cf_abc12345/finalize").mock(
        return_value=httpx.Response(200, json=CAPSULEFILE_RESPONSE)
    )

    cf = client.capsulefiles.create(
        name="test-model",
        runtime="python3.11",
        entrypoint="python model.py",
        files={"model.py": b"print('hello')"},
    )

    assert cf.id == "cf_abc12345"
    assert cf.name == "test-model"
    assert cf.status == "finalized"


def test_create_capsulefile_with_file_paths(mock_api, client, tmp_path):
    """Verify file reading from disk."""
    model_file = tmp_path / "model.py"
    model_file.write_text("print('test')")

    mock_api.post("/v1/capsulefiles").mock(
        return_value=httpx.Response(200, json={"id": "cf_xyz"})
    )
    mock_api.post("/v1/capsulefiles/cf_xyz/files").mock(
        return_value=httpx.Response(200, json={"filename": "model.py", "size_bytes": 14, "sha256": "x"})
    )
    mock_api.post("/v1/capsulefiles/cf_xyz/finalize").mock(
        return_value=httpx.Response(200, json={**CAPSULEFILE_RESPONSE, "id": "cf_xyz"})
    )

    cf = client.capsulefiles.create(
        name="test",
        runtime="python3.11",
        entrypoint="python model.py",
        files=[str(model_file)],
    )
    assert cf.id == "cf_xyz"


def test_create_capsulefile_with_bytes_dict(mock_api, client):
    """Verify bytes upload."""
    mock_api.post("/v1/capsulefiles").mock(
        return_value=httpx.Response(200, json={"id": "cf_bytes"})
    )
    mock_api.post("/v1/capsulefiles/cf_bytes/files").mock(
        return_value=httpx.Response(200, json={"filename": "x", "size_bytes": 5, "sha256": "y"})
    )
    mock_api.post("/v1/capsulefiles/cf_bytes/finalize").mock(
        return_value=httpx.Response(200, json={**CAPSULEFILE_RESPONSE, "id": "cf_bytes"})
    )

    cf = client.capsulefiles.create(
        name="test",
        runtime="python3.11",
        entrypoint="python model.py",
        files={"model.py": b"hello", "req.txt": b"numpy"},
    )
    assert cf.id == "cf_bytes"


def test_list_capsulefiles(mock_api, client):
    mock_api.get("/v1/capsulefiles").mock(
        return_value=httpx.Response(200, json={
            "capsulefiles": [CAPSULEFILE_RESPONSE],
            "has_more": False,
        })
    )

    result = client.capsulefiles.list()
    assert len(result.capsulefiles) == 1
    assert result.capsulefiles[0].id == "cf_abc12345"
    assert not result.has_more


def test_get_capsulefile(mock_api, client):
    mock_api.get("/v1/capsulefiles/cf_abc12345").mock(
        return_value=httpx.Response(200, json=CAPSULEFILE_RESPONSE)
    )

    cf = client.capsulefiles.get("cf_abc12345")
    assert cf.id == "cf_abc12345"
    assert cf.runtime == "python3.11"
    assert len(cf.files) == 2


def test_delete_capsulefile(mock_api, client):
    mock_api.delete("/v1/capsulefiles/cf_abc12345").mock(
        return_value=httpx.Response(204)
    )

    result = client.capsulefiles.delete("cf_abc12345")
    assert result is None


def test_wait_until_finalized_succeeds(mock_api, client):
    """Mock GET returning 'draft' then 'finalized'."""
    responses = [
        httpx.Response(200, json={**CAPSULEFILE_RESPONSE, "status": "draft"}),
        httpx.Response(200, json={**CAPSULEFILE_RESPONSE, "status": "finalized"}),
    ]
    mock_api.get("/v1/capsulefiles/cf_abc12345").mock(side_effect=responses)

    cf = client.capsulefiles.wait_until_finalized("cf_abc12345", poll_interval=0.01)
    assert cf.status == "finalized"


def test_create_capsulefile_duplicate_name_conflict(mock_api, client):
    mock_api.post("/v1/capsulefiles").mock(
        return_value=httpx.Response(409, json={"error": "Capsulefile name already exists"})
    )

    with pytest.raises(ConflictError, match="already exists"):
        client.capsulefiles.create(
            name="duplicate",
            runtime="python3.11",
            entrypoint="python x.py",
            files={"x.py": b"pass"},
        )


def test_create_capsulefile_exceeds_size_limit(mock_api, client):
    mock_api.post("/v1/capsulefiles").mock(
        return_value=httpx.Response(200, json={"id": "cf_big"})
    )
    mock_api.post("/v1/capsulefiles/cf_big/files").mock(
        return_value=httpx.Response(413, json={"error": "File exceeds tier limit"})
    )

    with pytest.raises(PayloadTooLargeError, match="tier limit"):
        client.capsulefiles.create(
            name="big",
            runtime="python3.11",
            entrypoint="python x.py",
            files={"huge.bin": b"x" * 100},
        )


def test_upload_dir(mock_api, client, tmp_path):
    """Upload_dir() walks a directory recursively and uploads each file."""
    # Create nested directory structure
    (tmp_path / "model.py").write_bytes(b"import torch")
    sub = tmp_path / "data"
    sub.mkdir()
    (sub / "train.csv").write_bytes(b"a,b,c")
    nested = sub / "nested"
    nested.mkdir()
    (nested / "config.json").write_bytes(b'{"lr": 0.01}')

    upload_calls = []

    def _capture(request, route):
        upload_calls.append(request)
        return httpx.Response(
            200,
            json={"filename": "ok", "size_bytes": 10, "sha256": "abc"},
        )

    mock_api.post("/v1/capsulefiles/cf_dir/files").mock(side_effect=_capture)

    count = client.capsulefiles.upload_dir("cf_dir", str(tmp_path))

    assert count == 3
    assert len(upload_calls) == 3

    # Verify relative paths were sent as the filename param
    uploaded_filenames = sorted(
        str(req.url.params.get("filename")) for req in upload_calls
    )
    assert uploaded_filenames == ["data/nested/config.json", "data/train.csv", "model.py"]


@pytest.mark.asyncio
async def test_upload_dir_async(tmp_path):
    """Async upload_dir() walks a directory and uploads each file."""
    import httpx
    import respx
    from nanorix import AsyncClient

    # Create directory structure
    (tmp_path / "main.py").write_bytes(b"print('hello')")
    sub = tmp_path / "lib"
    sub.mkdir()
    (sub / "utils.py").write_bytes(b"pass")

    upload_calls = []

    def _capture(request, route):
        upload_calls.append(request)
        return httpx.Response(
            200,
            json={"filename": "ok", "size_bytes": 10, "sha256": "abc"},
        )

    with respx.mock(base_url=TEST_BASE_URL) as router:
        router.post("/v1/capsulefiles/cf_async/files").mock(side_effect=_capture)
        async_client = AsyncClient(api_key=TEST_API_KEY, base_url=TEST_BASE_URL)

        count = await async_client.capsulefiles.upload_dir("cf_async", str(tmp_path))

        assert count == 2
        assert len(upload_calls) == 2

        uploaded_filenames = sorted(
            str(req.url.params.get("filename")) for req in upload_calls
        )
        assert uploaded_filenames == ["lib/utils.py", "main.py"]

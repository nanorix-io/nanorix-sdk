"""Capsule resource tests."""
import io
import httpx
import pytest
from nanorix import ExecutionError
from nanorix._models import CdpAccess

TEST_API_KEY = "nrx_test_sk_test_1234567890abcdef"
TEST_BASE_URL = "https://api.nanorix.io"

CAPSULE_RESPONSE = {
    "id": "cap_test001",
    "status": "active",
    "capsulefile_id": "cf_abc",
    "max_lifetime_seconds": 300,
    "created_at": "2026-03-25T00:00:00Z",
}

DESTROY_RESPONSE = {
    "cdp": {
        "capsule_id": "cap_test001",
        "activity": [],
        "destruction_trigger": "user_explicit",
    },
    "verification_cdp": {
        "capsule_id": "cap_test001",
        "verify": "https://nanorix.io/verify/cap_test001",
    },
}


def test_create_capsule(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )

    capsule = client.capsules.create()
    assert capsule.id == "cap_test001"
    assert capsule.status == "active"


def test_create_capsule_with_capsulefile(mock_api, client):
    route = mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )

    client.capsules.create(capsulefile="cf_abc")
    request = route.calls[0].request
    import json
    body = json.loads(request.content)
    assert body["capsulefile_id"] == "cf_abc"


def test_create_capsule_with_lifetime(mock_api, client):
    route = mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )

    client.capsules.create(max_lifetime_seconds=600)
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["max_lifetime_seconds"] == 600


def test_upload_file_bytes(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    upload_route = mock_api.post("/v1/capsules/cap_test001/upload").mock(
        return_value=httpx.Response(200, json={
            "filename": "data.csv", "size_bytes": 5, "sha256": "abc123"
        })
    )

    capsule = client.capsules.create()
    result = capsule.upload("data.csv", b"hello")
    assert result.filename == "data.csv"
    assert result.size_bytes == 5
    assert upload_route.calls[0].request.content == b"hello"


def test_upload_file_object(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.post("/v1/capsules/cap_test001/upload").mock(
        return_value=httpx.Response(200, json={
            "filename": "data.csv", "size_bytes": 11, "sha256": "xyz"
        })
    )

    capsule = client.capsules.create()
    file_obj = io.BytesIO(b"file content")
    result = capsule.upload("data.csv", file_obj)
    assert result.filename == "data.csv"


def test_execute_success(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.post("/v1/capsules/cap_test001/exec").mock(
        return_value=httpx.Response(200, json={
            "exit_code": 0, "stdout": "done\n", "stderr": "", "duration_seconds": 1.5
        })
    )

    capsule = client.capsules.create()
    result = capsule.execute("python model.py")
    assert result.exit_code == 0
    assert result.stdout == "done\n"
    assert result.duration_seconds == 1.5


def test_execute_nonzero_exit(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.post("/v1/capsules/cap_test001/exec").mock(
        return_value=httpx.Response(200, json={
            "exit_code": 1, "stdout": "", "stderr": "error\n"
        })
    )

    capsule = client.capsules.create()
    with pytest.raises(ExecutionError) as exc_info:
        capsule.execute("bad_command")
    assert exc_info.value.exit_code == 1
    assert exc_info.value.stderr == "error\n"


def test_download_output(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.get("/v1/capsules/cap_test001/output/result.json").mock(
        return_value=httpx.Response(200, content=b'{"score": 0.95}')
    )

    capsule = client.capsules.create()
    data = capsule.download("result.json")
    assert data == b'{"score": 0.95}'


def test_list_outputs(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.get("/v1/capsules/cap_test001/output").mock(
        return_value=httpx.Response(200, json={
            "files": [
                {"filename": "result.json", "size_bytes": 15, "sha256": "aaa"},
                {"filename": "log.txt", "size_bytes": 100, "sha256": "bbb"},
            ]
        })
    )

    capsule = client.capsules.create()
    outputs = capsule.list_outputs()
    assert len(outputs) == 2
    assert outputs[0].filename == "result.json"


def test_destroy_returns_cdp(mock_api, client):
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.delete("/v1/capsules/cap_test001").mock(
        return_value=httpx.Response(200, json=DESTROY_RESPONSE)
    )

    capsule = client.capsules.create()
    cdp = capsule.destroy()
    assert isinstance(cdp, CdpAccess)
    assert cdp.id == "cap_test001"
    full = cdp.full()
    assert full.capsule_id == "cap_test001"
    verify = cdp.verification()
    assert verify.capsule_id == "cap_test001"


def test_run_one_shot_full_lifecycle(mock_api, client):
    """Mock all 5 steps: start, upload, exec, list outputs, download, destroy."""
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.post("/v1/capsules/cap_test001/upload").mock(
        return_value=httpx.Response(200, json={
            "filename": "input.csv", "size_bytes": 5, "sha256": "in_hash"
        })
    )
    mock_api.post("/v1/capsules/cap_test001/exec").mock(
        return_value=httpx.Response(200, json={"exit_code": 0, "stdout": "ok"})
    )
    mock_api.get("/v1/capsules/cap_test001/output").mock(
        return_value=httpx.Response(200, json={
            "files": [{"filename": "out.json", "size_bytes": 10, "sha256": "out_hash"}]
        })
    )
    mock_api.get("/v1/capsules/cap_test001/output/out.json").mock(
        return_value=httpx.Response(200, content=b'{"result":1}')
    )
    mock_api.delete("/v1/capsules/cap_test001").mock(
        return_value=httpx.Response(200, json=DESTROY_RESPONSE)
    )

    outputs, cdp = client.capsules.run(
        capsulefile="cf_abc",
        inputs={"input.csv": b"data"},
        command="python model.py",
    )

    assert "out.json" in outputs
    assert outputs["out.json"] == b'{"result":1}'
    assert isinstance(cdp, CdpAccess)
    assert cdp.id == "cap_test001"


def test_run_with_execution_error_still_destroys(mock_api, client):
    """Verify destroy called on ExecutionError, CDP attached."""
    mock_api.post("/v1/capsules").mock(
        return_value=httpx.Response(200, json=CAPSULE_RESPONSE)
    )
    mock_api.post("/v1/capsules/cap_test001/exec").mock(
        return_value=httpx.Response(200, json={"exit_code": 1, "stderr": "fail"})
    )
    destroy_route = mock_api.delete("/v1/capsules/cap_test001").mock(
        return_value=httpx.Response(200, json=DESTROY_RESPONSE)
    )

    with pytest.raises(ExecutionError) as exc_info:
        client.capsules.run(capsulefile="cf_abc")

    assert exc_info.value.exit_code == 1
    assert exc_info.value.cdp is not None
    assert destroy_route.called

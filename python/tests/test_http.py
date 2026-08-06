"""HTTP transport tests."""
import httpx
import respx
from nanorix._http import HttpTransport
from nanorix._constants import USER_AGENT

TEST_API_KEY = "nrx_test_sk_test_1234567890abcdef"
TEST_BASE_URL = "https://api.nanorix.io"


def test_user_agent_header_sent():
    with respx.mock(base_url=TEST_BASE_URL) as router:
        route = router.get("/v1/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        transport = HttpTransport(api_key=TEST_API_KEY, base_url=TEST_BASE_URL)
        transport.request("GET", "/v1/health")

        request = route.calls[0].request
        assert request.headers["user-agent"] == USER_AGENT
        transport.close()


def test_bearer_auth_header_sent():
    with respx.mock(base_url=TEST_BASE_URL) as router:
        route = router.get("/v1/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )

        transport = HttpTransport(api_key=TEST_API_KEY, base_url=TEST_BASE_URL)
        transport.request("GET", "/v1/health")

        request = route.calls[0].request
        assert request.headers["authorization"] == f"Bearer {TEST_API_KEY}"
        transport.close()


def test_retry_on_429_then_success():
    with respx.mock(base_url=TEST_BASE_URL) as router:
        responses = [
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(200, json={"status": "ok"}),
        ]
        route = router.get("/v1/health").mock(side_effect=responses)

        transport = HttpTransport(
            api_key=TEST_API_KEY, base_url=TEST_BASE_URL, max_retries=3
        )
        result = transport.request("GET", "/v1/health")

        assert result == {"status": "ok"}
        assert route.call_count == 2
        transport.close()


def test_retry_on_500_then_success():
    with respx.mock(base_url=TEST_BASE_URL) as router:
        responses = [
            httpx.Response(500, json={"error": "internal"}),
            httpx.Response(200, json={"data": "recovered"}),
        ]
        route = router.get("/v1/test").mock(side_effect=responses)

        transport = HttpTransport(
            api_key=TEST_API_KEY, base_url=TEST_BASE_URL, max_retries=3
        )
        result = transport.request("GET", "/v1/test")

        assert result == {"data": "recovered"}
        assert route.call_count == 2
        transport.close()

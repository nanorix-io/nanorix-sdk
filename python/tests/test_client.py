"""Client initialization tests."""
import pytest
import respx
from nanorix import Client

TEST_BASE_URL = "https://api.nanorix.io"


def test_client_init_valid_live_key():
    with respx.mock(base_url=TEST_BASE_URL):
        c = Client(api_key="nrx_live_abc123def456", base_url=TEST_BASE_URL)
        assert c.capsulefiles is not None
        c.close()


def test_client_init_valid_test_key():
    with respx.mock(base_url=TEST_BASE_URL):
        c = Client(api_key="nrx_test_abc123def456", base_url=TEST_BASE_URL)
        assert c.capsules is not None
        c.close()


def test_client_init_invalid_key():
    with pytest.raises(ValueError, match="Invalid API key"):
        Client(api_key="sk_live_invalid")


def test_client_init_empty_key():
    with pytest.raises(ValueError, match="Invalid API key"):
        Client(api_key="")


def test_client_context_manager():
    with respx.mock(base_url=TEST_BASE_URL):
        with Client(api_key="nrx_test_abc123", base_url=TEST_BASE_URL) as c:
            assert c.capsulefiles is not None
            assert c.capsules is not None


def test_client_custom_base_url():
    custom_url = "https://custom.api.example.com"
    with respx.mock(base_url=custom_url):
        c = Client(api_key="nrx_test_abc123", base_url=custom_url)
        assert c._http._base_url == custom_url
        c.close()

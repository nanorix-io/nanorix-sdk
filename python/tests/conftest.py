import pytest
import respx
from nanorix import Client

TEST_API_KEY = "nrx_test_sk_test_1234567890abcdef"
TEST_BASE_URL = "https://api.nanorix.io"


@pytest.fixture
def mock_api():
    """Respx mock router for all tests."""
    with respx.mock(base_url=TEST_BASE_URL) as router:
        yield router


@pytest.fixture
def client(mock_api):
    """Pre-configured test client."""
    return Client(api_key=TEST_API_KEY, base_url=TEST_BASE_URL)

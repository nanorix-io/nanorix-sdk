import os

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


# ── Internal engine-name leak guard ──────────────────────────────────────
#
# Single source of truth for the engine codenames that must never reach
# customer-visible output or shipped module source. Every leak-guard test
# consumes the `engine_names` fixture; do not re-declare the list locally.
#
# Private CI can widen the set without this repository naming the extra
# codenames, by exporting a comma-separated list:
#
#     NANORIX_INTERNAL_ENGINE_NAMES="Alpha,Beta,Gamma"
#
# Injected names are added to the ones below, so a private build always
# asserts against a superset of what this repository checks.
#
# Give each name in canonical mixed case: guards match case-insensitively
# on a word boundary against module source, and as case-sensitive
# substrings against rendered output.

# Only names that are already public are listed here. EEE, DIRE, RZL and FGX
# are readable straight off the subsystem step names below, which are hashed
# into every signed AuditProof, so withholding them would buy nothing. The
# remaining internal codenames are supplied by private CI through the
# environment variable documented above.
ENGINE_NAMES = (
    "EEE",
    "DIRE",
    "RZL",
    "FGX",
    "ACE",
    "MCP",
)

# Subsystem step names carried in the signed AuditProof chain. These are
# public by construction — a customer verifies against them — so they are
# named here rather than injected.
ENGINE_STEP_NAMES = (
    "eee_namespace",
    "eee_tmpfs",
    "eee_memory",
    "dire_keys",
    "dire_identity",
    "fgx_forensic",
    "rzl_audit",
)

INJECTED_ENGINE_NAMES_ENV = "NANORIX_INTERNAL_ENGINE_NAMES"


def _injected_engine_names():
    raw = os.environ.get(INJECTED_ENGINE_NAMES_ENV, "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


@pytest.fixture(scope="session")
def engine_names():
    """Engine codenames that must never appear in customer-visible output."""
    return ENGINE_NAMES + _injected_engine_names()


@pytest.fixture(scope="session")
def engine_step_names():
    """Public AuditProof subsystem step names."""
    return ENGINE_STEP_NAMES

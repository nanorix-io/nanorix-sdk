"""SDK constants. Single source of truth for version and defaults."""

VERSION = "0.5.0"
DEFAULT_BASE_URL = "https://api.nanorix.io"
DEFAULT_TIMEOUT = 30.0          # seconds, for API calls
DEFAULT_UPLOAD_TIMEOUT = 300.0  # seconds, for file uploads
DEFAULT_EXECUTE_TIMEOUT = 600.0  # seconds, for code execution
MAX_RETRIES = 3
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
USER_AGENT = f"nanorix-python/{VERSION}"

/** SDK constants. Single source of truth for version and defaults. */

export const VERSION = "0.5.0";
export const DEFAULT_BASE_URL = "https://api.nanorix.io";
export const DEFAULT_TIMEOUT = 30_000; // ms, for API calls
export const DEFAULT_UPLOAD_TIMEOUT = 300_000; // ms, for file uploads
export const DEFAULT_EXECUTE_TIMEOUT = 600_000; // ms, for code execution
export const MAX_RETRIES = 3;
export const RETRY_STATUS_CODES = new Set([429, 500, 502, 503, 504]);
export const USER_AGENT = `nanorix-node/${VERSION}`;

/**
 * Wire-constraint helpers shared by all framework context classes.
 *
 * Mirrors `services/api/src/validation.rs::validate_metadata` so the
 * helper fails fast at construction rather than letting an oversized
 * payload reach the API. Reproduce the exact wire constraints; do not
 * loosen them client-side.
 *
 * Reserved-prefix discipline (post-G2 validation amendment):
 * the API whitelists `nanorix:framework` and `nanorix:framework_version`
 * as helper-emitted reserved keys. Customer-supplied `nanorix:*` keys
 * are rejected by the API. Helpers in this module emit ONLY those two
 * reserved keys via `RESERVED_FRAMEWORK_KEY` + `RESERVED_FRAMEWORK_VERSION_KEY`.
 */

export const MAX_METADATA_KEYS = 16;
export const MAX_KEY_LENGTH = 64;
export const MAX_VALUE_LENGTH = 256;
export const MAX_TOTAL_SERIALIZED = 4096;

/**
 * Reserved keys the helpers emit. Server validates these against
 * `RESERVED_NANORIX_KEYS_ALLOWED` whitelist in validation.rs. Adding a
 * new reserved key here requires a coordinated server-side amendment.
 */
export const RESERVED_FRAMEWORK_KEY = "nanorix:framework";
export const RESERVED_FRAMEWORK_VERSION_KEY = "nanorix:framework_version";

/** Throw if a metadata value exceeds the 256-char wire limit. */
export function validateValueLength(fieldName: string, value: string): void {
  if (value.length > MAX_VALUE_LENGTH) {
    throw new Error(
      `${fieldName} exceeds ${MAX_VALUE_LENGTH}-char limit ` +
        `(got ${value.length} chars; matches API validate_metadata)`,
    );
  }
}

/**
 * Throw if any item in a list contains `,` (the comma-join separator
 * used to flatten list-typed fields into Dict<string, string> values).
 */
export function validateNoCommaInList(fieldName: string, items: string[]): void {
  items.forEach((item, idx) => {
    if (item.includes(",")) {
      throw new Error(
        `${fieldName}[${idx}]=${JSON.stringify(item)} contains ',' which is the ` +
          `comma-join separator; remove or escape before passing`,
      );
    }
  });
}

/**
 * Estimate JSON-serialized size of a metadata object. Mirrors
 * `serde_json::to_string(metadata).len()` in validation.rs which checks
 * against the 4096-byte limit.
 */
export function estimateSerializedSize(
  metadata: Record<string, string>,
): number {
  return JSON.stringify(metadata).length;
}

/** Throw if metadata exceeds wire constraints (key count + total size). */
export function validateMetadataEnvelope(metadata: Record<string, string>): void {
  const keyCount = Object.keys(metadata).length;
  if (keyCount > MAX_METADATA_KEYS) {
    throw new Error(
      `toMetadata produced ${keyCount} keys, max ${MAX_METADATA_KEYS}`,
    );
  }
  const size = estimateSerializedSize(metadata);
  if (size > MAX_TOTAL_SERIALIZED) {
    throw new Error(
      `toMetadata serializes to ${size} bytes, max ${MAX_TOTAL_SERIALIZED}`,
    );
  }
}

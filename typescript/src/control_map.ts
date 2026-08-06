/**
 * control-map resolver (Wave B 2026-05-26) — published mapping artifact.
 *
 * Layer-2 published artifact `(jurisdiction × data_classification × pattern_tag)
 * → string[]` of canonical control IDs. Consumed by GRC adapters (Vanta /
 * Drata / Anecdotes / internal) at ingestion time given a receipt's signed
 * `pattern_tag` + customer's declared `jurisdiction` + capsule's
 * declared `data_classification` (customer-sole-declarer).
 *
 * ## Three-layer separation (RE-SCOPED — founder direction 2026-05-12)
 *
 * | Layer | Carries | Evolves at |
 * |---|---|---|
 * | Receipt | `pattern_tag` (signed) | Forever-Standard pace |
 * | Mapping artifact (this module) | `(j × c × p) → string[]` | Framework revision pace |
 * | Adapter | Reads receipt + applies mapping | Vendor pace |
 *
 * Stamping mapping output inside the cryptographic receipt would freeze
 * historical receipts with stale control IDs forever when frameworks revise.
 *
 * ## Discipline (CCO policy)
 *
 * - Control IDs are factual `references` — NOT assertions of satisfaction.
 * - Forbidden vocabulary: COMPLIANT / SATISFIED / PASSED / MEETS.
 * - Required relationship vocabulary: applies_to / references / related_to.
 * - Every adapter surface that consumes this module must surface the disclaimer.
 *
 * ## Usage
 *
 * ```typescript
 * import { resolve, loadMapping, MAPPING_2026_02 } from "@nanorix/sdk";
 *
 * // Option 1: helper resolver
 * const controlIds = resolve("US", "PHI", "pa");
 *
 * // Option 2: dict-style direct access via embedded constant
 * const ids = MAPPING_2026_02["US.PHI.pa"];
 *
 * // Option 3: load_mapping pinned to a framework_version
 * const mapping = loadMapping("2026-02");
 * const ids2 = mapping.resolve("US", "PHI", "pa");
 * ```
 */

/** Canonical framework version stamp. */
export const FRAMEWORK_VERSION = "2026-02" as const;

/** JSON Schema version for the published artifact format. */
export const SCHEMA_VERSION = "1.0" as const;

/** Canonical disclaimer per CCO policy + three-role discipline. */
export const DISCLAIMER =
  "Control IDs are references using canonical framework identifiers. " +
  "Whether evidence is acceptable for a control is determined by the " +
  "customer's compliance program and the auditor's adjudication, not " +
  "by Nanorix.";

/**
 * Canonical mapping for framework_version 2026-02. Append-only.
 *
 * Mirrors the Rust resolver in the Nanorix API service
 * and Python `nanorix.control_map.MAPPING_2026_02` — single source of truth.
 */
export const MAPPING_2026_02: Readonly<Record<string, readonly string[]>> = {
  // ── Pattern 1 — Prior Authorization (PA) decisions ──
  // Anterior, Latent Health, Itiliti Health, Humata Health
  "US.PHI.pa": [
    "HIPAA-164.312(b)",
    "HIPAA-164.312(c)",
    "URAC-UM-AC-1",
    "URAC-UM-AC-2",
    "NCQA-UM-5",
    "NCQA-UM-7",
    "ERISA-503(2)",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],
  "EU.PHI.pa": [
    "GDPR-Art-30",
    "GDPR-Art-32",
    "EU-AI-Act-Art-26",
    "ISO-42001-8.6",
    "EHDS-Art-73",
    "SOC2-CC7.2",
  ],

  // ── Pattern 4 — RCM claim, eligibility, remit ──
  // Adonis, Candid, Nym, Fathom, Cair, Sohar
  "US.PHI.rcm_claim": [
    "HIPAA-164.312(b)",
    "HIPAA-164.312(c)",
    "HITRUST-02.e",
    "CMS-CoP-482.24",
    "ACA-1557",
    "NIST-800-53-AU-2",
    "SOC2-CC7.2",
  ],
  "US.Financial.rcm_claim": [
    "HIPAA-164.312(b)",
    "PCI-DSS-10.2",
    "HITRUST-02.e",
    "SOX-302(a)(4)",
    "SOC2-CC7.2",
  ],
  "US.PHI.rcm_eligibility": [
    "HIPAA-164.312(b)",
    "HITRUST-02.e",
    "CMS-CoP-482.24",
    "NIST-800-53-AU-2",
    "SOC2-CC7.2",
  ],
  "US.PHI.rcm_remit": [
    "HIPAA-164.312(b)",
    "HITRUST-02.e",
    "PCI-DSS-10.2",
    "NIST-800-53-AU-2",
    "SOC2-CC7.2",
  ],

  // ── Pattern 4 — NCPDP e-prescription — Banjo Health ──
  "US.PHI.ncpdp_script": [
    "HIPAA-164.312(b)",
    "DEA-21CFR1311.300",
    "NCPDP-D.0",
    "NIST-800-53-AU-2",
    "SOC2-CC7.2",
  ],

  // ── Pattern 6 — DICOM imaging — Overjet, Cleerly, PathAI ──
  "US.PHI.dicom_study": [
    "HIPAA-164.312(b)",
    "FDA-21CFR820.198",
    "FDA-21CFR11.10(e)",
    "ACR-PP",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],
  "EU.PHI.dicom_study": [
    "GDPR-Art-30",
    "GDPR-Art-32",
    "EU-MDR-Art-87",
    "EU-AI-Act-Art-26",
    "EHDS-Art-73",
    "SOC2-CC7.2",
  ],
  "US.PHI.dicom_sr": [
    "HIPAA-164.312(b)",
    "FDA-21CFR820.198",
    "FDA-21CFR11.10(e)",
    "ACR-PP",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],
  "EU.PHI.dicom_sr": [
    "GDPR-Art-30",
    "GDPR-Art-32",
    "EU-MDR-Art-87",
    "EU-AI-Act-Art-26",
    "EHDS-Art-73",
    "SOC2-CC7.2",
  ],

  // ── Pattern 8 — long-horizon agent workloads ──
  // Itiliti, Humata, Hippocratic AI
  "US.PHI.agent_step": [
    "HIPAA-164.312(b)",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],
  "EU.PHI.agent_step": [
    "GDPR-Art-30",
    "EU-AI-Act-Art-26",
    "EU-AI-Act-Art-15",
    "ISO-42001-8.6",
    "SOC2-CC7.2",
  ],
  "US.PHI.agent_turn": [
    "HIPAA-164.312(b)",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],
  "EU.PHI.agent_turn": [
    "GDPR-Art-30",
    "EU-AI-Act-Art-26",
    "EU-AI-Act-Art-50",
    "ISO-42001-8.6",
    "SOC2-CC7.2",
  ],

  // ── R&D screening — Recursion, Insitro, Atomwise ──
  "US.General.screening_hit": [
    "FDA-21CFR11.10(e)",
    "21CFR-Part-58-GLP",
    "NIST-800-53-AU-2",
    "SOC2-CC7.2",
  ],

  // ── FHIR record — MDClone, Verata, Beacon, SmarterDx, Pieces ──
  "US.PHI.fhir_record": [
    "HIPAA-164.312(b)",
    "HL7-FHIR-Audit",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],

  // ── EHR document ──
  "US.PHI.ehr_document": [
    "HIPAA-164.312(b)",
    "HITECH-13402",
    "SOC2-CC7.2",
  ],

  // ── Extraction — Atria HCC coding, Pattern 3 ──
  "US.PHI.extraction": [
    "HIPAA-164.312(b)",
    "NIST-AI-RMF-MAP-4.1",
    "SOC2-CC7.2",
  ],
  "EU.PHI.extraction": [
    "GDPR-Art-30",
    "GDPR-Art-32",
    "EU-AI-Act-Art-26",
    "ISO-42001-8.6",
    "SOC2-CC7.2",
  ],

  // ── Baseline cells (fallback when no specific pattern cell seeded) ──
  "_baseline.US.PII": ["GDPR-Art-30-equivalent", "SOC2-CC7.2"],
  "_baseline.EU.PII": ["GDPR-Art-30", "GDPR-Art-32", "SOC2-CC7.2"],
  "_baseline.CA.PII": ["CCPA-1798.130", "CPRA-1798.140", "SOC2-CC7.2"],
  "_baseline.US.Financial": [
    "PCI-DSS-10.2",
    "SOX-302(a)(4)",
    "SOC2-CC7.2",
  ],
  "_baseline.US.Credentials": ["NIST-800-53-IA-5", "SOC2-CC6.1"],
};

/**
 * Registry of available framework_versions. Append-only —
 * old versions remain forever in future SDK releases.
 */
const MAPPINGS_BY_VERSION: Readonly<
  Record<string, Readonly<Record<string, readonly string[]>>>
> = {
  "2026-02": MAPPING_2026_02,
};

/**
 * Customer-declared jurisdiction (customer-sole-declarer).
 */
export type Jurisdiction = "US" | "EU" | "UK" | "CA" | "AU" | "IN" | "OTHER";

/**
 * Customer-declared data classification (customer-sole-declarer).
 */
export type DataClassification =
  | "PHI"
  | "PII"
  | "Financial"
  | "Credentials"
  | "General";

/**
 * Receipt's signed pattern_tag. Closed-enum wire form (snake_case).
 */
export type PatternTag =
  | "pa"
  | "extraction"
  | "annotation"
  | "agent_step"
  | "agent_turn"
  | "rcm_claim"
  | "rcm_eligibility"
  | "rcm_remit"
  | "ncpdp_script"
  | "dicom_study"
  | "dicom_sr"
  | "screening_hit"
  | "fhir_record"
  | "ehr_document"
  | "custom";

/**
 * Pinned mapping at a specific framework_version. Returned by `loadMapping()`.
 *
 * Discipline: this is layer-2 of the three-layer separation. The mapping is
 * applied at ADAPTER TIME, never stamped into the signed receipt.
 */
export class Mapping {
  readonly frameworkVersion: string;
  readonly schemaVersion: string = SCHEMA_VERSION;
  readonly disclaimer: string = DISCLAIMER;
  readonly mappings: Readonly<Record<string, readonly string[]>>;

  constructor(
    frameworkVersion: string,
    mappings: Readonly<Record<string, readonly string[]>>,
  ) {
    this.frameworkVersion = frameworkVersion;
    this.mappings = mappings;
  }

  /**
   * Resolve canonical control IDs for a specific cell.
   *
   * Customer-sole-declarer: arguments are customer-declared values
   * consumed verbatim. Nanorix does NOT infer jurisdiction or classification.
   *
   * @param jurisdiction Customer-declared jurisdiction.
   * @param classification Customer-declared data_classification.
   * @param patternTag Receipt's signed pattern_tag.
   * @returns Canonical control IDs for the cell. Empty when cell is not seeded.
   */
  resolve(
    jurisdiction: string,
    classification: string,
    patternTag: string,
  ): string[] {
    const cls = canonicalizeClassification(classification);
    const key = `${jurisdiction}.${cls}.${patternTag}`;
    const ids = this.mappings[key];
    return ids ? [...ids] : [];
  }

  /**
   * Baseline fallback for a `(jurisdiction × classification)` cell.
   *
   * Used when `resolve()` returns an empty list for a specific `pattern_tag`
   * cell. Returns the `_baseline.{jurisdiction}.{classification}` cell.
   */
  resolveBaseline(jurisdiction: string, classification: string): string[] {
    const cls = canonicalizeClassification(classification);
    const key = `_baseline.${jurisdiction}.${cls}`;
    const ids = this.mappings[key];
    return ids ? [...ids] : [];
  }
}

function canonicalizeClassification(classification: string): string {
  const lc = classification.toLowerCase();
  if (lc === "phi") return "PHI";
  if (lc === "pii") return "PII";
  if (lc === "financial") return "Financial";
  if (lc === "credentials") return "Credentials";
  if (lc === "general") return "General";
  return classification;
}

/**
 * Load a pinned mapping at the given framework_version.
 *
 * @param frameworkVersion Framework version (defaults to current canonical).
 * @returns Mapping pinned to the requested version.
 * @throws Error if `frameworkVersion` is not available.
 */
export function loadMapping(frameworkVersion?: string): Mapping {
  const fv = frameworkVersion ?? FRAMEWORK_VERSION;
  const mappings = MAPPINGS_BY_VERSION[fv];
  if (!mappings) {
    const available = Object.keys(MAPPINGS_BY_VERSION).sort().join(", ");
    throw new Error(
      `framework_version '${fv}' not available. ` +
        `Available versions: [${available}]. ` +
        `Forever-Standard, old versions remain available ` +
        `forever; ensure you are using a published version.`,
    );
  }
  return new Mapping(fv, mappings);
}

/**
 * Convenience accessor — resolve canonical control IDs for a cell.
 *
 * Equivalent to `loadMapping(frameworkVersion).resolve(...)`.
 *
 * @example
 *   const ids = resolve("US", "PHI", "pa");
 *   // ids = ["HIPAA-164.312(b)", "URAC-UM-AC-1", ...]
 */
export function resolve(
  jurisdiction: string,
  classification: string,
  patternTag: string,
  frameworkVersion?: string,
): string[] {
  return loadMapping(frameworkVersion).resolve(
    jurisdiction,
    classification,
    patternTag,
  );
}

/**
 * List all available framework_versions in this SDK release.
 *
 * Forever-Standard: this list is append-only across SDK
 * releases — old framework_versions are NEVER removed.
 */
export function availableFrameworkVersions(): string[] {
  return Object.keys(MAPPINGS_BY_VERSION).sort();
}

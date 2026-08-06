/** SOC 2 / Trust Services Criteria 2017 context for capsule workloads. */

import {
  RESERVED_FRAMEWORK_KEY,
  RESERVED_FRAMEWORK_VERSION_KEY,
  validateMetadataEnvelope,
  validateNoCommaInList,
  validateValueLength,
} from "./_constraints.js";

const FRAMEWORK_VERSION = "TSC-2017";

/** SOC 2 Trust Services Criteria — closed set per AICPA TSC 2017. */
export type Soc2TrustServiceCriterion =
  | "security"
  | "availability"
  | "processing_integrity"
  | "confidentiality"
  | "privacy";

const VALID_TRUST_SERVICE_CRITERIA: ReadonlySet<Soc2TrustServiceCriterion> =
  new Set<Soc2TrustServiceCriterion>([
    "security",
    "availability",
    "processing_integrity",
    "confidentiality",
    "privacy",
  ]);

export interface Soc2ContextOptions {
  /** The service organization receiving the SOC 2 audit. Required. */
  serviceOrganization: string;
  /**
   * Subset of: `security`, `availability`, `processing_integrity`,
   * `confidentiality`, `privacy`. Stored comma-joined in the metadata
   * string value.
   */
  trustServiceCriteria: Soc2TrustServiceCriterion[];
  /**
   * Customer's classification (e.g., `'tenant_data'`, `'production'`).
   * Required (empty value rejected at construction).
   */
  dataClassification: string;
  /** Per-customer tag when the workload is dedicated to one customer. */
  customerId?: string;
  /** Comma-separated names of subservice organizations. */
  subserviceOrganizations?: string;
  /** Optional period tag (e.g., `'2026-Q2'`). */
  auditPeriod?: string;
}

export class Soc2Context {
  readonly serviceOrganization: string;
  readonly trustServiceCriteria: ReadonlyArray<Soc2TrustServiceCriterion>;
  readonly dataClassification: string;
  readonly customerId?: string;
  readonly subserviceOrganizations?: string;
  readonly auditPeriod?: string;

  constructor(opts: Soc2ContextOptions) {
    if (!opts.serviceOrganization) {
      throw new Error("serviceOrganization is required and must be non-empty");
    }
    if (!opts.trustServiceCriteria || opts.trustServiceCriteria.length === 0) {
      throw new Error(
        "trustServiceCriteria is required and must be non-empty " +
          "(pick at least one of: security, availability, processing_integrity, " +
          "confidentiality, privacy)",
      );
    }
    opts.trustServiceCriteria.forEach((item, idx) => {
      if (typeof item !== "string") {
        throw new Error(
          `trustServiceCriteria[${idx}] must be a string ` +
            `(got ${typeof item})`,
        );
      }
      if (!VALID_TRUST_SERVICE_CRITERIA.has(item)) {
        throw new Error(
          `trustServiceCriteria[${idx}]=${JSON.stringify(item)} not in ` +
            `${JSON.stringify(Array.from(VALID_TRUST_SERVICE_CRITERIA))}`,
        );
      }
    });
    validateNoCommaInList("trustServiceCriteria", [...opts.trustServiceCriteria]);
    if (!opts.dataClassification) {
      throw new Error("dataClassification must be non-empty");
    }

    validateValueLength("serviceOrganization", opts.serviceOrganization);
    validateValueLength("dataClassification", opts.dataClassification);
    const joined = opts.trustServiceCriteria.join(",");
    validateValueLength("trustServiceCriteria(joined)", joined);
    if (opts.customerId !== undefined) {
      validateValueLength("customerId", opts.customerId);
    }
    if (opts.subserviceOrganizations !== undefined) {
      validateValueLength(
        "subserviceOrganizations",
        opts.subserviceOrganizations,
      );
    }
    if (opts.auditPeriod !== undefined) {
      validateValueLength("auditPeriod", opts.auditPeriod);
    }

    this.serviceOrganization = opts.serviceOrganization;
    this.trustServiceCriteria = Object.freeze([...opts.trustServiceCriteria]);
    this.dataClassification = opts.dataClassification;
    this.customerId = opts.customerId;
    this.subserviceOrganizations = opts.subserviceOrganizations;
    this.auditPeriod = opts.auditPeriod;

    Object.freeze(this);
  }

  toMetadata(): Record<string, string> {
    const meta: Record<string, string> = {
      [RESERVED_FRAMEWORK_KEY]: "SOC 2",
      [RESERVED_FRAMEWORK_VERSION_KEY]: FRAMEWORK_VERSION,
      service_organization: this.serviceOrganization,
      trust_service_criteria: this.trustServiceCriteria.join(","),
      data_classification: this.dataClassification,
    };
    if (this.customerId !== undefined) meta.customer_id = this.customerId;
    if (this.subserviceOrganizations !== undefined) {
      meta.subservice_organizations = this.subserviceOrganizations;
    }
    if (this.auditPeriod !== undefined) meta.audit_period = this.auditPeriod;
    validateMetadataEnvelope(meta);
    return meta;
  }
}

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { NanorixClient } from "../src/client.js";
import { GdprContext, HipaaContext, Soc2Context } from "../src/compliance/index.js";
import { FetchMock, TEST_API_KEY } from "./setup.js";

const MOCK_CAPSULE = {
  id: "cap_1",
  status: "active",
  customer_id: "cust_1",
  capsulefile_id: "cf_1",
  created_at: "2026-05-03T00:00:00Z",
};

// ── HipaaContext ───────────────────────────────────────────────

describe("HipaaContext", () => {
  it("emits required reserved keys + customer fields with defaults", () => {
    const ctx = new HipaaContext({
      coveredEntity: "Acme Hospital",
      minimumNecessaryPurpose: "Radiology inference",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("HIPAA");
    expect(meta["nanorix:framework_version"]).toBe("2013-Omnibus");
    expect(meta.covered_entity).toBe("Acme Hospital");
    expect(meta.minimum_necessary_purpose).toBe("Radiology inference");
    expect(meta.data_classification).toBe("PHI"); // default
    expect(meta).not.toHaveProperty("business_associate");
    expect(meta).not.toHaveProperty("baa_reference");
    expect(meta).not.toHaveProperty("workforce_member_role");
  });

  it("emits all optional fields when set", () => {
    const ctx = new HipaaContext({
      coveredEntity: "Acme Hospital",
      minimumNecessaryPurpose: "Radiology",
      dataClassification: "PHI-radiology",
      businessAssociate: "ba_12345",
      baaReference: "BAA-2026-Q2",
      workforceMemberRole: "attending_radiologist",
    });
    const meta = ctx.toMetadata();
    expect(meta.data_classification).toBe("PHI-radiology");
    expect(meta.business_associate).toBe("ba_12345");
    expect(meta.baa_reference).toBe("BAA-2026-Q2");
    expect(meta.workforce_member_role).toBe("attending_radiologist");
  });

  it("rejects empty required fields at construction", () => {
    expect(
      () =>
        new HipaaContext({
          coveredEntity: "",
          minimumNecessaryPurpose: "X",
        }),
    ).toThrowError(/coveredEntity/);
    expect(
      () =>
        new HipaaContext({
          coveredEntity: "X",
          minimumNecessaryPurpose: "",
        }),
    ).toThrowError(/minimumNecessaryPurpose/);
  });

  it("rejects oversized values at construction", () => {
    expect(
      () =>
        new HipaaContext({
          coveredEntity: "A".repeat(257),
          minimumNecessaryPurpose: "X",
        }),
    ).toThrowError(/256-char limit/);
  });

  it("frozen after construction (mutation has no effect or throws)", () => {
    const ctx = new HipaaContext({
      coveredEntity: "A",
      minimumNecessaryPurpose: "X",
    });
    expect(() => {
      // @ts-expect-error — readonly + Object.freeze
      ctx.coveredEntity = "B";
    }).toThrow();
  });

  it("metadata round-trips via JSON.stringify / JSON.parse", () => {
    const ctx = new HipaaContext({
      coveredEntity: "Acme",
      minimumNecessaryPurpose: "X",
      businessAssociate: "ba_1",
    });
    const meta = ctx.toMetadata();
    expect(JSON.parse(JSON.stringify(meta))).toEqual(meta);
  });
});

// ── GdprContext ────────────────────────────────────────────────

describe("GdprContext", () => {
  it("emits required keys + closed-enum legal basis", () => {
    const ctx = new GdprContext({
      dataController: "Acme GmbH",
      legalBasis: "contract",
      processingPurpose: "Customer support automation",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("GDPR");
    expect(meta["nanorix:framework_version"]).toBe("2016-679");
    expect(meta.data_controller).toBe("Acme GmbH");
    expect(meta.legal_basis).toBe("contract");
    expect(meta.data_classification).toBe("personal_data");
  });

  it("rejects legal basis outside Article 6(1) closed enum", () => {
    expect(
      () =>
        new GdprContext({
          dataController: "X",
          // @ts-expect-error — testing runtime rejection of non-enum value
          legalBasis: "hand_wave",
          processingPurpose: "X",
        }),
    ).toThrowError(/Article 6/);
  });

  it("accepts all six valid legal bases", () => {
    const bases: ("consent" | "contract" | "legal_obligation" |
      "vital_interests" | "public_task" | "legitimate_interests")[] = [
      "consent",
      "contract",
      "legal_obligation",
      "vital_interests",
      "public_task",
      "legitimate_interests",
    ];
    for (const basis of bases) {
      const ctx = new GdprContext({
        dataController: "X",
        legalBasis: basis,
        processingPurpose: "Y",
      });
      expect(ctx.toMetadata().legal_basis).toBe(basis);
    }
  });
});

// ── Soc2Context ────────────────────────────────────────────────

describe("Soc2Context", () => {
  it("emits required keys + comma-joined trust service criteria", () => {
    const ctx = new Soc2Context({
      serviceOrganization: "Acme SaaS",
      trustServiceCriteria: ["security", "availability"],
      dataClassification: "tenant_data",
    });
    const meta = ctx.toMetadata();
    expect(meta["nanorix:framework"]).toBe("SOC 2");
    expect(meta["nanorix:framework_version"]).toBe("TSC-2017");
    expect(meta.service_organization).toBe("Acme SaaS");
    expect(meta.trust_service_criteria).toBe("security,availability");
    expect(meta.data_classification).toBe("tenant_data");
  });

  it("accepts all five trust criteria", () => {
    const ctx = new Soc2Context({
      serviceOrganization: "X",
      trustServiceCriteria: [
        "security",
        "availability",
        "processing_integrity",
        "confidentiality",
        "privacy",
      ],
      dataClassification: "X",
    });
    expect(ctx.toMetadata().trust_service_criteria).toBe(
      "security,availability,processing_integrity,confidentiality,privacy",
    );
  });

  it("rejects invalid trust criterion", () => {
    expect(
      () =>
        new Soc2Context({
          serviceOrganization: "X",
          // @ts-expect-error — testing runtime rejection
          trustServiceCriteria: ["security", "compliance"],
          dataClassification: "X",
        }),
    ).toThrowError(/trustServiceCriteria/);
  });

  it("rejects empty trust criteria list", () => {
    expect(
      () =>
        new Soc2Context({
          serviceOrganization: "X",
          trustServiceCriteria: [],
          dataClassification: "X",
        }),
    ).toThrowError(/trustServiceCriteria/);
  });
});

// ── Capsules.create() metadata plumbing ────────────────────────

describe("Capsules.create() metadata plumbing", () => {
  const mock = new FetchMock();
  let client: NanorixClient;

  beforeEach(() => {
    mock.reset();
    client = new NanorixClient({ apiKey: TEST_API_KEY });
  });

  afterEach(() => {
    mock.restore();
  });

  it("threads HipaaContext metadata into request body", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.install();

    const ctx = new HipaaContext({
      coveredEntity: "Acme Hospital",
      minimumNecessaryPurpose: "Radiology",
    });
    await client.capsules.create({
      capsulefile: "cf_1",
      metadata: ctx.toMetadata(),
    });

    const body = mock.calls[0].body as Record<string, unknown>;
    const metadata = body.metadata as Record<string, string>;
    expect(metadata["nanorix:framework"]).toBe("HIPAA");
    expect(metadata.covered_entity).toBe("Acme Hospital");
  });

  it("omits metadata field when not provided", async () => {
    mock.on("POST", "/v1/capsules", { status: 200, body: MOCK_CAPSULE });
    mock.install();

    await client.capsules.create({ capsulefile: "cf_1" });
    const body = mock.calls[0].body as Record<string, unknown>;
    expect(body).not.toHaveProperty("metadata");
  });
});

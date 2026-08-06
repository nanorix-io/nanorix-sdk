/**
 * Main client class. Users interact with this.
 *
 * Usage:
 *   import { NanorixClient } from "@nanorix/sdk";
 *
 *   const client = new NanorixClient({ apiKey: "nrx_live_..." });
 *   const capsulefile = await client.capsulefiles.create({ ... });
 *   const [outputs, cdp] = await client.capsules.run({ capsulefile: capsulefile.id, ... });
 */
import { Billing } from "./billing.js";
import { HttpTransport } from "./http.js";
import { Capsulefiles } from "./resources/capsulefiles.js";
import { Capsules } from "./resources/capsules.js";
import { CdpResource } from "./resources/cdp.js";
import { EgressProfiles } from "./resources/egress-profiles.js";
import { Usage } from "./resources/usage.js";
import { Webhooks } from "./resources/webhooks.js";
import {
  DEFAULT_BASE_URL,
  DEFAULT_TIMEOUT,
  MAX_RETRIES,
} from "./constants.js";

export interface NanorixClientOptions {
  apiKey: string;
  baseUrl?: string;
  timeout?: number;
  maxRetries?: number;
}

export class NanorixClient {
  // Amendment 2: store HttpTransport as private member for .health()
  private readonly _http: HttpTransport;
  readonly capsulefiles: Capsulefiles;
  readonly capsules: Capsules;
  readonly cdp: CdpResource;
  /** brand alias for `cdp`. Same instance; same interface. */
  readonly auditProofs: CdpResource;
  readonly egressProfiles: EgressProfiles;
  readonly usage: Usage;
  /**
   * Billing meter readout
   * surface mirroring Stripe metered billing for programmatic
   * consumption. By-pattern · by-shape · cost-forecast endpoints.
   */
  readonly billing: Billing;
  readonly webhooks: Webhooks;

  // No .close() needed — native fetch has no persistent connection pool.
  // This is an intentional difference from the Python SDK (which uses httpx).

  constructor(opts: NanorixClientOptions) {
    if (
      !opts.apiKey ||
      opts.apiKey.length < 20 ||
      (!opts.apiKey.startsWith("nrx_live_") &&
        !opts.apiKey.startsWith("nrx_test_"))
    ) {
      throw new Error(
        "Invalid API key. Keys must start with 'nrx_live_' or 'nrx_test_'. " +
          "Get your key at https://app.nanorix.io/settings/api-keys",
      );
    }

    const baseUrl = opts.baseUrl ?? DEFAULT_BASE_URL;
    // Enforce HTTPS to prevent API key leakage over plaintext.
    // Allow http:// only for localhost/127.0.0.1 (local dev).
    try {
      const parsed = new URL(baseUrl);
      if (
        parsed.protocol !== "https:" &&
        parsed.hostname !== "localhost" &&
        parsed.hostname !== "127.0.0.1"
      ) {
        throw new Error(
          `baseUrl must use HTTPS (got ${parsed.protocol}). ` +
            "HTTP is only allowed for localhost development.",
        );
      }
    } catch (err) {
      if (err instanceof TypeError) {
        throw new Error(`Invalid baseUrl: ${baseUrl}`);
      }
      throw err;
    }

    this._http = new HttpTransport({
      apiKey: opts.apiKey,
      baseUrl,
      timeout: opts.timeout ?? DEFAULT_TIMEOUT,
      maxRetries: opts.maxRetries ?? MAX_RETRIES,
    });

    this.capsulefiles = new Capsulefiles(this._http);
    this.capsules = new Capsules(this._http);
    this.cdp = new CdpResource(this._http);
    this.auditProofs = this.cdp;
    this.egressProfiles = new EgressProfiles(this._http);
    this.usage = new Usage(this._http);
    this.billing = new Billing(this._http);
    this.webhooks = new Webhooks(this._http);
  }

  /** Check API health status. GET /v1/health. */
  async health(): Promise<Record<string, unknown>> {
    return await this._http.request("GET", "/v1/health");
  }
}

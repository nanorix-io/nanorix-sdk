/** CDP resource — get full+verification CDPs, verify publicly. */
import type { VerificationCdp } from "../models.js";
import { CdpAccess } from "../models.js";
import type { HttpTransport } from "../http.js";

export class CdpResource {
  constructor(private readonly http: HttpTransport) {}

  /** Get CDP for a destroyed capsule (requires auth). */
  async get(capsuleId: string): Promise<CdpAccess> {
    const full = await this.http.request(
      "GET",
      `/v1/capsules/${capsuleId}/cdp`,
    );
    const verify = await this.http.request(
      "GET",
      `/v1/capsules/${capsuleId}/cdp/verify`,
    );
    return new CdpAccess(full, verify, capsuleId);
  }

  /** Get verification CDP (public, no auth required). */
  async verify(capsuleId: string): Promise<VerificationCdp> {
    return (await this.http.request(
      "GET",
      `/v1/capsules/${capsuleId}/cdp/verify`,
    )) as unknown as VerificationCdp;
  }
}

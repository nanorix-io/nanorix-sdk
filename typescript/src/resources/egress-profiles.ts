/** Egress profiles resource — named groups of egress rules. */
import type { HttpTransport } from "../http.js";

export interface EgressProfile {
  id: string;
  name: string;
  description?: string;
  rule_count: number;
  created_at: string;
  updated_at: string;
}

export interface EgressProfileDetail {
  id: string;
  name: string;
  description?: string;
  rules: EgressRule[];
  created_at: string;
  updated_at: string;
}

export interface EgressRule {
  id: string;
  domain_pattern: string;
  port: number;
  description?: string;
  created_at: string;
}

export class EgressProfiles {
  constructor(private readonly _http: HttpTransport) {}

  /** Create a named egress profile. Business+ only. */
  async create(opts: {
    name: string;
    description?: string;
  }): Promise<EgressProfile> {
    const body: Record<string, unknown> = { name: opts.name };
    if (opts.description !== undefined) body.description = opts.description;
    const result = await this._http.request("POST", "/v1/egress/profiles", {
      json: body,
    });
    return result as unknown as EgressProfile;
  }

  /** List all egress profiles for the account. */
  async list(): Promise<EgressProfile[]> {
    const result = await this._http.request("GET", "/v1/egress/profiles");
    return result as unknown as EgressProfile[];
  }

  /** Get a profile with its rules. */
  async get(profileId: string): Promise<EgressProfileDetail> {
    const result = await this._http.request(
      "GET",
      `/v1/egress/profiles/${profileId}`,
    );
    return result as unknown as EgressProfileDetail;
  }

  /** Update a profile's name or description. */
  async update(
    profileId: string,
    opts: { name?: string; description?: string },
  ): Promise<EgressProfile> {
    const body: Record<string, unknown> = {};
    if (opts.name !== undefined) body.name = opts.name;
    if (opts.description !== undefined) body.description = opts.description;
    const result = await this._http.request(
      "PUT",
      `/v1/egress/profiles/${profileId}`,
      { json: body },
    );
    return result as unknown as EgressProfile;
  }

  /** Delete a profile. Fails with 409 if active capsules reference it. */
  async delete(profileId: string): Promise<void> {
    await this._http.request("DELETE", `/v1/egress/profiles/${profileId}`);
  }

  /** Add a rule to a profile. */
  async addRule(
    profileId: string,
    opts: { domainPattern: string; port?: number; description?: string },
  ): Promise<EgressRule> {
    const body: Record<string, unknown> = {
      domain_pattern: opts.domainPattern,
      port: opts.port ?? 443,
    };
    if (opts.description !== undefined) body.description = opts.description;
    const result = await this._http.request(
      "POST",
      `/v1/egress/profiles/${profileId}/rules`,
      { json: body },
    );
    return result as unknown as EgressRule;
  }

  /** List rules in a profile. */
  async listRules(profileId: string): Promise<EgressRule[]> {
    const result = await this._http.request(
      "GET",
      `/v1/egress/profiles/${profileId}/rules`,
    );
    return result as unknown as EgressRule[];
  }

  /** Remove a rule from a profile. */
  async removeRule(profileId: string, ruleId: string): Promise<void> {
    await this._http.request(
      "DELETE",
      `/v1/egress/profiles/${profileId}/rules/${ruleId}`,
    );
  }
}

/** Webhook endpoint management + signature verification. */
import type { HttpTransport } from "../http.js";
import { createHmac, timingSafeEqual } from "crypto";

export interface WebhookEndpoint {
  id: string;
  url: string;
  events: string[];
  description?: string;
  active: boolean;
  secret?: string; // Only present on create response
  created_at: string;
  updated_at: string;
}

export interface WebhookDelivery {
  id: string;
  event_type: string;
  response_status?: number;
  attempt: number;
  delivered_at?: string;
  failed_at?: string;
  created_at: string;
}

export class Webhooks {
  constructor(private readonly _http: HttpTransport) {}

  /** Register a webhook endpoint. Returns endpoint with secret (shown once). */
  async create(opts: {
    url: string;
    events: string[];
    description?: string;
  }): Promise<WebhookEndpoint> {
    const body: Record<string, unknown> = {
      url: opts.url,
      events: opts.events,
    };
    if (opts.description !== undefined) body.description = opts.description;
    const result = await this._http.request("POST", "/v1/webhooks", {
      json: body,
    });
    return result as unknown as WebhookEndpoint;
  }

  async list(): Promise<WebhookEndpoint[]> {
    const result = await this._http.request("GET", "/v1/webhooks");
    return result as unknown as WebhookEndpoint[];
  }

  async get(webhookId: string): Promise<WebhookEndpoint> {
    const result = await this._http.request(
      "GET",
      `/v1/webhooks/${webhookId}`,
    );
    return result as unknown as WebhookEndpoint;
  }

  async update(
    webhookId: string,
    opts: {
      url?: string;
      events?: string[];
      description?: string;
      active?: boolean;
    },
  ): Promise<WebhookEndpoint> {
    const body: Record<string, unknown> = {};
    if (opts.url !== undefined) body.url = opts.url;
    if (opts.events !== undefined) body.events = opts.events;
    if (opts.description !== undefined) body.description = opts.description;
    if (opts.active !== undefined) body.active = opts.active;
    const result = await this._http.request(
      "PUT",
      `/v1/webhooks/${webhookId}`,
      { json: body },
    );
    return result as unknown as WebhookEndpoint;
  }

  async delete(webhookId: string): Promise<void> {
    await this._http.request("DELETE", `/v1/webhooks/${webhookId}`);
  }

  async test(
    webhookId: string,
  ): Promise<{ success: boolean; status?: number; error?: string }> {
    const result = await this._http.request(
      "POST",
      `/v1/webhooks/${webhookId}/test`,
    );
    return result as unknown as { success: boolean; status?: number; error?: string };
  }

  async deliveries(webhookId: string): Promise<WebhookDelivery[]> {
    const result = await this._http.request(
      "GET",
      `/v1/webhooks/${webhookId}/deliveries`,
    );
    return result as unknown as WebhookDelivery[];
  }

  /** Verify an incoming webhook signature (use on your server). */
  static verifySignature(
    payload: Buffer | string,
    signature: string,
    secret: string,
    timestamp: number,
  ): boolean {
    if (!signature.startsWith("sha256=")) return false;

    // Reject timestamps older than 5 minutes to prevent replay attacks
    const now = Math.floor(Date.now() / 1000);
    if (Math.abs(now - timestamp) > 300) return false;

    const expectedSig = signature.slice(7);
    const body =
      typeof payload === "string" ? payload : payload.toString("utf-8");
    const message = `${timestamp}.${body}`;
    const computed = createHmac("sha256", secret)
      .update(message)
      .digest("hex");
    try {
      return timingSafeEqual(
        Buffer.from(computed, "hex"),
        Buffer.from(expectedSig, "hex"),
      );
    } catch {
      return false;
    }
  }
}

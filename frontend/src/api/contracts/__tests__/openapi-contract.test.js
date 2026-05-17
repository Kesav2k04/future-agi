import { describe, expect, it } from "vitest";

import {
  findOpenApiEndpoint,
  validateContractedRequestConfig,
  validateContractedResponse,
} from "../openapi-contract";

describe("OpenAPI runtime contract", () => {
  it("finds endpoints across the full Management API surface", () => {
    expect(findOpenApiEndpoint("/usage/ee/licenses/", "get")).toMatchObject({
      template: "/usage/ee/licenses/",
      method: "get",
    });
    expect(
      findOpenApiEndpoint("/falcon-ai/conversations/", "post"),
    ).toMatchObject({
      template: "/falcon-ai/conversations/",
      method: "post",
    });
    expect(
      findOpenApiEndpoint("/model-hub/annotation-queues/queue-1/items/", "get"),
    ).toMatchObject({
      template: "/model-hub/annotation-queues/{queue_id}/items/",
      method: "get",
    });
  });

  it("validates request bodies from backend serializer schemas", () => {
    expect(
      validateContractedRequestConfig({
        url: "/usage/ee/licenses/",
        method: "post",
        data: { band: "team", billing_interval: "monthly" },
      }),
    ).toMatchObject({ ok: true });

    const result = validateContractedRequestConfig({
      url: "/usage/ee/licenses/",
      method: "post",
      data: { band: "legacy-plan" },
    });

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "request body contract validation failed",
    );
  });

  it("validates form bodies against the same request schema", () => {
    const body = new FormData();
    body.set("band", "business");
    body.set("billing_interval", "yearly");

    expect(
      validateContractedRequestConfig({
        url: "/usage/ee/licenses/",
        method: "post",
        data: body,
      }),
    ).toMatchObject({ ok: true });
  });

  it("does not unwrap response envelopes to hide schema drift", () => {
    const response = {
      status: 200,
      config: { url: "/usage/ee/licenses/", method: "get" },
      data: {
        result: {
          licenses: [],
        },
      },
    };

    const result = validateContractedResponse(response);

    expect(result.ok).toBe(false);
    expect(result.error.message).toContain(
      "response contract validation failed",
    );
    expect(result.error.message).toContain("status");
  });

  it("does not validate undocumented error responses against a success schema", () => {
    const response = {
      status: 404,
      config: {
        url: "/usage/ee/licenses/2db3e0e8-5cec-4bb3-a358-ff1ea0671599/revoke/",
        method: "post",
      },
      data: {
        status: false,
        result: "License not found",
      },
    };

    expect(validateContractedResponse(response)).toMatchObject({
      ok: true,
      skipped: true,
    });
  });
});

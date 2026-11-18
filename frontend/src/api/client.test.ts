import { describe, expect, it } from "vitest";

import { ApiError, call, toApiError } from "./client";

describe("toApiError", () => {
  it("maps a problem+json body", () => {
    const err = toApiError(422, {
      code: "validation_failed",
      detail: "bad",
      errors: [{ field: "name", message: "required" }],
      request_id: "r1",
    });
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(422);
    expect(err.code).toBe("validation_failed");
    expect(err.fieldErrors).toEqual([{ field: "name", message: "required" }]);
    expect(err.requestId).toBe("r1");
  });
  it("tolerates a non-problem body", () => {
    const err = toApiError(500, "<html>");
    expect(err.code).toBe("unknown");
    expect(err.message).toMatch(/try again/i);
  });
});

describe("call", () => {
  it("returns data on success", async () => {
    const data = await call(Promise.resolve({ data: { ok: 1 }, response: new Response(null, { status: 200 }) }));
    expect(data).toEqual({ ok: 1 });
  });
  it("throws ApiError and announces 401", async () => {
    let fired = false;
    window.addEventListener("revops:unauthorized", () => (fired = true), { once: true });
    await expect(
      call(Promise.resolve({ error: { code: "unauthenticated", detail: "no" }, response: new Response(null, { status: 401 }) })),
    ).rejects.toMatchObject({ status: 401, code: "unauthenticated" });
    expect(fired).toBe(true);
  });
});

import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

/** RFC 9457 problem+json from the API (02 section 1). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fieldErrors: { field: string; message: string }[];
  readonly requestId: string | null;

  constructor(status: number, code: string, detail: string, fieldErrors: { field: string; message: string }[], requestId: string | null) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
    this.requestId = requestId;
  }
}

interface ProblemBody {
  status?: number;
  code?: string;
  detail?: string;
  errors?: { field?: string; message?: string }[];
  request_id?: string;
}

export function toApiError(status: number, body: unknown): ApiError {
  const p = (typeof body === "object" && body !== null ? body : {}) as ProblemBody;
  return new ApiError(
    status,
    p.code ?? "unknown",
    p.detail ?? "Something went wrong. Please try again.",
    (p.errors ?? []).map((e) => ({ field: e.field ?? "", message: e.message ?? "" })),
    p.request_id ?? null,
  );
}

/** Same-origin client: the session lives in an HttpOnly cookie, so no token ever touches JS (03 section 6.2). */
export const api = createClient<paths>({
  // Same origin as the page (the dev server proxies /api; production serves both from FastAPI).
  baseUrl: window.location.origin,
  credentials: "same-origin",
  // Resolve fetch at call time so tests can substitute it.
  fetch: (request) => globalThis.fetch(request),
});

/** Unwraps an openapi-fetch result: returns data or throws an ApiError built from the problem body. */
export async function call<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request;
  if (!response.ok || data === undefined) {
    if (response.status === 401) window.dispatchEvent(new Event("revops:unauthorized"));
    throw toApiError(response.status, error);
  }
  return data;
}

/** Calls that return 204/empty bodies. */
export async function callVoid(request: Promise<{ error?: unknown; response: Response }>): Promise<void> {
  const { error, response } = await request;
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("revops:unauthorized"));
    throw toApiError(response.status, error);
  }
}

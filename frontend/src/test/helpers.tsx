import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

import { AppRoutes, makeQueryClient } from "../App";
import { AuthProvider } from "../auth/AuthContext";

type Handler = (req: Request) => { status?: number; body?: unknown } | Promise<{ status?: number; body?: unknown }>;

/** Routes "METHOD /path" to handlers; any unmatched call fails the test loudly. */
export function mockApi(routes: Record<string, Handler>) {
  const calls: string[] = [];
  const spy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const req = input instanceof Request ? input : new Request(new URL(String(input), "http://localhost"), init);
    const key = `${req.method} ${new URL(req.url).pathname}`;
    calls.push(key);
    const handler = routes[key];
    if (!handler) throw new Error(`Unmocked API call: ${key}`);
    const { status = 200, body } = await handler(req);
    return new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
    });
  });
  return { calls, spy };
}

export function renderApp(path: string) {
  return render(
    <QueryClientProvider client={makeQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export const problem = (status: number, code: string, detail = "nope") => ({ status, body: { status, code, detail, errors: [] } });

export const meOf = (role: "admin" | "finance" | "sales") => ({
  email: `${role}@example.com`, full_name: `${role} user`, org_id: "o1", org_name: "Acme Traders", role, user_id: "u1",
});

export const emptyPage = { items: [], total: 0, limit: 20, offset: 0 };

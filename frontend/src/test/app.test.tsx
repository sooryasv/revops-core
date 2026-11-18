import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { emptyPage, meOf, mockApi, problem, renderApp } from "./helpers";

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

const summary = {
  as_of_date: "2026-11-03", outstanding_paise: 12540050, overdue_paise: 500000, overdue_invoices: 2, open_invoices: 5,
  billed_last_30d_paise: 9000000, collected_last_30d_paise: 4500000, dso_30d: 15.5,
  invoices: { draft: 1, issued: 5, void: 0 }, top_overdue_customers: [], recent_payments: [], webhook_problems_7d: 0,
};
const aging = { as_of_date: "2026-11-03", total_due_paise: 0, buckets: [] };
const pipeline = { by_stage: {}, open_value_paise: 0, won_last_90d: { count: 0, value_paise: 0 }, lost_last_90d_count: 0, win_rate_bp_90d: null, stale_open_deals: [] };

describe("auth and routing", () => {
  it("sends an anonymous visitor to the login page", async () => {
    mockApi({ "GET /api/v1/auth/me": () => problem(401, "unauthenticated") });
    renderApp("/invoices");
    expect(await screen.findByRole("heading", { name: /sign in/i })).toBeInTheDocument();
  });

  it("validates the login form before calling the API", async () => {
    const { calls } = mockApi({ "GET /api/v1/auth/me": () => problem(401, "unauthenticated") });
    renderApp("/login");
    await userEvent.click(await screen.findByRole("button", { name: /sign in/i }));
    expect(await screen.findByText("Enter your email")).toBeInTheDocument();
    expect(calls).not.toContain("POST /api/v1/auth/login");
  });

  it("shows a friendly message for wrong credentials", async () => {
    mockApi({
      "GET /api/v1/auth/me": () => problem(401, "unauthenticated"),
      "POST /api/v1/auth/login": () => problem(401, "invalid_credentials"),
    });
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "a@b.co");
    await userEvent.type(screen.getByLabelText("Password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });

  it("signs in without putting anything in browser storage", async () => {
    let signedIn = false;
    mockApi({
      "GET /api/v1/auth/me": () => (signedIn ? { body: meOf("admin") } : problem(401, "unauthenticated")),
      "POST /api/v1/auth/login": () => { signedIn = true; return { body: meOf("admin") }; },
      "GET /api/v1/reports/summary": () => ({ body: summary }),
      "GET /api/v1/reports/aging": () => ({ body: aging }),
      "GET /api/v1/reports/pipeline": () => ({ body: pipeline }),
    });
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Email"), "admin@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct horse battery");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
  });
});

describe("role-based UI (convenience only; the API enforces)", () => {
  function dashboardRoutes(role: "admin" | "finance" | "sales") {
    return {
      "GET /api/v1/auth/me": () => ({ body: meOf(role) }),
      "GET /api/v1/reports/summary": () => ({ body: summary }),
      "GET /api/v1/reports/aging": () => ({ body: aging }),
      "GET /api/v1/reports/pipeline": () => ({ body: pipeline }),
    };
  }

  it("admin sees Users and Audit log", async () => {
    mockApi(dashboardRoutes("admin"));
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Users" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Audit log" })).toBeInTheDocument();
  });

  it("sales sees neither, and gets pipeline only on the dashboard", async () => {
    const { calls } = mockApi(dashboardRoutes("sales"));
    renderApp("/");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).queryByRole("link", { name: "Users" })).toBeNull();
    expect(within(nav).queryByRole("link", { name: "Audit log" })).toBeNull();
    expect(await screen.findByRole("heading", { name: "Sales pipeline" })).toBeInTheDocument();
    expect(calls).not.toContain("GET /api/v1/reports/summary");
  });

  it("a direct visit to an admin page shows no access for finance", async () => {
    mockApi(dashboardRoutes("finance"));
    renderApp("/users");
    expect(await screen.findByText("You do not have access to this page.")).toBeInTheDocument();
  });

  it("formats dashboard money in en-IN", async () => {
    mockApi(dashboardRoutes("finance"));
    renderApp("/");
    expect(await screen.findByText("₹1,25,400.50")).toBeInTheDocument();
  });
});

describe("list states", () => {
  it("shows the empty state, then an error with retry", async () => {
    mockApi({ "GET /api/v1/auth/me": () => ({ body: meOf("finance") }), "GET /api/v1/customers": () => ({ body: emptyPage }) });
    const first = renderApp("/customers");
    expect(await screen.findByText("No customers found")).toBeInTheDocument();
    first.unmount();
    vi.restoreAllMocks();
    mockApi({ "GET /api/v1/auth/me": () => ({ body: meOf("finance") }), "GET /api/v1/customers": () => problem(500, "internal_error", "boom") });
    renderApp("/customers");
    await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument(), { timeout: 4000 });
  });

  it("keeps filters in the URL and sends them to the API", async () => {
    const seen: string[] = [];
    mockApi({
      "GET /api/v1/auth/me": () => ({ body: meOf("finance") }),
      "GET /api/v1/invoices": (req) => { seen.push(new URL(req.url).search); return { body: emptyPage }; },
    });
    renderApp("/invoices?status=issued&overdue=true");
    await screen.findByText("No invoices match these filters");
    expect(seen[0]).toContain("status=issued");
    expect(seen[0]).toContain("overdue=true");
  });
});

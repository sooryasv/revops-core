import { expect, test } from "@playwright/test";

import { authFile, issueInvoiceWithPayLink } from "./helpers";

test.use({ storageState: authFile("admin") }); // organisation A

// Flow 3: tenant isolation as a person would meet it in the UI.
test("organisation B cannot see or open organisation A's records", async ({ browser, request }) => {
  const name = `E2E Isolation ${Date.now().toString(36)}`;
  const seeded = await issueInvoiceWithPayLink(request, name);

  const ctx = await browser.newContext({ storageState: authFile("adminB") });
  const b = await ctx.newPage();
  await b.goto("/");
  await expect(b.getByText("Tiruppur Knit Exports (demo)")).toBeVisible(); // B's own organisation in the header

  // Search by A's exact customer name: nothing.
  await b.goto(`/customers?q=${encodeURIComponent(name)}`);
  await expect(b.getByText("No customers found")).toBeVisible();

  // Direct links to A's customer and invoice behave like records that do not exist (404, not 403).
  await b.goto(`/customers/${seeded.customerId}`);
  await expect(b.getByText("We could not find that record.")).toBeVisible();
  await b.goto(`/invoices/${seeded.invoiceId}`);
  await expect(b.getByText("We could not find that record.")).toBeVisible();

  // The same at the API level, using B's browser session.
  const api = await b.request.get(`/api/v1/invoices/${seeded.invoiceId}`);
  expect(api.status()).toBe(404);

  // The seeded canary customer belongs to B: B sees it, A never does.
  await b.goto("/customers?q=Zephyr");
  await expect(b.getByRole("link", { name: "Zephyr Canary Traders (demo)" })).toBeVisible();
  const fromA = await request.get("/api/v1/customers?q=Zephyr");
  expect((await fromA.json()).total).toBe(0);
  await ctx.close();
});

import { expect, test } from "@playwright/test";

import { authFile } from "./helpers";

// Flow 1 (03 section 12): login, customer, deal won, draft invoice, issue, pay link. All through the real UI.
test.describe("as an administrator", () => {
test.use({ storageState: authFile("admin") });
test("sales-to-invoice: customer, deal won, draft, issue, pay link", async ({ page }) => {
  const stamp = Date.now().toString(36);
  const customerName = `E2E Flow1 ${stamp}`;
  await page.goto("/");

  // Customer
  await page.getByRole("link", { name: "Customers" }).click();
  await page.getByRole("button", { name: "New customer" }).click();
  const customerDialog = page.getByRole("dialog", { name: "New customer" });
  await customerDialog.getByLabel("Name").fill(customerName);
  await customerDialog.getByLabel("State").selectOption("33");
  await customerDialog.getByLabel("Billing address").fill("12 Test Road, Chennai");
  await customerDialog.getByRole("button", { name: "Create customer" }).click();
  await expect(page.getByRole("link", { name: customerName })).toBeVisible();

  // Deal -> won
  await page.getByRole("link", { name: "Deals" }).click();
  await page.getByRole("button", { name: "New deal" }).click();
  const dealDialog = page.getByRole("dialog", { name: "New deal" });
  await dealDialog.getByLabel("Title").fill(`Deal ${stamp}`);
  await dealDialog.getByLabel("Customer").selectOption({ label: customerName });
  await dealDialog.getByLabel("Estimated value (₹)").fill("25000");
  await dealDialog.getByRole("button", { name: "Create deal" }).click();
  const row = page.getByRole("row", { name: new RegExp(`Deal ${stamp}`) });
  await expect(row).toBeVisible();
  await row.getByLabel(/Move .* to stage/).selectOption("qualified");
  await expect(row.locator(".chip", { hasText: "Qualified" })).toBeVisible();
  await row.getByLabel(/Move .* to stage/).selectOption("proposal");
  await expect(row.locator(".chip", { hasText: "Proposal" })).toBeVisible();
  await row.getByLabel(/Move .* to stage/).selectOption("won");
  await expect(row.locator(".chip", { hasText: "Won" })).toBeVisible();

  // Won deal -> draft invoice (opens the editor), adjust, save
  await row.getByRole("button", { name: "Create invoice" }).click();
  await expect(page.getByRole("heading", { name: "Edit draft invoice" })).toBeVisible();
  await page.getByLabel("Quantity").fill("2");
  // Server-computed GST preview: 2 x 25,000 = 50,000 + 18% intra-state (CGST 4,500 + SGST 4,500) = 59,000
  const totals = page.getByRole("region", { name: /Totals/ });
  await expect(totals.getByText("₹59,000.00")).toBeVisible();
  await expect(totals.getByText("CGST")).toBeVisible();
  await page.getByRole("button", { name: "Save draft" }).click();

  // Issue
  await expect(page.getByRole("heading", { name: "Draft invoice" })).toBeVisible();
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await expect(page.getByRole("heading", { name: /^INV\/\d{2}-\d{2}\/\d{6}$/ })).toBeVisible();
  await expect(page.getByText("Issued", { exact: true }).first()).toBeVisible();

  // Pay link is shown once, in a modal
  await page.getByRole("button", { name: "Payment link" }).click();
  await page.getByRole("button", { name: "Create payment link" }).click();
  const link = page.getByRole("textbox", { name: "Payment link" });
  await expect(link).toHaveValue(/\/pay\/[\w-]{20,}/);
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.getByRole("button", { name: "Payment link" }).click();
  await expect(page.getByRole("textbox", { name: "Payment link" })).toHaveCount(0); // not retrievable again
});
});

test("role differences: sales cannot issue, finance cannot manage users", async ({ browser }) => {
  const salesCtx = await browser.newContext({ storageState: authFile("sales") });
  const sales = await salesCtx.newPage();
  await sales.goto("/");
  const nav = sales.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: "Deals" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Users" })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Audit log" })).toHaveCount(0);
  await salesCtx.close();

  const financeCtx = await browser.newContext({ storageState: authFile("finance") });
  const finance = await financeCtx.newPage();
  await finance.goto("/");
  await expect(finance.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Users" })).toHaveCount(0);
  await finance.goto("/users");
  await expect(finance.getByText("You do not have access to this page.")).toBeVisible();
  await financeCtx.close();
});

test("a wrong password shows a friendly error and does not sign in", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("definitely-wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Email or password is incorrect.");
  await expect(page).toHaveURL(/\/login$/);
});

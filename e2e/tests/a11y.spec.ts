import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import { authFile, issueInvoiceWithPayLink, STUB_CHECKOUT_JS } from "./helpers";

// Axe scans (03 section 12): login, dashboard, invoice detail, pay page. WCAG 2.x A and AA rules must have no violations.
async function expectNoViolations(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const summary = results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`);
  expect(summary, summary.join("\n")).toEqual([]);
}

test.describe("anonymous", () => {
  test("login page has no axe violations", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByRole("heading", { name: /sign in/i })).toBeVisible();
    await expectNoViolations(page);
  });

  test("public pay page has no axe violations", async ({ browser }) => {
    const owner = await browser.newContext({ storageState: authFile("admin") });
    const seeded = await issueInvoiceWithPayLink(owner.request, `E2E A11y Pay ${Date.now().toString(36)}`);
    await owner.close();
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.route("https://checkout.razorpay.com/**", (route) => route.fulfill({ status: 200, contentType: "application/javascript", body: STUB_CHECKOUT_JS }));
    await page.goto(`/pay/${seeded.token}`);
    await expect(page.getByRole("heading", { name: `Invoice ${seeded.number}` })).toBeVisible();
    await expectNoViolations(page);
    await ctx.close();
  });
});

test.describe("signed in", () => {
  test.use({ storageState: authFile("admin") });

  test("dashboard has no axe violations", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Receivables aging" })).toBeVisible();
    await expect(page.getByText("Outstanding", { exact: true })).toBeVisible();
    await expectNoViolations(page);
  });

  test("invoice detail has no axe violations", async ({ page, request }) => {
    const seeded = await issueInvoiceWithPayLink(request, `E2E A11y ${Date.now().toString(36)}`);
    await page.goto(`/invoices/${seeded.invoiceId}`);
    await expect(page.getByRole("heading", { name: seeded.number })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Payments" })).toBeVisible();
    await expectNoViolations(page);
  });
});

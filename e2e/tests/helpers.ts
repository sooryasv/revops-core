import { createHmac } from "node:crypto";
import { fileURLToPath } from "node:url";

import { expect, type APIRequestContext, type Page } from "@playwright/test";

// The stack runs with the fake provider; these are its documented dev-only secrets (backend/app/config.py).
export const FAKE_KEY_SECRET = process.env.E2E_FAKE_KEY_SECRET ?? "fake-key-secret-dev-only";
export const FAKE_WEBHOOK_SECRET = process.env.E2E_FAKE_WEBHOOK_SECRET ?? "fake-webhook-secret-dev-only";
export const DEMO_PASSWORD = process.env.SEED_DEMO_PASSWORD ?? "e2e-demo-password-1";

export type Role = "admin" | "finance" | "sales";
export const emailFor = (role: Role, org: "a" | "b" = "a") => `${role}.${org}@example.com`;

/** Saved browser sessions, one per seeded identity, created once by tests/auth.setup.ts. Logging in per test would trip the
 *  login rate limiter (which is itself a feature: see backend/tests/security). */
export const IDENTITIES = [
  { key: "admin", role: "admin", org: "a" },
  { key: "finance", role: "finance", org: "a" },
  { key: "sales", role: "sales", org: "a" },
  { key: "adminB", role: "admin", org: "b" },
] as const satisfies readonly { key: string; role: Role; org: "a" | "b" }[];
export type IdentityKey = (typeof IDENTITIES)[number]["key"];
export const authFile = (key: IdentityKey): string => fileURLToPath(new URL(`../.auth/${key}.json`, import.meta.url));

/** Log in through the real form. */
export async function loginViaUi(page: Page, role: Role, org: "a" | "b" = "a"): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(emailFor(role, org));
  await page.getByLabel("Password").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

export interface Seeded {
  customerId: string;
  invoiceId: string;
  number: string;
  token: string;
  totalPaise: number;
}

/** Arrange via the API: customer, draft invoice, issue, pay link. */
export async function issueInvoiceWithPayLink(request: APIRequestContext, label: string): Promise<Seeded> {
  const customer = await request.post("/api/v1/customers", {
    data: { name: label, state_code: "33", billing_address: "1 Test Street, Chennai" },
  });
  expect(customer.status(), await customer.text()).toBe(201);
  const customerId = (await customer.json()).id as string;
  const draft = await request.post("/api/v1/invoices", {
    data: {
      customer_id: customerId,
      items: [{ description: "E2E consulting", quantity: 2, unit_price_paise: 100000, gst_rate_bp: 1800 }],
    },
  });
  expect(draft.status(), await draft.text()).toBe(201);
  const invoiceId = (await draft.json()).id as string;
  const issued = await request.post(`/api/v1/invoices/${invoiceId}/issue`);
  expect(issued.status(), await issued.text()).toBe(200);
  const detail = await issued.json();
  const link = await request.post(`/api/v1/invoices/${invoiceId}/pay-link`);
  expect(link.status(), await link.text()).toBe(201);
  const url = (await link.json()).url as string;
  return { customerId, invoiceId, number: detail.number, token: url.split("/pay/")[1] ?? "", totalPaise: detail.total_paise };
}

/** Build and POST a signed Razorpay-style webhook, exactly as the provider would (HMAC-SHA256 of the raw body). */
export async function sendSignedWebhook(
  request: APIRequestContext,
  args: { event: "payment.captured" | "payment.failed" | "payment.authorized"; orderId: string; paymentId: string; amountPaise: number; eventId: string; secret?: string },
) {
  const payment = {
    id: args.paymentId, order_id: args.orderId, amount: args.amountPaise, currency: "INR", method: "upi",
    status: args.event === "payment.captured" ? "captured" : args.event === "payment.failed" ? "failed" : "authorized",
  };
  const body = JSON.stringify({
    entity: "event", account_id: "acc_e2e", event: args.event, contains: ["payment"],
    payload: { payment: { entity: payment } }, created_at: Math.floor(Date.now() / 1000),
  });
  const signature = createHmac("sha256", args.secret ?? FAKE_WEBHOOK_SECRET).update(body).digest("hex");
  return request.post("/api/v1/webhooks/razorpay", {
    data: body,
    headers: { "content-type": "application/json", "x-razorpay-signature": signature, "x-razorpay-event-id": args.eventId },
  });
}

/** A stand-in for Razorpay's checkout.js. It signs `order_id|payment_id` with the fake key secret in the browser, the
 *  same way the real Checkout hands back razorpay_signature, then calls the page's handler. */
export const STUB_CHECKOUT_JS = `
(function () {
  async function hmacHex(secret, message) {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    const sig = await crypto.subtle.sign("HMAC", key, enc.encode(message));
    return Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  window.Razorpay = function (options) {
    this.on = function () {};
    this.open = async function () {
      const paymentId = "pay_e2e_" + Math.random().toString(36).slice(2, 12);
      const signature = await hmacHex(${JSON.stringify(FAKE_KEY_SECRET)}, options.order_id + "|" + paymentId);
      window.__e2ePaymentId = paymentId;
      options.handler({ razorpay_payment_id: paymentId, razorpay_order_id: options.order_id, razorpay_signature: signature });
    };
  };
})();
`;

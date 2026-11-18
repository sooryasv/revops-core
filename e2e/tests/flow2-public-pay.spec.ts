import { expect, test } from "@playwright/test";

import { authFile, issueInvoiceWithPayLink, sendSignedWebhook, STUB_CHECKOUT_JS } from "./helpers";

test.use({ storageState: authFile("admin") });

// Flow 2: public pay with a simulated signed webhook. The browser never decides "paid": only the webhook does.
test("customer pays; invoice turns paid only after the signed webhook; redelivery changes nothing", async ({ page, browser, request }) => {
  const seeded = await issueInvoiceWithPayLink(request, `E2E Flow2 ${Date.now().toString(36)}`);

  const customerContext = await browser.newContext(); // an anonymous visitor: no cookies from the admin
  const pay = await customerContext.newPage();
  await pay.route("https://checkout.razorpay.com/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/javascript", body: STUB_CHECKOUT_JS }),
  );
  await pay.goto(`/pay/${seeded.token}`);
  await expect(pay.getByRole("heading", { name: `Invoice ${seeded.number}` })).toBeVisible();
  await expect(pay.getByRole("navigation")).toHaveCount(0); // no app chrome on the public page
  await expect(pay.getByRole("button", { name: /Pay ₹2,360\.00/ })).toBeVisible();

  const orderResponse = pay.waitForResponse((r) => r.url().includes(`/public/pay/${seeded.token}/order`));
  const confirmResponse = pay.waitForResponse((r) => r.url().includes(`/public/pay/${seeded.token}/confirm`));
  await pay.getByRole("button", { name: /Pay ₹2,360\.00/ }).click();
  const order = (await (await orderResponse).json()) as { order_id: string; amount_paise: number };
  expect((await confirmResponse).status()).toBe(200);
  const paymentId = await pay.evaluate(() => (window as unknown as { __e2ePaymentId: string }).__e2ePaymentId);

  // The signature was valid, but the invoice must NOT be paid yet.
  await expect(pay.getByText(/confirming it with your bank/)).toBeVisible();
  await expect(pay.getByText(/This invoice is paid/)).toHaveCount(0);

  // The provider's signed webhook arrives.
  const webhook = await sendSignedWebhook(request, {
    event: "payment.captured", orderId: order.order_id, paymentId, amountPaise: order.amount_paise, eventId: `evt_e2e_${paymentId}`,
  });
  expect(webhook.status(), await webhook.text()).toBe(200);
  await expect(pay.getByText(/This invoice is paid/)).toBeVisible({ timeout: 15_000 }); // the page polls every 2 s

  // Redelivery of the same event id is a no-op.
  const again = await sendSignedWebhook(request, {
    event: "payment.captured", orderId: order.order_id, paymentId, amountPaise: order.amount_paise, eventId: `evt_e2e_${paymentId}`,
  });
  expect(again.status()).toBe(200);
  const payments = await (await request.get(`/api/v1/invoices/${seeded.invoiceId}/payments`)).json();
  expect(payments.filter((p: { status: string }) => p.status === "captured")).toHaveLength(1);
  const invoice = await (await request.get(`/api/v1/invoices/${seeded.invoiceId}`)).json();
  expect(invoice.amount_paid_paise).toBe(seeded.totalPaise);
  expect(invoice.payment_status).toBe("paid");

  // The owner sees it in the UI too.
  await page.goto(`/invoices/${seeded.invoiceId}`);
  await expect(page.getByText("Paid", { exact: true }).first()).toBeVisible();
  await customerContext.close();
});

test("a forged webhook (bad signature) is rejected and changes nothing", async ({ request }) => {
  const seeded = await issueInvoiceWithPayLink(request, `E2E Forged ${Date.now().toString(36)}`);
  const order = await (await request.post(`/api/v1/public/pay/${seeded.token}/order`)).json();
  const forged = await sendSignedWebhook(request, {
    event: "payment.captured", orderId: order.order_id, paymentId: "pay_forged_1", amountPaise: order.amount_paise,
    eventId: "evt_forged_1", secret: "not-the-webhook-secret",
  });
  expect(forged.status()).toBeGreaterThanOrEqual(400);
  expect(forged.status()).toBeLessThan(500);
  const invoice = await (await request.get(`/api/v1/invoices/${seeded.invoiceId}`)).json();
  expect(invoice.payment_status).toBe("unpaid");
});

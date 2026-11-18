import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CHECKOUT_SRC } from "../lib/razorpay";
import { mockApi, problem, renderApp } from "./helpers";

afterEach(() => {
  vi.restoreAllMocks();
  document.head.querySelectorAll("script").forEach((s) => s.remove());
});

const invoice = {
  supplier_name: "Acme Traders", invoice_number: "INV/26-27/000001", issue_date: "2026-10-01", due_date: "2026-10-31",
  currency: "INR", items: [{ description: "Widgets", quantity: 2, unit_price_paise: 100000, gst_rate_bp: 1800, line_total_paise: 236000 }],
  subtotal_paise: 200000, cgst_paise: 18000, sgst_paise: 18000, igst_paise: 0, total_paise: 236000,
  amount_paid_paise: 0, amount_due_paise: 236000, payment_status: "unpaid", can_pay: true,
};
const razorpayScripts = () => [...document.querySelectorAll("script")].filter((s) => s.src.startsWith("https://checkout.razorpay.com"));

describe("public pay page", () => {
  it("shows a generic message for an invalid or expired link", async () => {
    mockApi({ "GET /api/v1/public/pay/bad": () => problem(404, "not_found") });
    renderApp("/pay/bad");
    expect(await screen.findByRole("heading", { name: "This payment link is not valid" })).toBeInTheDocument();
  });

  it("renders the invoice without app chrome and does not load Razorpay until Pay is pressed", async () => {
    mockApi({ "GET /api/v1/public/pay/tok": () => ({ body: invoice }) });
    renderApp("/pay/tok");
    expect(await screen.findByRole("heading", { name: "Invoice INV/26-27/000001" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation")).toBeNull();
    expect(screen.getByRole("button", { name: /pay/i })).toHaveTextContent("₹2,360.00");
    expect(razorpayScripts()).toHaveLength(0);
  });

  it("loads the Razorpay script only after the order is created", async () => {
    mockApi({
      "GET /api/v1/public/pay/tok": () => ({ body: invoice }),
      "POST /api/v1/public/pay/tok/order": () => ({ body: { order_id: "order_1", amount_paise: 236000, currency: "INR", key_id: "rzp_test_x" } }),
    });
    renderApp("/pay/tok");
    await userEvent.click(await screen.findByRole("button", { name: /pay/i }));
    await vi.waitFor(() => expect(razorpayScripts()).toHaveLength(1));
    expect(razorpayScripts()[0]?.src).toBe(CHECKOUT_SRC);
  });

  it("shows paid state with no pay button", async () => {
    mockApi({ "GET /api/v1/public/pay/tok": () => ({ body: { ...invoice, payment_status: "paid", can_pay: false, amount_due_paise: 0, amount_paid_paise: 236000 } }) });
    renderApp("/pay/tok");
    expect(await screen.findByText(/This invoice is paid/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /pay/i })).toBeNull();
  });

  it("explains when an invoice cannot be paid online", async () => {
    mockApi({ "GET /api/v1/public/pay/tok": () => ({ body: { ...invoice, can_pay: false } }) });
    renderApp("/pay/tok");
    expect(await screen.findByText(/cannot be paid online/)).toBeInTheDocument();
  });
});

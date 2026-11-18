import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useParams } from "react-router-dom";

import { api, ApiError, call } from "../api/client";
import { ErrorState, Loading, Money } from "../components/ui";
import { formatBps, formatDate } from "../lib/format";
import { loadCheckout, openCheckout } from "../lib/razorpay";

const POLL_MS = 2000;
const POLL_WINDOW_MS = 60_000;

type Phase = { kind: "idle" } | { kind: "working" } | { kind: "verifying" } | { kind: "success" } | { kind: "failed"; message: string };

/** Public pay page (no app chrome, no auth). The server is the only authority on whether a payment happened:
 *  after Checkout we ask it to verify the signature, then show "processing" until the webhook marks it paid. */
export function PayPage() {
  const { token = "" } = useParams();
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const pollUntil = useRef(0);
  const path = { params: { path: { token } } };

  const invoice = useQuery({
    queryKey: ["public-pay", token],
    retry: false,
    queryFn: () => call(api.GET("/api/v1/public/pay/{token}", path)),
    // After Checkout, poll every 2 s for up to 60 s until the webhook has marked the invoice paid (03 section 9).
    refetchInterval: (q) =>
      phase.kind === "success" && q.state.data?.payment_status !== "paid" && Date.now() < pollUntil.current ? POLL_MS : false,
  });

  const pay = useMutation({
    mutationFn: async () => {
      setPhase({ kind: "working" });
      const [order] = await Promise.all([call(api.POST("/api/v1/public/pay/{token}/order", path)), loadCheckout()]);
      const data = invoice.data;
      openCheckout(
        {
          key: order.key_id, amount: order.amount_paise, currency: order.currency, order_id: order.order_id,
          name: data?.supplier_name ?? "Payment", description: `Invoice ${data?.invoice_number ?? ""}`.trim(),
          theme: { color: "#1f4fd8" },
          modal: { ondismiss: () => setPhase({ kind: "idle" }) },
          handler: (r) => {
            setPhase({ kind: "verifying" });
            call(api.POST("/api/v1/public/pay/{token}/confirm", { ...path, body: { order_id: r.razorpay_order_id, payment_id: r.razorpay_payment_id, signature: r.razorpay_signature } }))
              .then(() => { pollUntil.current = Date.now() + POLL_WINDOW_MS; setPhase({ kind: "success" }); void queryClient.invalidateQueries({ queryKey: ["public-pay", token] }); })
              .catch(() => setPhase({ kind: "failed", message: "We could not verify your payment. If money left your account, it will be applied automatically; please do not pay twice." }));
          },
        },
        (message) => setPhase({ kind: "failed", message }),
      );
    },
    onError: (e) => setPhase({ kind: "failed", message: e instanceof ApiError ? e.message : "Could not start the payment. Please try again." }),
  });

  if (invoice.isPending) return <main className="pay-page"><Loading label="Loading your invoice…" /></main>;
  if (invoice.isError) {
    const gone = invoice.error instanceof ApiError && invoice.error.status === 404;
    return (
      <main className="pay-page">
        {gone ? (
          <div className="state error" role="alert">
            <h1>This payment link is not valid</h1>
            <p>It may have expired or been replaced. Please ask the sender for a new link.</p>
          </div>
        ) : <ErrorState error={invoice.error} onRetry={() => void invoice.refetch()} />}
      </main>
    );
  }

  const inv = invoice.data;
  const paid = inv.payment_status === "paid";
  const busy = phase.kind === "working" || phase.kind === "verifying";
  return (
    <main className="pay-page">
      <section className="card pay-card">
        <p className="muted">{inv.supplier_name}</p>
        <h1>Invoice {inv.invoice_number}</h1>
        <p className="muted">Issued {formatDate(inv.issue_date)} · Due {formatDate(inv.due_date)}</p>
        <table>
          <thead><tr><th>Item</th><th className="num">Qty</th><th className="num">Price</th><th className="num">GST</th><th className="num">Amount</th></tr></thead>
          <tbody>
            {inv.items.map((it, i) => (
              <tr key={i}><td>{it.description}</td><td className="num">{it.quantity}</td><td className="num"><Money paise={it.unit_price_paise} /></td><td className="num">{formatBps(it.gst_rate_bp)}</td><td className="num"><Money paise={it.line_total_paise} /></td></tr>
            ))}
          </tbody>
        </table>
        <dl className="details totals-list">
          <dt>Subtotal</dt><dd><Money paise={inv.subtotal_paise} /></dd>
          {inv.igst_paise > 0 ? <><dt>IGST</dt><dd><Money paise={inv.igst_paise} /></dd></> : <><dt>CGST</dt><dd><Money paise={inv.cgst_paise} /></dd><dt>SGST</dt><dd><Money paise={inv.sgst_paise} /></dd></>}
          <dt>Total</dt><dd><Money paise={inv.total_paise} /></dd>
          <dt>Already paid</dt><dd><Money paise={inv.amount_paid_paise} /></dd>
          <dt><strong>Amount due</strong></dt><dd><strong><Money paise={inv.amount_due_paise} /></strong></dd>
        </dl>

        <div aria-live="polite">
          {paid && <p className="notice success" role="status">This invoice is paid. Thank you!</p>}
          {!paid && phase.kind === "success" && <p className="notice" role="status">Payment received. We are confirming it with your bank, which usually takes a few seconds. If this page does not update, you can close it: we will mark the invoice paid as soon as the confirmation arrives.</p>}
          {phase.kind === "verifying" && <p className="notice" role="status">Verifying your payment…</p>}
          {phase.kind === "failed" && <p className="form-error" role="alert">{phase.message}</p>}
        </div>

        {!paid && inv.can_pay && phase.kind !== "success" && (
          <button type="button" className="btn btn-primary btn-lg" disabled={busy} onClick={() => pay.mutate()}>
            {busy ? "Please wait…" : <>Pay <Money paise={inv.amount_due_paise} /></>}
          </button>
        )}
        {!paid && !inv.can_pay && phase.kind !== "success" && <p className="notice" role="note">This invoice cannot be paid online right now.</p>}
      </section>
    </main>
  );
}

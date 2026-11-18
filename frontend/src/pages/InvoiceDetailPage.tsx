import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { api, call, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, Field, FormError, Modal, Money, PageHeader, QueryBoundary, StatusChip } from "../components/ui";
import { formatBps, formatDate, formatDateTime, parseRupeesToPaise, titleCase } from "../lib/format";

const manualSchema = z.object({
  amount: z.string().refine((v) => { const p = parseRupeesToPaise(v); return p !== null && p > 0; }, "Enter an amount above zero"),
  method: z.enum(["cash", "bank_transfer", "upi", "cheque", "other"]),
  paid_on: z.string().min(1, "Enter the payment date"),
  reference: z.string().max(100),
});
type ManualValues = z.infer<typeof manualSchema>;

function ManualPaymentModal({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { register, handleSubmit, formState: { errors } } = useForm<ManualValues>({
    resolver: zodResolver(manualSchema), defaultValues: { amount: "", method: "bank_transfer", paid_on: "", reference: "" },
  });
  const record = useMutation({
    mutationFn: (v: ManualValues) => call(api.POST("/api/v1/invoices/{invoice_id}/payments/manual", {
      params: { path: { invoice_id: invoiceId } },
      body: { amount_paise: parseRupeesToPaise(v.amount) ?? 0, method: v.method, paid_on: v.paid_on, reference: v.reference || null },
    })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["invoices"] }); void queryClient.invalidateQueries({ queryKey: ["reports"] }); onClose(); },
  });
  return (
    <Modal title="Record a payment" onClose={onClose}>
      <form className="form" noValidate onSubmit={handleSubmit((v) => record.mutate(v))}>
        <FormError error={record.error} />
        <Field label="Amount received (₹)" htmlFor="m-amt" error={errors.amount?.message}><input id="m-amt" inputMode="decimal" {...register("amount")} /></Field>
        <Field label="Method" htmlFor="m-method">
          <select id="m-method" {...register("method")}>
            {["cash", "bank_transfer", "upi", "cheque", "other"].map((m) => <option key={m} value={m}>{titleCase(m)}</option>)}
          </select>
        </Field>
        <Field label="Date received" htmlFor="m-date" error={errors.paid_on?.message}><input id="m-date" type="date" {...register("paid_on")} /></Field>
        <Field label="Reference (optional)" htmlFor="m-ref" error={errors.reference?.message}><input id="m-ref" {...register("reference")} /></Field>
        <button type="submit" className="btn btn-primary" disabled={record.isPending}>Record payment</button>
      </form>
    </Modal>
  );
}

function VoidModal({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState("");
  const voidIt = useMutation({
    mutationFn: () => call(api.POST("/api/v1/invoices/{invoice_id}/void", { params: { path: { invoice_id: invoiceId } }, body: { reason: reason.trim() } })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["invoices"] }); void queryClient.invalidateQueries({ queryKey: ["reports"] }); onClose(); },
  });
  return (
    <Modal title="Void this invoice" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); voidIt.mutate(); }}>
        <p>Voiding keeps the invoice number and cannot be undone. An invoice with captured payments cannot be voided.</p>
        <FormError error={voidIt.error} />
        <Field label="Reason" htmlFor="v-reason"><textarea id="v-reason" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} required /></Field>
        <button type="submit" className="btn btn-danger" disabled={voidIt.isPending || reason.trim() === ""}>Void invoice</button>
      </form>
    </Modal>
  );
}

function PayLinkModal({ invoiceId, onClose }: { invoiceId: string; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [copied, setCopied] = useState(false);
  const create = useMutation({
    mutationFn: () => call(api.POST("/api/v1/invoices/{invoice_id}/pay-link", { params: { path: { invoice_id: invoiceId } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["invoices", invoiceId] }),
  });
  const link: Schemas["PayLinkOut"] | undefined = create.data;
  return (
    <Modal title="Payment link" onClose={onClose}>
      {!link ? (
        <div className="form">
          <p>Creating a new link replaces any link you shared before. The link is shown once, so copy it right away.</p>
          <FormError error={create.error} />
          <button type="button" className="btn btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>Create payment link</button>
        </div>
      ) : (
        <div className="form">
          <p>Share this link with your customer. It will not be shown again.</p>
          <input readOnly aria-label="Payment link" value={link.url} onFocus={(e) => e.target.select()} />
          <p className="muted small">Expires {formatDateTime(link.expires_at)}</p>
          <button type="button" className="btn btn-primary" onClick={() => { void navigator.clipboard?.writeText(link.url).then(() => setCopied(true)); }}>
            {copied ? "Copied" : "Copy link"}
          </button>
        </div>
      )}
    </Modal>
  );
}

export function InvoiceDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [modal, setModal] = useState<"void" | "paylink" | "manual" | null>(null);
  const invoice = useQuery({ queryKey: ["invoices", id], queryFn: () => call(api.GET("/api/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id } } })) });
  const payments = useQuery({
    queryKey: ["invoices", id, "payments"], enabled: can("payments.read"),
    queryFn: () => call(api.GET("/api/v1/invoices/{invoice_id}/payments", { params: { path: { invoice_id: id } } })),
  });
  const audit = useQuery({
    queryKey: ["audit", "invoice", id], enabled: can("audit.read"),
    queryFn: () => call(api.GET("/api/v1/audit-logs", { params: { query: { entity_type: "invoice", entity_id: id, limit: 10 } } })),
  });
  const issue = useMutation({
    mutationFn: () => call(api.POST("/api/v1/invoices/{invoice_id}/issue", { params: { path: { invoice_id: id } } })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["invoices"] }); void queryClient.invalidateQueries({ queryKey: ["reports"] }); },
  });
  const inv = invoice.data;
  return (
    <QueryBoundary query={invoice}>
      {inv && (
        <>
          <PageHeader
            title={inv.number ?? "Draft invoice"}
            subtitle={inv.customer_name}
            actions={
              <>
                {inv.status === "draft" && can("invoices.draft") && <Link className="btn" to={`/invoices/${inv.id}/edit`}>Edit draft</Link>}
                {inv.status === "draft" && can("invoices.issue") && <button type="button" className="btn btn-primary" disabled={issue.isPending} onClick={() => issue.mutate()}>Issue invoice</button>}
                {inv.status === "issued" && can("invoices.paylink") && inv.payment_status !== "paid" && <button type="button" className="btn" onClick={() => setModal("paylink")}>Payment link</button>}
                {inv.status === "issued" && can("payments.manual") && inv.payment_status !== "paid" && <button type="button" className="btn" onClick={() => setModal("manual")}>Record payment</button>}
                {inv.status === "issued" && can("invoices.void") && <button type="button" className="btn btn-danger" onClick={() => setModal("void")}>Void</button>}
              </>
            }
          />
          <FormError error={issue.error} />
          <div className="chips">
            <StatusChip value={inv.status} />
            {inv.status === "issued" && <StatusChip value={inv.payment_status} />}
            {inv.is_overdue && <StatusChip value="overdue" label={`Overdue ${inv.days_overdue} days`} />}
            {inv.pay_link_active && <span className="chip chip-info">Payment link active</span>}
          </div>
          {inv.terms_exceed_statutory && (
            <p className="notice" role="note">
              These terms exceed the {inv.statutory_limit_days}-day limit that applies to MSME suppliers without a written agreement.
            </p>
          )}
          {inv.status === "void" && <p className="notice" role="note">Voided: {inv.void_reason}</p>}

          <section className="card">
            <dl className="details">
              <dt>Issue date</dt><dd>{formatDate(inv.issue_date)}</dd>
              <dt>Due date</dt><dd>{formatDate(inv.due_date)} ({inv.payment_terms_days}-day terms)</dd>
              <dt>Supply</dt><dd>{inv.supply_type === "intra" ? "Within the state (CGST + SGST)" : inv.supply_type === "inter" ? "Interstate (IGST)" : "—"}</dd>
            </dl>
            <table>
              <thead><tr><th>#</th><th>Description</th><th>HSN/SAC</th><th className="num">Qty</th><th className="num">Unit price</th><th className="num">GST</th><th className="num">Taxable</th><th className="num">Line total</th></tr></thead>
              <tbody>
                {inv.items.map((it) => (
                  <tr key={it.line_no}>
                    <td>{it.line_no}</td><td>{it.description}</td><td>{it.hsn_sac ?? "—"}</td>
                    <td className="num">{it.quantity}</td>
                    <td className="num"><Money paise={it.unit_price_paise} /></td>
                    <td className="num">{formatBps(it.gst_rate_bp)}</td>
                    <td className="num"><Money paise={it.taxable_paise} /></td>
                    <td className="num"><Money paise={it.line_total_paise} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <dl className="details totals-list">
              <dt>Subtotal</dt><dd><Money paise={inv.subtotal_paise} /></dd>
              {inv.supply_type === "inter" ? (
                <><dt>IGST</dt><dd><Money paise={inv.igst_paise} /></dd></>
              ) : (
                <><dt>CGST</dt><dd><Money paise={inv.cgst_paise} /></dd><dt>SGST</dt><dd><Money paise={inv.sgst_paise} /></dd></>
              )}
              <dt><strong>Total</strong></dt><dd><strong><Money paise={inv.total_paise} /></strong></dd>
              {inv.status === "issued" && (
                <>
                  <dt>Paid</dt><dd><Money paise={inv.amount_paid_paise} /></dd>
                  <dt><strong>Balance due</strong></dt><dd><strong><Money paise={inv.amount_due_paise} /></strong></dd>
                  {inv.overpaid_paise > 0 && <><dt>Overpaid</dt><dd><Money paise={inv.overpaid_paise} /></dd></>}
                </>
              )}
            </dl>
          </section>

          {can("payments.read") && (
            <section className="card" aria-labelledby="pay-h">
              <h2 id="pay-h">Payments</h2>
              <QueryBoundary query={payments}>
                {payments.data && (payments.data.length === 0 ? <EmptyState title="No payments yet" /> : (
                  <table>
                    <thead><tr><th>Date</th><th>Source</th><th>Method</th><th>Reference</th><th>Status</th><th className="num">Amount</th></tr></thead>
                    <tbody>
                      {payments.data.map((p) => (
                        <tr key={p.id}>
                          <td>{formatDate(p.paid_on ?? p.created_at)}</td><td>{titleCase(p.provider)}</td><td>{p.method ? titleCase(p.method) : "—"}</td>
                          <td>{p.reference ?? "—"}{p.failure_code && <span className="muted small"> ({p.failure_code})</span>}</td>
                          <td><StatusChip value={p.status} /></td><td className="num"><Money paise={p.amount_paise} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ))}
              </QueryBoundary>
            </section>
          )}

          {can("audit.read") && (
            <section className="card" aria-labelledby="aud-h">
              <h2 id="aud-h">Recent activity</h2>
              <QueryBoundary query={audit}>
                {audit.data && (audit.data.items.length === 0 ? <EmptyState title="No activity recorded" /> : (
                  <ul className="plain">
                    {audit.data.items.map((a) => <li key={a.id}><strong>{a.action}</strong> · {formatDateTime(a.created_at)} · {titleCase(a.actor_type)}</li>)}
                  </ul>
                ))}
              </QueryBoundary>
            </section>
          )}

          {modal === "void" && <VoidModal invoiceId={inv.id} onClose={() => setModal(null)} />}
          {modal === "paylink" && <PayLinkModal invoiceId={inv.id} onClose={() => setModal(null)} />}
          {modal === "manual" && <ManualPaymentModal invoiceId={inv.id} onClose={() => setModal(null)} />}
        </>
      )}
    </QueryBoundary>
  );
}

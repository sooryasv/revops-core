import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { api, call, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StateSelect } from "../components/StateSelect";
import { EmptyState, Field, FormError, Modal, Money, PageHeader, Pagination, QueryBoundary, StatusChip } from "../components/ui";
import { formatDate } from "../lib/format";
import { stateLabel } from "../lib/states";
import { useUrlState } from "../lib/useUrlState";

const GSTIN = /^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

const schema = z.object({
  name: z.string().trim().min(1, "Enter the customer name").max(200),
  state_code: z.string().min(1, "Select the customer's state"),
  gstin: z.string().trim().toUpperCase().refine((v) => v === "" || GSTIN.test(v), "Enter a valid 15-character GSTIN"),
  email: z.string().trim().refine((v) => v === "" || z.string().email().safeParse(v).success, "Enter a valid email"),
  phone: z.string().trim().max(32),
  billing_address: z.string().trim().max(500),
  default_terms_days: z.string().refine((v) => v === "" || (/^\d+$/.test(v) && Number(v) <= 365), "Enter whole days, 0 to 365"),
  has_written_agreement: z.boolean(),
});
type Values = z.infer<typeof schema>;

const blank: Values = { name: "", state_code: "", gstin: "", email: "", phone: "", billing_address: "", default_terms_days: "", has_written_agreement: false };

function toBody(v: Values): Schemas["CustomerCreate"] {
  return {
    name: v.name,
    state_code: v.state_code,
    gstin: v.gstin || null,
    email: v.email || null,
    phone: v.phone || null,
    billing_address: v.billing_address,
    default_terms_days: v.default_terms_days === "" ? null : Number(v.default_terms_days),
    has_written_agreement: v.has_written_agreement,
  };
}

function fromCustomer(c: Schemas["CustomerOut"]): Values {
  return {
    name: c.name, state_code: c.state_code, gstin: c.gstin ?? "", email: c.email ?? "", phone: c.phone ?? "",
    billing_address: c.billing_address, default_terms_days: String(c.default_terms_days), has_written_agreement: c.has_written_agreement,
  };
}

function CustomerForm({ initial, onSubmit, submitLabel, error, pending }: {
  initial: Values; onSubmit: (v: Values) => void; submitLabel: string; error: unknown; pending: boolean;
}) {
  const { register, handleSubmit, formState: { errors } } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: initial });
  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="form">
      <FormError error={error} />
      <Field label="Name" htmlFor="c-name" error={errors.name?.message}><input id="c-name" {...register("name")} /></Field>
      <Field label="State" htmlFor="c-state" error={errors.state_code?.message}><StateSelect id="c-state" {...register("state_code")} /></Field>
      <Field label="GSTIN (optional)" htmlFor="c-gstin" error={errors.gstin?.message} hint="Leave blank for an unregistered customer."><input id="c-gstin" {...register("gstin")} /></Field>
      <Field label="Email" htmlFor="c-email" error={errors.email?.message}><input id="c-email" type="email" {...register("email")} /></Field>
      <Field label="Phone" htmlFor="c-phone" error={errors.phone?.message}><input id="c-phone" {...register("phone")} /></Field>
      <Field label="Billing address" htmlFor="c-addr" error={errors.billing_address?.message}><textarea id="c-addr" rows={2} {...register("billing_address")} /></Field>
      <Field label="Payment terms (days)" htmlFor="c-terms" error={errors.default_terms_days?.message} hint="Blank uses your organisation's default."><input id="c-terms" inputMode="numeric" {...register("default_terms_days")} /></Field>
      <label className="check"><input type="checkbox" {...register("has_written_agreement")} /> A written agreement sets these terms (MSMED 45-day limit applies otherwise)</label>
      <button type="submit" className="btn btn-primary" disabled={pending}>{pending ? "Saving…" : submitLabel}</button>
    </form>
  );
}

export function CustomersPage() {
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const { values, offset, limit, set } = useUrlState(["q", "active"] as const);
  const [creating, setCreating] = useState(false);
  const query = useQuery({
    queryKey: ["customers", values.q, values.active, offset],
    queryFn: () => call(api.GET("/api/v1/customers", { params: { query: { q: values.q || undefined, is_active: values.active === "" ? undefined : values.active === "true", limit, offset } } })),
  });
  const create = useMutation({
    mutationFn: (v: Values) => call(api.POST("/api/v1/customers", { body: toBody(v) })),
    onSuccess: () => { setCreating(false); void queryClient.invalidateQueries({ queryKey: ["customers"] }); },
  });
  return (
    <>
      <PageHeader title="Customers" actions={can("customers.write") && <button type="button" className="btn btn-primary" onClick={() => { create.reset(); setCreating(true); }}>New customer</button>} />
      <div className="filters">
        <label>Search <input type="search" value={values.q} onChange={(e) => set({ q: e.target.value })} placeholder="Name or GSTIN" /></label>
        <label>Status
          <select value={values.active} onChange={(e) => set({ active: e.target.value })}>
            <option value="">All</option><option value="true">Active</option><option value="false">Inactive</option>
          </select>
        </label>
      </div>
      <QueryBoundary query={query}>
        {query.data && (query.data.items.length === 0 ? (
          <EmptyState title="No customers found" hint={values.q ? "Try a different search." : "Add your first customer to start invoicing."} />
        ) : (
          <>
            <table>
              <thead><tr><th>Name</th><th>State</th><th>GSTIN</th><th>Terms</th><th>Status</th></tr></thead>
              <tbody>
                {query.data.items.map((c) => (
                  <tr key={c.id}>
                    <td><Link to={`/customers/${c.id}`}>{c.name}</Link></td>
                    <td>{stateLabel(c.state_code)}</td>
                    <td>{c.gstin ?? "Unregistered"}</td>
                    <td>{c.default_terms_days} days</td>
                    <td><StatusChip value={c.is_active ? "active" : "inactive"} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={query.data.total} limit={limit} offset={offset} onChange={(o) => set({ offset: String(o) })} />
          </>
        ))}
      </QueryBoundary>
      {creating && (
        <Modal title="New customer" onClose={() => setCreating(false)}>
          <CustomerForm initial={blank} onSubmit={(v) => create.mutate(v)} submitLabel="Create customer" error={create.error} pending={create.isPending} />
        </Modal>
      )}
    </>
  );
}

export function CustomerDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const customer = useQuery({ queryKey: ["customers", id], queryFn: () => call(api.GET("/api/v1/customers/{customer_id}", { params: { path: { customer_id: id } } })) });
  const invoices = useQuery({
    queryKey: ["invoices", "customer", id],
    enabled: can("invoices.read"),
    queryFn: () => call(api.GET("/api/v1/invoices", { params: { query: { customer_id: id, limit: 50 } } })),
  });
  const deals = useQuery({
    queryKey: ["deals", "customer", id],
    enabled: can("deals.read"),
    queryFn: () => call(api.GET("/api/v1/deals", { params: { query: { customer_id: id, limit: 50 } } })),
  });
  const update = useMutation({
    mutationFn: (v: Values) => call(api.PATCH("/api/v1/customers/{customer_id}", { params: { path: { customer_id: id } }, body: toBody(v) })),
    onSuccess: () => { setEditing(false); void queryClient.invalidateQueries({ queryKey: ["customers"] }); },
  });
  const toggle = useMutation({
    mutationFn: (active: boolean) => call(api.PATCH("/api/v1/customers/{customer_id}", { params: { path: { customer_id: id } }, body: { is_active: active } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["customers"] }),
  });
  return (
    <QueryBoundary query={customer}>
      {customer.data && (
        <>
          <PageHeader
            title={customer.data.name}
            subtitle={`${stateLabel(customer.data.state_code)} · ${customer.data.gstin ?? "Unregistered"}`}
            actions={can("customers.write") && (
              <>
                <button type="button" className="btn" onClick={() => { update.reset(); setEditing(true); }}>Edit</button>
                <button type="button" className="btn" disabled={toggle.isPending} onClick={() => toggle.mutate(!customer.data.is_active)}>
                  {customer.data.is_active ? "Deactivate" : "Reactivate"}
                </button>
              </>
            )}
          />
          <section className="card">
            <dl className="details">
              <dt>Status</dt><dd><StatusChip value={customer.data.is_active ? "active" : "inactive"} /></dd>
              <dt>Email</dt><dd>{customer.data.email ?? "—"}</dd>
              <dt>Phone</dt><dd>{customer.data.phone ?? "—"}</dd>
              <dt>Address</dt><dd>{customer.data.billing_address || "—"}</dd>
              <dt>Payment terms</dt><dd>{customer.data.default_terms_days} days{customer.data.has_written_agreement ? " (written agreement)" : ""}</dd>
              <dt>Customer since</dt><dd>{formatDate(customer.data.created_at)}</dd>
            </dl>
          </section>
          {can("invoices.read") && (
            <section className="card" aria-labelledby="cust-inv">
              <h2 id="cust-inv">Invoices</h2>
              <QueryBoundary query={invoices}>
                {invoices.data && (invoices.data.items.length === 0 ? <EmptyState title="No invoices yet" /> : (
                  <table>
                    <thead><tr><th>Number</th><th>Issued</th><th>Status</th><th className="num">Total</th><th className="num">Due</th></tr></thead>
                    <tbody>
                      {invoices.data.items.map((i) => (
                        <tr key={i.id}>
                          <td><Link to={`/invoices/${i.id}`}>{i.number ?? "Draft"}</Link></td>
                          <td>{formatDate(i.issue_date)}</td>
                          <td><StatusChip value={i.status} /> {i.status === "issued" && <StatusChip value={i.payment_status} />}</td>
                          <td className="num"><Money paise={i.total_paise} /></td>
                          <td className="num"><Money paise={i.amount_due_paise} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ))}
              </QueryBoundary>
            </section>
          )}
          {can("deals.read") && (
            <section className="card" aria-labelledby="cust-deals">
              <h2 id="cust-deals">Deals</h2>
              <QueryBoundary query={deals}>
                {deals.data && (deals.data.items.length === 0 ? <EmptyState title="No deals yet" /> : (
                  <table>
                    <thead><tr><th>Title</th><th>Stage</th><th className="num">Estimate</th></tr></thead>
                    <tbody>
                      {deals.data.items.map((d) => (
                        <tr key={d.id}><td>{d.title}</td><td><StatusChip value={d.stage} /></td><td className="num"><Money paise={d.amount_estimate_paise} /></td></tr>
                      ))}
                    </tbody>
                  </table>
                ))}
              </QueryBoundary>
            </section>
          )}
          {editing && (
            <Modal title="Edit customer" onClose={() => setEditing(false)}>
              <CustomerForm initial={fromCustomer(customer.data)} onSubmit={(v) => update.mutate(v)} submitLabel="Save changes" error={update.error} pending={update.isPending} />
            </Modal>
          )}
        </>
      )}
    </QueryBoundary>
  );
}

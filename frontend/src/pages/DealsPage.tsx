import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router-dom";
import { z } from "zod";

import { api, call, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, Field, FormError, Modal, Money, PageHeader, Pagination, QueryBoundary, StatusChip } from "../components/ui";
import { formatDate, parseRupeesToPaise, titleCase } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";

type Stage = Schemas["DealOut"]["stage"];
const STAGES: Stage[] = ["lead", "qualified", "proposal", "won", "lost"];
// Mirror of the server's pipeline (backend app/domain/deal_rules.py): the next stage, or lost, from any open stage.
// A convenience only: the API rejects every other move.
const NEXT: Partial<Record<Stage, Stage>> = { lead: "qualified", qualified: "proposal", proposal: "won" };
export function allowedMoves(stage: Stage): Stage[] {
  const next = NEXT[stage];
  return next ? [next, "lost"] : [];
}

const schema = z.object({
  title: z.string().trim().min(1, "Enter a title").max(200),
  customer_id: z.string().min(1, "Select a customer"),
  amount: z.string().refine((v) => v === "" || parseRupeesToPaise(v) !== null, "Enter an amount like 25,000 or 1250.50"),
  expected_close_date: z.string(),
});
type Values = z.infer<typeof schema>;

function NewDealForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const customers = useQuery({ queryKey: ["customers", "options"], queryFn: () => call(api.GET("/api/v1/customers", { params: { query: { is_active: true, limit: 100 } } })) });
  const { register, handleSubmit, formState: { errors } } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { title: "", customer_id: "", amount: "", expected_close_date: "" } });
  const create = useMutation({
    mutationFn: (v: Values) => call(api.POST("/api/v1/deals", { body: {
      title: v.title, customer_id: v.customer_id,
      amount_estimate_paise: v.amount === "" ? 0 : (parseRupeesToPaise(v.amount) ?? 0),
      expected_close_date: v.expected_close_date || null,
    } })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["deals"] }); onDone(); },
  });
  return (
    <form className="form" noValidate onSubmit={handleSubmit((v) => create.mutate(v))}>
      <FormError error={create.error} />
      <Field label="Title" htmlFor="d-title" error={errors.title?.message}><input id="d-title" {...register("title")} /></Field>
      <Field label="Customer" htmlFor="d-cust" error={errors.customer_id?.message}>
        <select id="d-cust" {...register("customer_id")}>
          <option value="">{customers.isPending ? "Loading…" : "Select a customer"}</option>
          {customers.data?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </Field>
      <Field label="Estimated value (₹)" htmlFor="d-amt" error={errors.amount?.message}><input id="d-amt" inputMode="decimal" {...register("amount")} /></Field>
      <Field label="Expected close date" htmlFor="d-date"><input id="d-date" type="date" {...register("expected_close_date")} /></Field>
      <button type="submit" className="btn btn-primary" disabled={create.isPending}>{create.isPending ? "Saving…" : "Create deal"}</button>
    </form>
  );
}

function TransitionControl({ deal }: { deal: Schemas["DealOut"] }) {
  const queryClient = useQueryClient();
  const [reasonFor, setReasonFor] = useState<Stage | null>(null);
  const [reason, setReason] = useState("");
  const move = useMutation({
    mutationFn: (body: Schemas["DealTransition"]) => call(api.POST("/api/v1/deals/{deal_id}/transition", { params: { path: { deal_id: deal.id } }, body })),
    onSuccess: () => { setReasonFor(null); setReason(""); void queryClient.invalidateQueries({ queryKey: ["deals"] }); void queryClient.invalidateQueries({ queryKey: ["reports"] }); },
  });
  return (
    <div>
      <label className="sr-only" htmlFor={`stage-${deal.id}`}>Move {deal.title} to stage</label>
      <select id={`stage-${deal.id}`} value="" disabled={move.isPending || allowedMoves(deal.stage).length === 0} onChange={(e) => {
        const stage = e.target.value as Stage;
        if (!stage) return;
        if (stage === "lost") setReasonFor("lost"); else move.mutate({ stage });
      }}>
        <option value="">Move to…</option>
        {allowedMoves(deal.stage).map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
      </select>
      {move.error && !reasonFor && <FormError error={move.error} />}
      {reasonFor && (
        <Modal title={`Mark “${deal.title}” as lost`} onClose={() => setReasonFor(null)}>
          <form className="form" onSubmit={(e) => { e.preventDefault(); move.mutate({ stage: "lost", lost_reason: reason.trim() }); }}>
            <FormError error={move.error} />
            <Field label="Why was it lost?" htmlFor={`lost-${deal.id}`}><textarea id={`lost-${deal.id}`} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} required /></Field>
            <button type="submit" className="btn btn-primary" disabled={move.isPending || reason.trim() === ""}>Mark as lost</button>
          </form>
        </Modal>
      )}
    </div>
  );
}

function CreateInvoiceButton({ deal }: { deal: Schemas["DealOut"] }) {
  const navigate = useNavigate();
  const create = useMutation({
    mutationFn: () => call(api.POST("/api/v1/deals/{deal_id}/create-invoice", { params: { path: { deal_id: deal.id } } })),
    onSuccess: (invoice) => navigate(`/invoices/${invoice.id}/edit`),
  });
  return (
    <>
      <button type="button" className="btn" disabled={create.isPending} onClick={() => create.mutate()}>Create invoice</button>
      {create.error && <FormError error={create.error} />}
    </>
  );
}

export function DealsPage() {
  const { can } = useAuth();
  const { values, offset, limit, set } = useUrlState(["stage"] as const);
  const [creating, setCreating] = useState(false);
  const query = useQuery({
    queryKey: ["deals", values.stage, offset],
    queryFn: () => call(api.GET("/api/v1/deals", { params: { query: { stage: (values.stage || undefined) as Stage | undefined, limit, offset } } })),
  });
  return (
    <>
      <PageHeader title="Deals" actions={can("deals.write") && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New deal</button>} />
      <div className="filters">
        <label>Stage
          <select value={values.stage} onChange={(e) => set({ stage: e.target.value })}>
            <option value="">All stages</option>
            {STAGES.map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </select>
        </label>
      </div>
      <QueryBoundary query={query}>
        {query.data && (query.data.items.length === 0 ? (
          <EmptyState title="No deals found" hint="Create a deal to track it from lead to won." />
        ) : (
          <>
            <table>
              <thead><tr><th>Title</th><th>Customer</th><th>Stage</th><th className="num">Estimate</th><th>Expected close</th>{can("deals.write") && <th>Actions</th>}</tr></thead>
              <tbody>
                {query.data.items.map((d) => (
                  <tr key={d.id}>
                    <td>{d.title}{d.stage === "lost" && d.lost_reason && <div className="muted small">Lost: {d.lost_reason}</div>}</td>
                    <td><Link to={`/customers/${d.customer_id}`}>View customer</Link></td>
                    <td><StatusChip value={d.stage} /></td>
                    <td className="num"><Money paise={d.amount_estimate_paise} /></td>
                    <td>{formatDate(d.expected_close_date)}</td>
                    {can("deals.write") && (
                      <td className="row-actions">
                        <TransitionControl deal={d} />
                        {d.stage === "won" && can("invoices.draft") && <CreateInvoiceButton deal={d} />}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={query.data.total} limit={limit} offset={offset} onChange={(o) => set({ offset: String(o) })} />
          </>
        ))}
      </QueryBoundary>
      {creating && <Modal title="New deal" onClose={() => setCreating(false)}><NewDealForm onDone={() => setCreating(false)} /></Modal>}
    </>
  );
}

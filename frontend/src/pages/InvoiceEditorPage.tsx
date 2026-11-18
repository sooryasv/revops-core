import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { api, call, type Schemas } from "../api/client";
import { StateSelect } from "../components/StateSelect";
import { Field, FormError, Loading, Money, PageHeader, QueryBoundary } from "../components/ui";
import { formatBps, paiseToInput, parseRupeesToPaise } from "../lib/format";
import { useDebounced } from "../lib/useDebounced";

const RATES = [0, 500, 1800, 4000] as const;

const itemSchema = z.object({
  description: z.string().trim().min(1, "Describe the item"),
  hsn_sac: z.string().trim().regex(/^(\d{4}|\d{6}|\d{8})?$/, "HSN/SAC is 4, 6 or 8 digits"),
  quantity: z.string().regex(/^[1-9]\d{0,8}$/, "Whole number, at least 1"),
  unit_price: z.string().refine((v) => { const p = parseRupeesToPaise(v); return p !== null && p > 0; }, "Enter a price above zero, like 1250.50"),
  gst_rate_bp: z.string(),
});
const schema = z.object({
  customer_id: z.string().min(1, "Select a customer"),
  place_of_supply_state_code: z.string().min(1, "Select the place of supply"),
  payment_terms_days: z.string().regex(/^\d{1,3}$/, "Whole days, 0 to 365"),
  items: z.array(itemSchema).min(1, "Add at least one item"),
});
type Values = z.infer<typeof schema>;

const emptyItem = { description: "", hsn_sac: "", quantity: "1", unit_price: "", gst_rate_bp: "1800" };

function toItems(items: Values["items"]): Schemas["ItemIn"][] | null {
  const out: Schemas["ItemIn"][] = [];
  for (const it of items) {
    const price = parseRupeesToPaise(it.unit_price);
    if (!it.description.trim() || price === null || price <= 0 || !/^[1-9]\d{0,8}$/.test(it.quantity)) return null;
    out.push({
      description: it.description.trim(),
      hsn_sac: it.hsn_sac || null,
      quantity: Number(it.quantity),
      unit_price_paise: price,
      gst_rate_bp: Number(it.gst_rate_bp) as Schemas["ItemIn"]["gst_rate_bp"],
    });
  }
  return out;
}

export function InvoiceEditorPage() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const editing = id !== undefined;

  const existing = useQuery({
    queryKey: ["invoices", id],
    enabled: editing,
    queryFn: () => call(api.GET("/api/v1/invoices/{invoice_id}", { params: { path: { invoice_id: id ?? "" } } })),
  });
  const customers = useQuery({
    queryKey: ["customers", "options"],
    queryFn: () => call(api.GET("/api/v1/customers", { params: { query: { is_active: true, limit: 100 } } })),
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      customer_id: search.get("customer_id") ?? "", place_of_supply_state_code: "", payment_terms_days: "30", items: [emptyItem],
    },
  });
  const { register, control, handleSubmit, setValue, reset, formState: { errors } } = form;
  const { fields, append, remove } = useFieldArray({ control, name: "items" });

  useEffect(() => {
    const inv = existing.data;
    if (!inv) return;
    reset({
      customer_id: inv.customer_id,
      place_of_supply_state_code: inv.place_of_supply_state_code,
      payment_terms_days: String(inv.payment_terms_days),
      items: inv.items.map((i) => ({
        description: i.description, hsn_sac: i.hsn_sac ?? "", quantity: String(i.quantity),
        unit_price: paiseToInput(i.unit_price_paise), gst_rate_bp: String(i.gst_rate_bp),
      })),
    });
  }, [existing.data, reset]);

  const customerId = useWatch({ control, name: "customer_id" });
  const place = useWatch({ control, name: "place_of_supply_state_code" });
  const watchedItems = useWatch({ control, name: "items" });
  const customer = customers.data?.items.find((c) => c.id === customerId);

  // New invoice: default place of supply and terms from the chosen customer.
  useEffect(() => {
    if (editing || !customer) return;
    setValue("place_of_supply_state_code", customer.state_code);
    setValue("payment_terms_days", String(customer.default_terms_days));
  }, [customer, editing, setValue]);

  // The server is the only place GST is computed; the editor asks it to preview (03 section 10).
  const previewBody = useMemo(() => {
    const items = toItems(watchedItems ?? []);
    if (!items || items.length === 0 || !place) return null;
    return { customer_id: customerId || null, place_of_supply_state_code: place, items } satisfies Schemas["InvoicePreviewRequest"];
  }, [watchedItems, place, customerId]);
  const debounced = useDebounced(previewBody, 350);
  const preview = useQuery({
    queryKey: ["invoice-preview", debounced],
    enabled: debounced !== null,
    placeholderData: (prev) => prev,
    queryFn: () => call(api.POST("/api/v1/invoices/preview", { body: debounced as Schemas["InvoicePreviewRequest"] })),
  });

  const save = useMutation({
    mutationFn: async (v: Values) => {
      const items = toItems(v.items) ?? [];
      if (editing && existing.data) {
        return call(api.PUT("/api/v1/invoices/{invoice_id}", {
          params: { path: { invoice_id: existing.data.id } },
          body: { items, payment_terms_days: Number(v.payment_terms_days), place_of_supply_state_code: v.place_of_supply_state_code, version: existing.data.version },
        }));
      }
      return call(api.POST("/api/v1/invoices", {
        body: { customer_id: v.customer_id, deal_id: search.get("deal_id"), items, payment_terms_days: Number(v.payment_terms_days), place_of_supply_state_code: v.place_of_supply_state_code },
      }));
    },
    onSuccess: (invoice) => {
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      navigate(`/invoices/${invoice.id}`);
    },
  });

  if (editing && existing.isPending) return <Loading />;
  if (editing && existing.data && existing.data.status !== "draft") {
    return <div className="state error" role="alert"><p className="state-title">Only draft invoices can be edited.</p></div>;
  }

  const body = (
    <form noValidate onSubmit={handleSubmit((v) => save.mutate(v))} className="form">
      <FormError error={save.error} />
      {save.error && (save.error as { status?: number }).status === 409 && (
        <p className="muted">Someone else changed this invoice. Reload the page to see the latest version.</p>
      )}
      <div className="grid-3">
        <Field label="Customer" htmlFor="i-cust" error={errors.customer_id?.message}>
          <select id="i-cust" disabled={editing} {...register("customer_id")}>
            <option value="">{customers.isPending ? "Loading…" : "Select a customer"}</option>
            {customers.data?.items.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Place of supply" htmlFor="i-pos" error={errors.place_of_supply_state_code?.message}><StateSelect id="i-pos" {...register("place_of_supply_state_code")} /></Field>
        <Field label="Payment terms (days)" htmlFor="i-terms" error={errors.payment_terms_days?.message}><input id="i-terms" inputMode="numeric" {...register("payment_terms_days")} /></Field>
      </div>

      <h2>Items</h2>
      {errors.items?.root?.message && <p className="field-error" role="alert">{errors.items.root.message}</p>}
      <div className="items">
        {fields.map((f, idx) => (
          <fieldset key={f.id} className="item">
            <legend>Item {idx + 1}</legend>
            <Field label="Description" htmlFor={`it-${idx}-d`} error={errors.items?.[idx]?.description?.message}><input id={`it-${idx}-d`} {...register(`items.${idx}.description`)} /></Field>
            <Field label="HSN/SAC" htmlFor={`it-${idx}-h`} error={errors.items?.[idx]?.hsn_sac?.message}><input id={`it-${idx}-h`} inputMode="numeric" {...register(`items.${idx}.hsn_sac`)} /></Field>
            <Field label="Quantity" htmlFor={`it-${idx}-q`} error={errors.items?.[idx]?.quantity?.message}><input id={`it-${idx}-q`} inputMode="numeric" {...register(`items.${idx}.quantity`)} /></Field>
            <Field label="Unit price (₹)" htmlFor={`it-${idx}-p`} error={errors.items?.[idx]?.unit_price?.message}><input id={`it-${idx}-p`} inputMode="decimal" {...register(`items.${idx}.unit_price`)} /></Field>
            <Field label="GST rate" htmlFor={`it-${idx}-g`}>
              <select id={`it-${idx}-g`} {...register(`items.${idx}.gst_rate_bp`)}>
                {RATES.map((r) => <option key={r} value={r}>{formatBps(r)}</option>)}
              </select>
            </Field>
            {fields.length > 1 && <button type="button" className="btn btn-ghost" onClick={() => remove(idx)}>Remove item {idx + 1}</button>}
          </fieldset>
        ))}
      </div>
      <button type="button" className="btn" onClick={() => append(emptyItem)}>Add item</button>

      <section className="card totals" aria-live="polite" aria-labelledby="totals-h">
        <h2 id="totals-h">Totals {preview.isFetching && <span className="muted small">updating…</span>}</h2>
        {preview.isError ? <FormError error={preview.error} /> : preview.data ? (
          <dl className="details">
            <dt>Subtotal</dt><dd><Money paise={preview.data.subtotal_paise} /></dd>
            {preview.data.supply_type === "intra" ? (
              <><dt>CGST</dt><dd><Money paise={preview.data.cgst_paise} /></dd><dt>SGST</dt><dd><Money paise={preview.data.sgst_paise} /></dd></>
            ) : (
              <><dt>IGST</dt><dd><Money paise={preview.data.igst_paise} /></dd></>
            )}
            <dt><strong>Total</strong></dt><dd><strong><Money paise={preview.data.total_paise} /></strong></dd>
          </dl>
        ) : <p className="muted">Fill in the items and place of supply to see GST and totals.</p>}
      </section>

      <button type="submit" className="btn btn-primary" disabled={save.isPending}>{save.isPending ? "Saving…" : editing ? "Save draft" : "Create draft"}</button>
    </form>
  );

  return (
    <>
      <PageHeader title={editing ? "Edit draft invoice" : "New invoice"} />
      {editing ? <QueryBoundary query={existing}>{body}</QueryBoundary> : body}
    </>
  );
}

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api, call } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { StateSelect } from "../components/StateSelect";
import { Field, FormError, PageHeader, QueryBoundary } from "../components/ui";

const GSTIN = /^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const schema = z.object({
  name: z.string().trim().min(1, "Enter a name"),
  legal_name: z.string().trim().min(1, "Enter the legal name"),
  gstin: z.string().trim().toUpperCase().refine((v) => v === "" || GSTIN.test(v), "Enter a valid 15-character GSTIN"),
  state_code: z.string().min(1, "Select a state"),
  address: z.string().trim(),
  default_terms_days: z.string().regex(/^\d{1,3}$/, "Whole days, 0 to 365"),
  udyam_category: z.enum(["micro", "small", "medium", "none"]),
});
type Values = z.infer<typeof schema>;

export function SettingsPage() {
  const { can } = useAuth();
  const writable = can("org.write");
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);
  const org = useQuery({ queryKey: ["org"], queryFn: () => call(api.GET("/api/v1/org")) });
  const { register, handleSubmit, reset, formState: { errors } } = useForm<Values>({ resolver: zodResolver(schema) });
  useEffect(() => {
    if (org.data) reset({ name: org.data.name, legal_name: org.data.legal_name, gstin: org.data.gstin ?? "", state_code: org.data.state_code, address: org.data.address, default_terms_days: String(org.data.default_terms_days), udyam_category: org.data.udyam_category });
  }, [org.data, reset]);
  const save = useMutation({
    mutationFn: (v: Values) => call(api.PATCH("/api/v1/org", { body: { ...v, gstin: v.gstin || null, default_terms_days: Number(v.default_terms_days) } })),
    onSuccess: () => { setSaved(true); void queryClient.invalidateQueries({ queryKey: ["org"] }); void queryClient.invalidateQueries({ queryKey: ["me"] }); },
  });
  return (
    <>
      <PageHeader title="Settings" subtitle="Your organisation's details appear on every invoice." />
      <QueryBoundary query={org}>
        <form className="form card" noValidate onSubmit={handleSubmit((v) => { setSaved(false); save.mutate(v); })}>
          <FormError error={save.error} />
          {saved && <p className="notice" role="status">Settings saved.</p>}
          <fieldset disabled={!writable} className="plain-fieldset">
            <Field label="Display name" htmlFor="o-name" error={errors.name?.message}><input id="o-name" {...register("name")} /></Field>
            <Field label="Legal name" htmlFor="o-legal" error={errors.legal_name?.message}><input id="o-legal" {...register("legal_name")} /></Field>
            <Field label="GSTIN" htmlFor="o-gstin" error={errors.gstin?.message}><input id="o-gstin" {...register("gstin")} /></Field>
            <Field label="State" htmlFor="o-state" error={errors.state_code?.message}><StateSelect id="o-state" {...register("state_code")} /></Field>
            <Field label="Address" htmlFor="o-addr"><textarea id="o-addr" rows={2} {...register("address")} /></Field>
            <Field label="Default payment terms (days)" htmlFor="o-terms" error={errors.default_terms_days?.message}><input id="o-terms" inputMode="numeric" {...register("default_terms_days")} /></Field>
            <Field label="Udyam category" htmlFor="o-udyam" hint="Micro and small suppliers are bound by the 45-day payment limit.">
              <select id="o-udyam" {...register("udyam_category")}><option value="micro">Micro</option><option value="small">Small</option><option value="medium">Medium</option><option value="none">Not registered</option></select>
            </Field>
          </fieldset>
          {writable ? <button type="submit" className="btn btn-primary" disabled={save.isPending}>Save settings</button> : <p className="muted">Only an administrator can change these settings.</p>}
        </form>
      </QueryBoundary>
    </>
  );
}

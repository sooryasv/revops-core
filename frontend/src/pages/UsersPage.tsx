import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { api, call, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, Field, FormError, Modal, PageHeader, QueryBoundary, StatusChip } from "../components/ui";
import { formatDateTime } from "../lib/format";

type Role = Schemas["UserOut"]["role"];
const ROLES: Role[] = ["admin", "finance", "sales"];

const schema = z.object({
  full_name: z.string().trim().min(1, "Enter a name"),
  email: z.string().trim().email("Enter a valid email"),
  password: z.string().min(12, "Use at least 12 characters"),
  role: z.enum(["admin", "finance", "sales"]),
});
type Values = z.infer<typeof schema>;

function NewUserForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const { register, handleSubmit, formState: { errors } } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: { full_name: "", email: "", password: "", role: "sales" } });
  const create = useMutation({
    mutationFn: (v: Values) => call(api.POST("/api/v1/users", { body: v })),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["users"] }); onDone(); },
  });
  return (
    <form className="form" noValidate onSubmit={handleSubmit((v) => create.mutate(v))}>
      <FormError error={create.error} />
      <Field label="Full name" htmlFor="u-name" error={errors.full_name?.message}><input id="u-name" {...register("full_name")} /></Field>
      <Field label="Email" htmlFor="u-email" error={errors.email?.message}><input id="u-email" type="email" {...register("email")} /></Field>
      <Field label="Temporary password" htmlFor="u-pw" error={errors.password?.message}><input id="u-pw" type="password" autoComplete="new-password" {...register("password")} /></Field>
      <Field label="Role" htmlFor="u-role"><select id="u-role" {...register("role")}>{ROLES.map((r) => <option key={r} value={r}>{r}</option>)}</select></Field>
      <button type="submit" className="btn btn-primary" disabled={create.isPending}>Create user</button>
    </form>
  );
}

export function UsersPage() {
  const { me } = useAuth();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);
  const query = useQuery({ queryKey: ["users"], queryFn: () => call(api.GET("/api/v1/users")) });
  const patch = useMutation({
    mutationFn: (args: { id: string; body: Schemas["UserPatch"] }) => call(api.PATCH("/api/v1/users/{user_id}", { params: { path: { user_id: args.id } }, body: args.body })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["users"] }),
  });
  return (
    <>
      <PageHeader title="Users" actions={<button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>New user</button>} />
      <FormError error={patch.error} />
      <QueryBoundary query={query}>
        {query.data && (query.data.length === 0 ? <EmptyState title="No users" /> : (
          <table>
            <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Last sign-in</th><th>Status</th><th>Actions</th></tr></thead>
            <tbody>
              {query.data.map((u) => (
                <tr key={u.id}>
                  <td>{u.full_name}{u.id === me?.user_id && <span className="muted small"> (you)</span>}</td>
                  <td>{u.email}</td>
                  <td>
                    <label className="sr-only" htmlFor={`role-${u.id}`}>Role for {u.full_name}</label>
                    <select id={`role-${u.id}`} value={u.role} disabled={patch.isPending} onChange={(e) => patch.mutate({ id: u.id, body: { role: e.target.value as Role } })}>
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                  </td>
                  <td>{formatDateTime(u.last_login_at)}</td>
                  <td><StatusChip value={u.is_active ? "active" : "inactive"} /></td>
                  <td><button type="button" className="btn" disabled={patch.isPending} onClick={() => patch.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? "Deactivate" : "Reactivate"}</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
      </QueryBoundary>
      {creating && <Modal title="New user" onClose={() => setCreating(false)}><NewUserForm onDone={() => setCreating(false)} /></Modal>}
    </>
  );
}

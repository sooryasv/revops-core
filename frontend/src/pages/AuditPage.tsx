import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, call } from "../api/client";
import { EmptyState, PageHeader, Pagination, QueryBoundary } from "../components/ui";
import { formatDateTime } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";

export function AuditPage() {
  const { values, offset, limit, set } = useUrlState(["entity_type", "action", "from", "to"] as const, 25);
  const [open, setOpen] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["audit", values, offset],
    queryFn: () => call(api.GET("/api/v1/audit-logs", { params: { query: {
      entity_type: values.entity_type || undefined, action: values.action || undefined,
      from: values.from || undefined, to: values.to || undefined, limit, offset,
    } } })),
  });
  return (
    <>
      <PageHeader title="Audit log" subtitle="Every change to invoices, payments, users and settings." />
      <div className="filters">
        <label>Entity
          <select value={values.entity_type} onChange={(e) => set({ entity_type: e.target.value })}>
            <option value="">All</option>
            {["invoice", "payment", "customer", "deal", "user", "org", "reporting"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        <label>Action <input value={values.action} onChange={(e) => set({ action: e.target.value })} placeholder="e.g. invoice.issued" /></label>
        <label>From <input type="date" value={values.from} onChange={(e) => set({ from: e.target.value })} /></label>
        <label>To <input type="date" value={values.to} onChange={(e) => set({ to: e.target.value })} /></label>
      </div>
      <QueryBoundary query={query}>
        {query.data && (query.data.items.length === 0 ? <EmptyState title="No audit entries match" /> : (
          <>
            <table>
              <thead><tr><th>When</th><th>Action</th><th>Entity</th><th>Actor</th><th>Details</th></tr></thead>
              <tbody>
                {query.data.items.map((a) => (
                  <tr key={a.id}>
                    <td>{formatDateTime(a.created_at)}</td>
                    <td>{a.action}</td>
                    <td>{a.entity_type}</td>
                    <td>{a.actor_type}</td>
                    <td>
                      <button type="button" className="btn btn-ghost" aria-expanded={open === a.id} onClick={() => setOpen(open === a.id ? null : a.id)}>
                        {open === a.id ? "Hide" : "Show"}
                      </button>
                      {open === a.id && <pre className="json">{JSON.stringify({ before: a.before, after: a.after, request_id: a.request_id }, null, 2)}</pre>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={query.data.total} limit={limit} offset={offset} onChange={(o) => set({ offset: String(o) })} />
          </>
        ))}
      </QueryBoundary>
    </>
  );
}

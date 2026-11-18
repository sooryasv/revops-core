import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, call, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { EmptyState, Money, PageHeader, Pagination, QueryBoundary, StatusChip } from "../components/ui";
import { formatDate } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";

type Status = Schemas["InvoiceSummary"]["status"];
type PaymentStatus = Schemas["InvoiceSummary"]["payment_status"];

const SORTS: [string, string][] = [
  ["-created_at", "Newest first"], ["created_at", "Oldest first"], ["due_date", "Due date (soonest)"],
  ["-due_date", "Due date (latest)"], ["-total_paise", "Largest total"], ["number", "Number"],
];

export function InvoicesPage() {
  const { can } = useAuth();
  const { values, offset, limit, set } = useUrlState(["status", "payment_status", "overdue", "q", "issue_from", "issue_to", "sort", "customer_id"] as const);
  const sort = values.sort || "-created_at";
  const query = useQuery({
    queryKey: ["invoices", values, offset],
    queryFn: () => call(api.GET("/api/v1/invoices", { params: { query: {
      status: (values.status || undefined) as Status | undefined,
      payment_status: (values.payment_status || undefined) as PaymentStatus | undefined,
      overdue: values.overdue === "true" ? true : undefined,
      customer_id: values.customer_id || undefined,
      q: values.q || undefined,
      issue_from: values.issue_from || undefined,
      issue_to: values.issue_to || undefined,
      sort, limit, offset,
    } } })),
  });
  return (
    <>
      <PageHeader title="Invoices" actions={can("invoices.draft") && <Link className="btn btn-primary" to="/invoices/new">New invoice</Link>} />
      <div className="filters">
        <label>Search <input type="search" value={values.q} onChange={(e) => set({ q: e.target.value })} placeholder="Number or customer" /></label>
        <label>Status
          <select value={values.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">All</option><option value="draft">Draft</option><option value="issued">Issued</option><option value="void">Void</option>
          </select>
        </label>
        <label>Payment
          <select value={values.payment_status} onChange={(e) => set({ payment_status: e.target.value })}>
            <option value="">All</option><option value="unpaid">Unpaid</option><option value="partially_paid">Partially paid</option><option value="paid">Paid</option>
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={values.overdue === "true"} onChange={(e) => set({ overdue: e.target.checked ? "true" : "" })} /> Overdue only</label>
        <label>Issued from <input type="date" value={values.issue_from} onChange={(e) => set({ issue_from: e.target.value })} /></label>
        <label>Issued to <input type="date" value={values.issue_to} onChange={(e) => set({ issue_to: e.target.value })} /></label>
        <label>Sort
          <select value={sort} onChange={(e) => set({ sort: e.target.value })}>
            {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
      </div>
      <QueryBoundary query={query}>
        {query.data && (query.data.items.length === 0 ? (
          <EmptyState title="No invoices match these filters" hint="Clear a filter or create a new invoice." />
        ) : (
          <>
            <table>
              <thead><tr><th>Number</th><th>Customer</th><th>Issued</th><th>Due</th><th>Status</th><th className="num">Total</th><th className="num">Due now</th></tr></thead>
              <tbody>
                {query.data.items.map((i) => (
                  <tr key={i.id}>
                    <td><Link to={`/invoices/${i.id}`}>{i.number ?? "Draft"}</Link></td>
                    <td>{i.customer_name}</td>
                    <td>{formatDate(i.issue_date)}</td>
                    <td>{formatDate(i.due_date)}</td>
                    <td>
                      <StatusChip value={i.status} />{" "}
                      {i.status === "issued" && <StatusChip value={i.payment_status} />}{" "}
                      {i.is_overdue && <StatusChip value="overdue" label={`Overdue ${i.days_overdue}d`} />}
                    </td>
                    <td className="num"><Money paise={i.total_paise} /></td>
                    <td className="num"><Money paise={i.amount_due_paise} /></td>
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

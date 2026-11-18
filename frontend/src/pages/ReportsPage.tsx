import { useQuery } from "@tanstack/react-query";

import { api, call } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { BarList, CollectionsChart } from "../components/charts";
import { Money, PageHeader, QueryBoundary, EmptyState } from "../components/ui";
import { formatDate, titleCase } from "../lib/format";
import { useUrlState } from "../lib/useUrlState";

function Collections() {
  const { values, set } = useUrlState(["from", "to", "group"] as const);
  const group = values.group === "month" ? "month" : "day";
  const query = useQuery({
    queryKey: ["reports", "collections", values.from, values.to, group],
    queryFn: () =>
      call(api.GET("/api/v1/reports/collections", { params: { query: { from: values.from || undefined, to: values.to || undefined, group } } })),
  });
  return (
    <section className="card" aria-labelledby="coll-h">
      <h2 id="coll-h">Billed vs collected</h2>
      <div className="filters">
        <label>From <input type="date" value={values.from} onChange={(e) => set({ from: e.target.value })} /></label>
        <label>To <input type="date" value={values.to} onChange={(e) => set({ to: e.target.value })} /></label>
        <label>
          Group by
          <select value={group} onChange={(e) => set({ group: e.target.value })}>
            <option value="day">Day</option>
            <option value="month">Month</option>
          </select>
        </label>
      </div>
      <QueryBoundary query={query}>
        {query.data &&
          (query.data.rows.length === 0 ? (
            <EmptyState title="No activity in this range" hint="Try a wider date range." />
          ) : (
            <>
              <CollectionsChart points={query.data.rows.map((r) => ({ label: r.period, billed: r.billed_paise, collected: r.collected_paise }))} />
              <table>
                <thead><tr><th>Period</th><th className="num">Invoices issued</th><th className="num">Billed</th><th className="num">Payments</th><th className="num">Collected</th></tr></thead>
                <tbody>
                  {query.data.rows.map((r) => (
                    <tr key={r.period}>
                      <td>{r.period}</td>
                      <td className="num">{r.invoices_issued}</td>
                      <td className="num"><Money paise={r.billed_paise} /></td>
                      <td className="num">{r.payments_count}</td>
                      <td className="num"><Money paise={r.collected_paise} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {query.data.note && <p className="muted small">{query.data.note}</p>}
            </>
          ))}
      </QueryBoundary>
    </section>
  );
}

function Dso() {
  const query = useQuery({ queryKey: ["reports", "dso"], queryFn: () => call(api.GET("/api/v1/reports/dso", { params: { query: { months: 6 } } })) });
  return (
    <section className="card" aria-labelledby="dso-h">
      <h2 id="dso-h">DSO trend</h2>
      <QueryBoundary query={query}>
        {query.data && (
          <>
            <p>Current DSO (30 days): <strong>{query.data.current_dso_30d === null ? "—" : `${query.data.current_dso_30d.toFixed(1)} days`}</strong></p>
            <table>
              <thead><tr><th>Month</th><th className="num">Billed</th><th className="num">Collected</th><th className="num">Outstanding at month end</th><th className="num">DSO</th></tr></thead>
              <tbody>
                {query.data.months.map((m) => (
                  <tr key={m.month}>
                    <td>{m.month}</td>
                    <td className="num"><Money paise={m.billed_paise} /></td>
                    <td className="num"><Money paise={m.collected_paise} /></td>
                    <td className="num"><Money paise={m.outstanding_end_paise} /></td>
                    <td className="num">{m.dso === null ? "—" : m.dso.toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </QueryBoundary>
    </section>
  );
}

function Aging() {
  const query = useQuery({ queryKey: ["reports", "aging"], queryFn: () => call(api.GET("/api/v1/reports/aging")) });
  return (
    <section className="card" aria-labelledby="age-h">
      <h2 id="age-h">Receivables aging</h2>
      <QueryBoundary query={query}>
        {query.data && (
          <>
            <p className="muted">As of {formatDate(query.data.as_of_date)} · Total due <Money paise={query.data.total_due_paise} /></p>
            <BarList caption="Amount due by days overdue" data={query.data.buckets.map((b) => ({ label: b.bucket === "not_due" ? "Not yet due" : `${b.bucket} days`, value: b.due_paise, detail: `${b.invoices} inv` }))} />
          </>
        )}
      </QueryBoundary>
    </section>
  );
}

function Pipeline() {
  const query = useQuery({ queryKey: ["reports", "pipeline"], queryFn: () => call(api.GET("/api/v1/reports/pipeline")) });
  return (
    <section className="card" aria-labelledby="pipe2-h">
      <h2 id="pipe2-h">Pipeline by stage</h2>
      <QueryBoundary query={query}>
        {query.data && (
          <table>
            <thead><tr><th>Stage</th><th className="num">Deals</th><th className="num">Value</th></tr></thead>
            <tbody>
              {Object.entries(query.data.by_stage as Record<string, { count: number; value_paise: number }>).map(([stage, t]) => (
                <tr key={stage}>
                  <td>{titleCase(stage)}</td>
                  <td className="num">{t.count}</td>
                  <td className="num"><Money paise={t.value_paise} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </QueryBoundary>
    </section>
  );
}

export function ReportsPage() {
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="Reports" />
      {can("reports.finance") && (<><Collections /><Aging /><Dso /></>)}
      {can("reports.pipeline") && <Pipeline />}
    </>
  );
}

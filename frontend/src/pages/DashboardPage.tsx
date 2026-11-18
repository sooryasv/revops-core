import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { api, call } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { BarList } from "../components/charts";
import { EmptyState, Money, PageHeader, QueryBoundary } from "../components/ui";
import { formatDate, titleCase } from "../lib/format";

function Kpi({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{children}</div>
      {hint && <div className="muted small">{hint}</div>}
    </div>
  );
}

function FinanceDashboard() {
  const summary = useQuery({ queryKey: ["reports", "summary"], queryFn: () => call(api.GET("/api/v1/reports/summary")) });
  const aging = useQuery({ queryKey: ["reports", "aging"], queryFn: () => call(api.GET("/api/v1/reports/aging")) });
  return (
    <>
      <QueryBoundary query={summary}>
        {summary.data && (
          <>
            <p className="muted">As of {formatDate(summary.data.as_of_date)} (IST)</p>
            <div className="kpis">
              <Kpi label="Outstanding"><Money paise={summary.data.outstanding_paise} /></Kpi>
              <Kpi label="Overdue" hint={`${summary.data.overdue_invoices} invoices`}>
                <Money paise={summary.data.overdue_paise} />
              </Kpi>
              <Kpi label="Billed, last 30 days"><Money paise={summary.data.billed_last_30d_paise} /></Kpi>
              <Kpi label="Collected, last 30 days"><Money paise={summary.data.collected_last_30d_paise} /></Kpi>
              <Kpi label="DSO (30 days)" hint="Outstanding ÷ billed × 30">
                {summary.data.dso_30d === null ? "—" : `${summary.data.dso_30d.toFixed(1)} days`}
              </Kpi>
              <Kpi label="Webhook problems (7 days)" hint="Quarantined or rejected events">
                <span className={summary.data.webhook_problems_7d > 0 ? "text-bad" : ""}>
                  {summary.data.webhook_problems_7d}
                </span>
              </Kpi>
            </div>

            <div className="grid-2">
              <section className="card" aria-labelledby="top-overdue">
                <h2 id="top-overdue">Top overdue customers</h2>
                {summary.data.top_overdue_customers.length === 0 ? (
                  <EmptyState title="Nothing overdue" hint="Every issued invoice is within its terms." />
                ) : (
                  <table>
                    <thead><tr><th>Customer</th><th className="num">Invoices</th><th className="num">Overdue</th></tr></thead>
                    <tbody>
                      {summary.data.top_overdue_customers.map((c) => (
                        <tr key={c.customer_id}>
                          <td><Link to={`/customers/${c.customer_id}`}>{c.customer_name}</Link></td>
                          <td className="num">{c.invoices}</td>
                          <td className="num"><Money paise={c.overdue_paise} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
              <section className="card" aria-labelledby="recent-pay">
                <h2 id="recent-pay">Recent payments</h2>
                {summary.data.recent_payments.length === 0 ? (
                  <EmptyState title="No payments yet" hint="Payments appear here as soon as they are captured or recorded." />
                ) : (
                  <table>
                    <thead><tr><th>Invoice</th><th>Customer</th><th>Date</th><th className="num">Amount</th></tr></thead>
                    <tbody>
                      {summary.data.recent_payments.map((p) => (
                        <tr key={p.payment_id}>
                          <td><Link to={`/invoices/${p.invoice_id}`}>{p.invoice_number}</Link></td>
                          <td>{p.customer_name}</td>
                          <td>{formatDate(p.paid_on)} · {titleCase(p.provider)}</td>
                          <td className="num"><Money paise={p.amount_paise} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            </div>
          </>
        )}
      </QueryBoundary>

      <section className="card" aria-labelledby="aging-h">
        <h2 id="aging-h">Receivables aging</h2>
        <QueryBoundary query={aging}>
          {aging.data && (
            <BarList
              caption="Amount due by days overdue"
              data={aging.data.buckets.map((b) => ({
                label: b.bucket === "not_due" ? "Not yet due" : `${b.bucket} days`,
                value: b.due_paise,
                detail: `${b.invoices} inv`,
              }))}
            />
          )}
        </QueryBoundary>
      </section>
    </>
  );
}

function PipelineDashboard() {
  const pipeline = useQuery({ queryKey: ["reports", "pipeline"], queryFn: () => call(api.GET("/api/v1/reports/pipeline")) });
  return (
    <section className="card" aria-labelledby="pipe-h">
      <h2 id="pipe-h">Sales pipeline</h2>
      <QueryBoundary query={pipeline}>
        {pipeline.data && (
          <>
            <div className="kpis">
              <Kpi label="Open pipeline"><Money paise={pipeline.data.open_value_paise} /></Kpi>
              <Kpi label="Won, last 90 days" hint={`${pipeline.data.won_last_90d.count} deals`}>
                <Money paise={pipeline.data.won_last_90d.value_paise} />
              </Kpi>
              <Kpi label="Win rate (90 days)">
                {pipeline.data.win_rate_bp_90d === null ? "—" : `${(pipeline.data.win_rate_bp_90d / 100).toFixed(1)}%`}
              </Kpi>
            </div>
            {pipeline.data.stale_open_deals.length > 0 && (
              <>
                <h3>Deals sitting too long</h3>
                <ul className="plain">
                  {pipeline.data.stale_open_deals.map((d) => (
                    <li key={d.deal_id}>
                      <strong>{d.title}</strong> — {d.customer_name}, {titleCase(d.stage)} for {d.days_in_stage} days
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </QueryBoundary>
    </section>
  );
}

export function DashboardPage() {
  const { me, can } = useAuth();
  return (
    <>
      <PageHeader title="Dashboard" subtitle={me ? `Welcome back, ${me.full_name}` : undefined} />
      {can("reports.finance") && <FinanceDashboard />}
      {can("reports.pipeline") && <PipelineDashboard />}
    </>
  );
}

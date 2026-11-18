import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, Suspense, useState, type ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import type { Permission } from "./auth/permissions";
import { Layout } from "./components/Layout";
import { Loading, RouteErrorBoundary } from "./components/ui";
import { AuditPage } from "./pages/AuditPage";
import { CustomerDetailPage, CustomersPage } from "./pages/CustomersPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DealsPage } from "./pages/DealsPage";
import { InvoiceDetailPage } from "./pages/InvoiceDetailPage";
import { InvoiceEditorPage } from "./pages/InvoiceEditorPage";
import { InvoicesPage } from "./pages/InvoicesPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ReportsPage } from "./pages/ReportsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { UsersPage } from "./pages/UsersPage";

// The public pay page is its own chunk: Razorpay's script is only ever loaded from that route.
const PayPage = lazy(() => import("./pages/PayPage").then((m) => ({ default: m.PayPage })));

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

function RequirePermission({ needs, children }: { needs: Permission; children: ReactNode }) {
  const { can } = useAuth();
  if (!can(needs)) {
    return (
      <div className="state error" role="alert">
        <p className="state-title">You do not have access to this page.</p>
      </div>
    );
  }
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/pay/:token"
        element={
          <RouteErrorBoundary>
            <Suspense fallback={<Loading />}>
              <PayPage />
            </Suspense>
          </RouteErrorBoundary>
        }
      />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="customers" element={<RequirePermission needs="customers.read"><CustomersPage /></RequirePermission>} />
        <Route path="customers/:id" element={<RequirePermission needs="customers.read"><CustomerDetailPage /></RequirePermission>} />
        <Route path="deals" element={<RequirePermission needs="deals.read"><DealsPage /></RequirePermission>} />
        <Route path="invoices" element={<RequirePermission needs="invoices.read"><InvoicesPage /></RequirePermission>} />
        <Route path="invoices/new" element={<RequirePermission needs="invoices.draft"><InvoiceEditorPage /></RequirePermission>} />
        <Route path="invoices/:id" element={<RequirePermission needs="invoices.read"><InvoiceDetailPage /></RequirePermission>} />
        <Route path="invoices/:id/edit" element={<RequirePermission needs="invoices.draft"><InvoiceEditorPage /></RequirePermission>} />
        <Route path="reports" element={<RequirePermission needs="reports.pipeline"><ReportsPage /></RequirePermission>} />
        <Route path="users" element={<RequirePermission needs="users.manage"><UsersPage /></RequirePermission>} />
        <Route path="audit" element={<RequirePermission needs="audit.read"><AuditPage /></RequirePermission>} />
        <Route path="settings" element={<RequirePermission needs="org.read"><SettingsPage /></RequirePermission>} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        // Never retry client errors (auth, validation, not found); one retry for transient failures.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
      },
    },
  });
}

export function App() {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

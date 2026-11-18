import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import type { Permission } from "../auth/permissions";
import { RouteErrorBoundary, StatusChip } from "./ui";

const NAV: { to: string; label: string; needs?: Permission }[] = [
  { to: "/", label: "Dashboard" },
  { to: "/customers", label: "Customers", needs: "customers.read" },
  { to: "/deals", label: "Deals", needs: "deals.read" },
  { to: "/invoices", label: "Invoices", needs: "invoices.read" },
  { to: "/reports", label: "Reports", needs: "reports.pipeline" },
  { to: "/users", label: "Users", needs: "users.manage" },
  { to: "/audit", label: "Audit log", needs: "audit.read" },
  { to: "/settings", label: "Settings", needs: "org.read" },
];

export function Layout() {
  const { me, can, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  return (
    <div className="shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">RevOps Core</div>
        <nav aria-label="Main">
          <ul>
            {NAV.filter((n) => !n.needs || can(n.needs)).map((n) => (
              <li key={n.to}>
                <NavLink to={n.to} end={n.to === "/"}>
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      <div className="content">
        <header className="topbar">
          <span className="org">{me?.org_name}</span>
          <span className="who">
            {me?.full_name} {me && <StatusChip value={me.role} />}
          </span>
          <button
            type="button"
            className="btn"
            onClick={() => void logout().then(() => navigate("/login", { replace: true }))}
          >
            Sign out
          </button>
        </header>
        <main id="main" tabIndex={-1}>
          <RouteErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </RouteErrorBoundary>
        </main>
      </div>
    </div>
  );
}

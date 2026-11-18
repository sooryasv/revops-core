// Convenience mirror of backend app/core/permissions.py. Hiding a control is UX only; the API enforces every rule (03 section 10).
export type Role = "admin" | "finance" | "sales";

export type Permission =
  | "org.read" | "org.write" | "users.manage" | "customers.read" | "customers.write" | "deals.read" | "deals.write"
  | "invoices.read" | "invoices.draft" | "invoices.issue" | "invoices.void" | "invoices.paylink"
  | "payments.read" | "payments.manual" | "reports.finance" | "reports.pipeline" | "audit.read" | "admin.jobs";

const FINANCE: Permission[] = [
  "org.read", "customers.read", "customers.write", "deals.read", "invoices.read", "invoices.draft", "invoices.issue",
  "invoices.void", "invoices.paylink", "payments.read", "payments.manual", "reports.finance", "reports.pipeline",
];
const SALES: Permission[] = [
  "org.read", "customers.read", "customers.write", "deals.read", "deals.write", "invoices.read", "invoices.draft",
  "payments.read", "reports.pipeline",
];
const ALL: Permission[] = [
  ...new Set<Permission>([...FINANCE, ...SALES, "org.write", "users.manage", "audit.read", "admin.jobs"]),
];

export const ROLE_PERMISSIONS: Record<Role, ReadonlySet<Permission>> = {
  admin: new Set(ALL),
  finance: new Set(FINANCE),
  sales: new Set(SALES),
};

export function can(role: Role | undefined, permission: Permission): boolean {
  return role !== undefined && ROLE_PERMISSIONS[role].has(permission);
}

import { describe, expect, it } from "vitest";

import { can } from "./permissions";

describe("can", () => {
  it("admin has admin-only permissions", () => {
    expect(can("admin", "audit.read")).toBe(true);
    expect(can("admin", "users.manage")).toBe(true);
  });
  it("finance cannot manage users or read audit but can void and record payments", () => {
    expect(can("finance", "users.manage")).toBe(false);
    expect(can("finance", "audit.read")).toBe(false);
    expect(can("finance", "invoices.void")).toBe(true);
    expect(can("finance", "payments.manual")).toBe(true);
  });
  it("sales can draft but not issue, and works deals", () => {
    expect(can("sales", "invoices.draft")).toBe(true);
    expect(can("sales", "invoices.issue")).toBe(false);
    expect(can("sales", "deals.write")).toBe(true);
    expect(can("sales", "reports.finance")).toBe(false);
  });
  it("no role means no permission", () => {
    expect(can(undefined, "org.read")).toBe(false);
  });
});

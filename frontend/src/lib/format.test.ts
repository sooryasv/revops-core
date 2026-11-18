import { describe, expect, it } from "vitest";

import { formatBps, formatDate, formatDateTime, formatPaise, paiseToInput, parseRupeesToPaise, titleCase } from "./format";

describe("formatPaise", () => {
  it.each([
    [0, "₹0.00"],
    [5, "₹0.05"],
    [100, "₹1.00"],
    [12540050, "₹1,25,400.50"],
    [100000000, "₹10,00,000.00"],
    [-12550, "-₹125.50"],
    [99999999999, "₹99,99,99,999.99"],
  ])("%i paise -> %s", (paise, expected) => {
    expect(formatPaise(paise)).toBe(expected);
  });
});

describe("parseRupeesToPaise", () => {
  it.each([
    ["1,250.5", 125050],
    ["₹ 99", 9900],
    ["0.07", 7],
    ["100", 10000],
  ])("%s -> %i", (text, expected) => {
    expect(parseRupeesToPaise(text)).toBe(expected);
  });
  it.each(["", "abc", "1.234", "-5", "1.2.3"])("rejects %j", (text) => {
    expect(parseRupeesToPaise(text)).toBeNull();
  });
  it("is exact where floats are not (19.99 -> 1999)", () => {
    expect(parseRupeesToPaise("19.99")).toBe(1999);
    expect(parseRupeesToPaise("1.15")).toBe(115);
  });
});

describe("formatBps", () => {
  it("formats whole and fractional percentages", () => {
    expect(formatBps(1800)).toBe("18%");
    expect(formatBps(500)).toBe("5%");
    expect(formatBps(0)).toBe("0%");
    expect(formatBps(250)).toBe("2.50%");
  });
});

describe("dates", () => {
  it("formats a business date without a timezone shift", () => {
    expect(formatDate("2026-10-31")).toBe("31 Oct 2026");
    expect(formatDate("2026-01-01")).toBe("01 Jan 2026");
  });
  it("shows an em dash for missing or bad dates", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("nope")).toBe("—");
    expect(formatDateTime(undefined)).toBe("—");
  });
  it("renders instants in IST", () => {
    expect(formatDateTime("2026-10-31T18:30:00Z")).toContain("01 Nov 2026");
    expect(formatDateTime("2026-10-31T18:30:00Z")).toContain("IST");
  });
});

describe("titleCase", () => {
  it("humanises snake_case", () => {
    expect(titleCase("partially_paid")).toBe("Partially Paid");
  });
});

describe("paiseToInput", () => {
  it("round-trips with parseRupeesToPaise", () => {
    for (const paise of [0, 5, 100, 125050, 1999]) {
      expect(parseRupeesToPaise(paiseToInput(paise))).toBe(paise);
    }
    expect(paiseToInput(125050)).toBe("1250.50");
  });
});

// Display-only formatting. The server owns every money calculation (03 section 10): the UI never adds, multiplies
// or rounds amounts; it only turns integer paise into text and parses what a person typed into integer paise.

const inr = new Intl.NumberFormat("en-IN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** 12540050 -> "₹1,25,400.50". Integer paise in, string out; split by integer division so no float rounding occurs. */
export function formatPaise(paise: number): string {
  const negative = paise < 0;
  const abs = Math.abs(paise);
  const rupees = Math.trunc(abs / 100);
  const rest = abs % 100;
  const grouped = inr.format(rupees).replace(/\.00$/, "");
  return `${negative ? "-" : ""}₹${grouped}.${String(rest).padStart(2, "0")}`;
}

/** Parse a typed rupee amount ("1,250.5", "₹ 99") into integer paise, or null if it is not a valid amount. */
export function parseRupeesToPaise(input: string): number | null {
  const cleaned = input.replace(/[₹,\s]/g, "");
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) return null;
  const rupees = Number(match[1]);
  const paise = Number((match[2] ?? "").padEnd(2, "0") || "0");
  const total = rupees * 100 + paise;
  return Number.isSafeInteger(total) ? total : null;
}

/** Basis points to a percentage label: 1800 -> "18%", 500 -> "5%". */
export function formatBps(bps: number): string {
  return `${bps % 100 === 0 ? bps / 100 : (bps / 100).toFixed(2)}%`;
}

const dateFmt = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

/** "2026-10-31" (a business date) -> "31 Oct 2026". Parsed as a calendar date, never through a time zone. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return "—";
  return dateFmt.format(new Date(Date.UTC(y, m - 1, d)));
}

const dateTimeFmt = new Intl.DateTimeFormat("en-IN", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});

/** An instant (ISO timestamp) shown in IST. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : `${dateTimeFmt.format(date)} IST`;
}

export function titleCase(value: string): string {
  return value
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Integer paise -> editable rupee text ("125050" -> "1250.50"), for pre-filling form inputs. */
export function paiseToInput(paise: number): string {
  return `${Math.trunc(paise / 100)}.${String(paise % 100).padStart(2, "0")}`;
}

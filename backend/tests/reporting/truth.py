"""Ground truth for report numbers, computed in plain Python from the RAW tables. It deliberately shares no code with
the application (no domain helpers, no report SQL), so a bug in either cannot cancel out in the comparison."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

import asyncpg


def half_up_2dp(numerator: int, denominator: int) -> float:
    """round(numerator / denominator, 2) with halves going up, in integer arithmetic."""
    hundredths = (numerator * 200 + denominator) // (2 * denominator)
    return hundredths / 100


class Truth:
    def __init__(
        self, invoices: list[Any], payments: list[Any], deals: list[Any], today: date
    ) -> None:
        self.invoices = [dict(r) for r in invoices]
        self.payments = [dict(r) for r in payments]
        self.deals = [dict(r) for r in deals]
        self.today = today

    @classmethod
    async def load(cls, conn: asyncpg.Connection, org_id: str, today: date) -> Truth:
        invoices = await conn.fetch("SELECT * FROM invoices WHERE org_id = $1", UUID(org_id))
        payments = await conn.fetch("SELECT * FROM payments WHERE org_id = $1", UUID(org_id))
        deals = await conn.fetch("SELECT * FROM deals WHERE org_id = $1", UUID(org_id))
        return cls(invoices, payments, deals, today)

    # ---- invoices
    def issued(self) -> list[dict[str, Any]]:
        return [i for i in self.invoices if i["status"] == "issued"]

    @staticmethod
    def due(i: dict[str, Any]) -> int:
        return max(i["total_paise"] - i["amount_paid_paise"], 0)

    def days_overdue(self, i: dict[str, Any]) -> int:
        if i["status"] != "issued" or self.due(i) == 0 or i["due_date"] >= self.today:
            return 0
        return (self.today - i["due_date"]).days

    def summary(self) -> dict[str, Any]:
        issued = self.issued()
        outstanding = sum(self.due(i) for i in issued)
        overdue = sum(self.due(i) for i in issued if self.days_overdue(i) > 0)
        billed30 = sum(
            i["total_paise"] for i in issued if i["issue_date"] > self.today - timedelta(days=30)
        )
        collected30 = sum(
            p["amount_paise"]
            for p in self.payments
            if p["status"] == "captured" and p["paid_on"] > self.today - timedelta(days=30)
        )
        return {
            "outstanding_paise": outstanding, "overdue_paise": overdue,
            "overdue_invoices": sum(1 for i in issued if self.days_overdue(i) > 0),
            "open_invoices": sum(1 for i in issued if self.due(i) > 0),
            "billed_last_30d_paise": billed30, "collected_last_30d_paise": collected30,
            "dso_30d": half_up_2dp(outstanding * 30, billed30) if billed30 else None,
            "invoices": {s: sum(1 for i in self.invoices if i["status"] == s) for s in ("draft", "issued", "void")},
        }  # fmt: skip

    def aging(self) -> dict[str, tuple[int, int]]:
        out = dict.fromkeys(("not_due", "1-30", "31-60", "61-90", "90+"), (0, 0))
        for i in self.issued():
            if self.due(i) == 0:
                continue
            d = self.days_overdue(i)
            bucket = (
                "not_due"
                if d == 0
                else "1-30"
                if d <= 30
                else "31-60"
                if d <= 60
                else "61-90"
                if d <= 90
                else "90+"
            )
            n, amount = out[bucket]
            out[bucket] = (n + 1, amount + self.due(i))
        return out

    # ---- collections
    def daily(self) -> dict[date, dict[str, int]]:
        days: dict[date, dict[str, int]] = {}
        for i in self.issued():
            row = days.setdefault(
                i["issue_date"],
                {
                    "billed_paise": 0,
                    "collected_paise": 0,
                    "invoices_issued": 0,
                    "payments_count": 0,
                },
            )
            row["billed_paise"] += i["total_paise"]
            row["invoices_issued"] += 1
        for p in self.payments:
            if p["status"] == "captured":
                row = days.setdefault(
                    p["paid_on"],
                    {
                        "billed_paise": 0,
                        "collected_paise": 0,
                        "invoices_issued": 0,
                        "payments_count": 0,
                    },
                )
                row["collected_paise"] += p["amount_paise"]
                row["payments_count"] += 1
        return days

    # ---- DSO trend (02 section 3.2)
    def dso_months(self, months: int) -> list[dict[str, Any]]:
        result = []
        year, month = self.today.year, self.today.month
        for back in range(months - 1, -1, -1):
            m = month - back
            y = year
            while m <= 0:
                m += 12
                y -= 1
            start = date(y, m, 1)
            end = (date(y + (m == 12), (m % 12) + 1, 1)) - timedelta(days=1)
            billed = sum(i["total_paise"] for i in self.issued() if start <= i["issue_date"] <= end)
            collected = sum(
                p["amount_paise"]
                for p in self.payments
                if p["status"] == "captured" and start <= p["paid_on"] <= end
            )
            outstanding = sum(
                i["total_paise"] for i in self.issued() if i["issue_date"] <= end
            ) - sum(
                p["amount_paise"]
                for p in self.payments
                if p["status"] == "captured" and p["paid_on"] <= end
            )
            days_in_month = (end - start).days + 1
            result.append(
                {"month": f"{y:04d}-{m:02d}", "billed_paise": billed, "collected_paise": collected,
                 "outstanding_end_paise": outstanding,
                 "dso": half_up_2dp(outstanding * days_in_month, billed) if billed else None}
            )  # fmt: skip
        return result

    # ---- pipeline
    def pipeline(self) -> dict[str, Any]:
        open_stages = ("lead", "qualified", "proposal")
        by_stage = {s: {"count": 0, "value_paise": 0} for s in (*open_stages, "won", "lost")}
        for d in self.deals:
            by_stage[d["stage"]]["count"] += 1
            by_stage[d["stage"]]["value_paise"] += d["amount_estimate_paise"]

        def ist_day(ts: Any) -> date:
            return (ts + timedelta(hours=5, minutes=30)).date()

        won = [
            d
            for d in self.deals
            if d["stage"] == "won" and ist_day(d["closed_at"]) > self.today - timedelta(days=90)
        ]
        lost = [
            d
            for d in self.deals
            if d["stage"] == "lost" and ist_day(d["closed_at"]) > self.today - timedelta(days=90)
        ]
        closed = len(won) + len(lost)
        stale = sorted(
            (
                (self.today - ist_day(d["stage_changed_at"])).days
                for d in self.deals
                if d["stage"] in open_stages
                and (self.today - ist_day(d["stage_changed_at"])).days > 30
            ),
            reverse=True,
        )[:10]
        return {
            "by_stage": by_stage,
            "open_value_paise": sum(by_stage[s]["value_paise"] for s in open_stages),
            "won_last_90d": {"count": len(won), "value_paise": sum(d["amount_estimate_paise"] for d in won)},
            "lost_last_90d_count": len(lost),
            "win_rate_bp_90d": None if closed == 0 else (len(won) * 20000 + closed) // (2 * closed),
            "stale_days": stale,
        }  # fmt: skip

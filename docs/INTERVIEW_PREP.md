# Interview preparation

## Thirty-second pitch

"I built a multi-tenant lead-to-cash app for small Indian suppliers: pipeline, GST-correct invoices in integer paise, Razorpay
payments where signed, idempotent webhooks are the only source of truth, and reports. It has a repository-level tenant guard with
a leak-test suite, 594 backend tests on real PostgreSQL, Playwright end-to-end tests, and a read-only `ai` schema contract for a
follow-on agent. I built it with an AI coding agent under my direction and I can explain and rebuild the critical parts."

## Questions and short answers

**How do you stop one tenant seeing another's data?** Every tenant table has `org_id`; child tables use composite `(org_id, id)` foreign keys; services can only reach tenant tables through `TenantRepository`, which always filters by the caller's `org_id`; foreign ids are 404; a leak suite hits every route as the other tenant; an architecture test pins the few places that may bypass the guard. I chose that over row-level security for debuggability and said what I would add if the team grew (RLS as a second layer).

**Why integer paise?** Floats drift; decimals leak into JSON and ORMs. Integers add exactly and match Razorpay's API. Rounding happens once, per line, half-up.

**What happens if Razorpay delivers the same webhook twice, or two at once?** The first insert of the event id wins (`ON CONFLICT DO NOTHING`); the second sees `duplicate` and returns 200. The invoice is locked while applied, status only moves forward, and `amount_paid` is recomputed as the sum of captured payments, so ordering and repetition cannot corrupt it. I broke the claim logic on purpose and six tests failed.

**Why doesn't the browser callback mark the invoice paid?** Anyone can call a client callback. The server verifies the signature and answers "pending"; only the signed webhook changes money.

**How is invoice numbering gap-free?** Per organisation and financial year, a counter row is locked inside the issue transaction; a failed issue rolls back the increment; voids keep their number. A concurrency test issues invoices in parallel and asserts consecutive numbers.

**How did you handle "today" for overdue invoices?** The business date is IST everywhere (`today_ist()` in Python, `timezone('Asia/Kolkata', now())` in SQL). Due today is not overdue; a test moves the clock across 23:00/00:00 IST while the UTC date is unchanged.

**What does the AI-facing contract guarantee?** A separate role that can only SELECT from seven views; each view filters by a transaction-local `app.current_org`, and an unset value returns nothing. A load test applies the exported package to an empty database and checks isolation.

**How did you test the critical paths, and how do you know the tests can fail?** Unit and property tests for the domain, real-PostgreSQL integration and concurrency tests, API and security suites, E2E in a browser. For each critical test I broke the code once (tenant filter, RBAC, signature check, idempotency, IST boundary, client drift) and recorded that it failed.

**What did the AI agent do and what did you do?** I set scope, wrote the specification and gates, reviewed each milestone report, and verify the unverified items; the agent wrote code and tests, and had to label every claim RAN, WROTE-NOT-RUN or ASSUMED. (Be ready to open any file and explain it; see the study guide.)

**What would you do next?** Redis-backed rate limits, keyset pagination, type-ahead customer picker, RLS as defence in depth, credit notes and e-invoicing, real Razorpay verification, observability.

**What is the biggest weakness?** Several external behaviours are unverified until run live (Razorpay CSP domains and payload quirks, Docker, Azure); they are listed in `.build/UNVERIFIED.md` rather than hidden.

## Whiteboard topics to be ready for

Request lifecycle through the layers; the webhook sequence diagram; why derived balances beat counters; composite keys versus RLS; JWT in an HttpOnly cookie versus localStorage; what `SECURITY DEFINER` does and its risks (pinned `search_path`).

## DSA practice list (20 problems; no solutions here)

Arrays and hash maps
1. Two Sum
2. Contains Duplicate
3. Valid Anagram
4. Group Anagrams
5. Top K Frequent Elements
6. Product of Array Except Self

Prefix sums
7. Subarray Sum Equals K
8. Range Sum Query (immutable)
9. Contiguous Array
10. Continuous Subarray Sum

Two pointers and sliding window
11. Valid Palindrome
12. 3Sum
13. Container With Most Water
14. Longest Substring Without Repeating Characters
15. Minimum Window Substring

Strings
16. Longest Common Prefix
17. Valid Parentheses
18. Longest Palindromic Substring
19. Decode Ways
20. String to Integer (atoi)

Tie each pattern to this project when you can: prefix sums ↔ running collections (`sql_drills.sql` Q4), hash maps ↔ webhook dedupe by event id, two pointers ↔ merging two sorted ledgers, sliding window ↔ rate limiting.

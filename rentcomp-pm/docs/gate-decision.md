# T-S3 Go/No-Go Gate — Decision Record (broad pull)

**Run date:** 2026-07-26
**Subject:** 3651 S Wood St, Chicago, IL 60609
**Mode:** broad pull — literal daysOld=1:1095; seasonal windows applied locally
**Window (year-agnostic):** 07-28..08-20, 3 years back, padded ±90d inclusive
**Calls on ledger this month:** 8
**Raw records pulled:** 539
**Raw distinct addresses/ids (diagnostic only):** 539
**In-window distinct comps (verdict basis):** 337

Per-window in-window distinct counts:
- 2026 window: 70 in-window distinct comps
- 2025 window: 155 in-window distinct comps
- 2024 window: 112 in-window distinct comps

**Verdict: GO**

(Threshold: >=15 distinct comps whose listedDate lands inside ANY padded
seasonal window — the same ±90-day inclusive windows a windowed pull
would have requested on the wire, applied locally to the broad pull instead.
Records with a missing or unparseable listedDate cannot be verified in-window:
they are excluded from the verdict count but are NOT an error — they remain in
the raw fixture data for the F4 pipeline to handle. Distinctness rule:
formattedAddress, falling back to id — same as the windowed-mode raw count.)

Raw responses saved to `fixtures/live-samples/` — these seed the entire build's
fixture-mode development going forward (WORKFLOW.md §6).

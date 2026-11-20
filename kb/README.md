# Knowledge Base (KB) — Lakrish Mart / "Shop" Cart platform

This folder is the **structured knowledge base** for the project. It distills the
running decisions log and the code into focused, readable articles you can hand to
anyone who needs to understand the system fast.

## Where the project knowledge lives

| Source | What it is | Keep updating? |
|--------|-----------|----------------|
| `kb/` (this folder) | **Organized reference articles** — the stable "how it works" view. | Yes — update when behaviour changes. |
| `../PROJECT_NOTES.md` | **Chronological decisions / changelog** (Stage 1 → 8.3). The source of truth for *why* and *when*. | Yes — append new decisions here. |
| `../DOMAIN_MODEL.md` | The one foundational rule: the platform is distributor-based. | Rarely — only if the core model changes. |
| `../README.md` | Local setup & run instructions. | When setup changes. |

Rule of thumb: **PROJECT_NOTES.md** = the diary (append-only history).
**kb/** = the handbook (rewritten to stay current).

## Articles

1. [01 — Project Overview](01-project-overview.md) — what the product is, tech stack, how to run it.
2. [02 — Domain Model & Roles](02-domain-model-and-roles.md) — the supply chain, the 6 roles, naming map.
3. [03 — Apps & Architecture](03-apps-and-architecture.md) — Django apps, URLs, data policies.
4. [04 — Pricing & Stock Flow](04-pricing-and-stock-flow.md) — how a rupee and a unit of stock move through tiers.
5. [05 — Features Catalogue](05-features.md) — rentals, own-products, auto-restock, direct supply, etc.
6. [06 — Testing & Operations](06-testing-and-ops.md) — test suite, backups, seeds, verification habits.
7. [07 — Supplier Shipment Routing](07-supplier-shipment-routing.md) — how a supplier routes a product, skips tiers, and the collect/send handover. Live explainer at `/supplier-shipment/`.
8. [08 — Supplier Suspension](08-supplier-suspension.md) — what suspending a supplier does: login block, in-progress-order guard, and the buffer-stock-aware product-hiding cascade. **Source of truth for suspension behaviour.**
9. [09 — Account Lifecycle](09-account-lifecycle.md) — the Active → Suspended → Deleted (soft) → Hard-deleted (purged) flow for all partner roles + customers, the grace-period setting, and the purge job. **Source of truth for the account lifecycle.**
10. [10 — Bundle Supply Chain](10-bundle-supply-chain.md) — why bundle rules are carried down the chain (Supplier → Stockist → Distributor → Marketplace), the rules, the settings pages, the no-buffer switch and restock routing. **Source of truth for bundle behaviour.**
11. [11 — Requirements and Decision Log](11-requirements-decision-log.md) — the owner's requests and answers, with plan changes marked **PLAN CHANGE**.
12. [12 — Handover: Owner Context, Decisions and Open Items](12-handover-owner-context.md) — **read first in a new session.** Owner's working rules, decisions and where they live in the code, current state, open items, and mistakes to avoid.

13. [13 - Stock Availability, Blocking and Returns](13-stock-availability-and-blocking.md) - free stock, blocking on requests, customer-only cancel and return to source. **Source of truth for stock rules.**

14. [14 - Stock Flow Test Scenarios, Results and Open Doubts](14-stock-flow-tests-and-doubts.md) - scenario list with test names, results, and open doubts. Use it to repeat the tests on another system.

_Last reviewed: 2026-11-18 (articles 10–12 added: bundle chain, decision log, handover; reflects PROJECT_NOTES.md through the 2026-11-18 changes)._

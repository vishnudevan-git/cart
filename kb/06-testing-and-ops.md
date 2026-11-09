# 06 — Testing & Operations

## Test suite

Permanent tests live in the apps (the old throwaway scratchpad crawlers were made
permanent). As of the latest quality pass (2026-10-31), **118 tests, all green**.

| Location | Covers |
|----------|--------|
| `accounts/factories.py` | `build_world()` fixture — every role, two rival shops, a customer per shop, one shared product. |
| `accounts/tests.py` | Single-role invariants, `customer_only` gating, no `{# #}` template leaks. |
| `distributors/tests.py` | Own-product creation/pricing + cross-shop & upstream isolation. |
| `stockist/tests.py` | Stock rules per tier, routing, direct supply, auto-restock. |
| `rentals/tests.py` | Master switch gating, wallet no-overspend, wallet vs points separation, no double-booking, zero-commission credit, isolation. |
| `stockist/test_price_flow_trace.py` | End-to-end rupee trace through every tier (run `-v2`). |
| `lakrish_mart/test_smoke.py` | Walks every resolvable GET URL as all 7 role contexts; fails on any 5xx. |

Run:
```powershell
venv\Scripts\python.exe manage.py test
```
`settings.py` swaps to the MD5 password hasher **only during tests**, so the full
suite runs in well under a minute.

## Standard verification habits (from the decisions log)

Before declaring work done, the project historically runs a battery:
- `manage.py check` / `check --deploy`
- `makemigrations --check` (no migration drift)
- `python -m compileall` (syntax)
- `collectstatic`
- full test suite
- **URL crawl** across all 7 role contexts (`scripts/url_smoke_audit.py` /
  the smoke test) — expect 0 HTTP 5xx
- authorization crawl (wrong-role access returns 302/403, no bypass)
- data-integrity + security checks
- mobile check at ~375px (no horizontal overflow)

## Backups

`backup.py` (project root) — one command:

By default it leaves the running server alone and creates a restore zip. Use
`--stop-server` only when you deliberately want an offline backup on a
Linux-hosted deployment; Windows process discovery is not implemented, so stop
the Windows dev server manually first when an offline checkpoint is required.

- checkpoints the SQLite WAL,
- zips **everything including `db.sqlite3`, media, and `.env`** (it is a restore
  backup, not a git export — only `__pycache__`/`venv`/`.git`/`staticfiles` are
  skipped),
- verifies the archive CRC,
- **never prunes** old backups (no-delete policy).

Output: `../Cart-backups/Cart_<timestamp>.zip`.
Flags: `-o DIR`, `--stop-server`, `--keep-running` (legacy no-op), `--list`.

## Seeding & utilities

| Script | Purpose |
|--------|---------|
| `seed_products.py`, `seed_distributors.py`, `setup_data.py` | Seed catalog / tiers / base data. |
| `scripts/minimal_seed.py`, `mass_seed_scale.py` | **Destructive demo-only fixture generators.** They erase non-superuser users and categories; they refuse to run unless `LAKRISH_DB_MODE=demo` and `LAKRISH_ALLOW_DESTRUCTIVE_SEED=YES` are both set. Never use on `db.sqlite3`. |
| `scripts/full_test_seed.py`, `seed_network.py` | Additive demo/test seeds. See each script header for the exact PowerShell command and created data. |
| `scripts/setup_smoke_users.py` | Provisions one dedicated account and required profile for each of the six authenticated roles. |
| `scripts/url_smoke_audit.py` | Standalone URL crawler: guest + all six authenticated roles; exits non-zero if a required role fixture is missing. |
| `scripts/transaction_product_customer_report.py` | **Mutating integration-flow fixture**, not a read-only report: it can create/reuse users, profiles, catalogue/pricing, cart data, and an order. It writes ignored `transaction_customer_flow_report.html`; use only on a disposable demo DB. |
| `check_dupes.py`, `compile_messages.py` | Dupe check / i18n compile. |

## Performance notes

Indexes added **after measuring** at 40k rows (`products.0018`):
`product_shared_lookup_idx`, `product_owner_idx`, `variant_product_active_idx`.
A `(stock, is_active)` index was tested and **rejected** (made low-stock slower) —
don't add it back without re-measuring.

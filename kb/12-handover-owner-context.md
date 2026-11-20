# 12 - Handover: Owner Context, Decisions and Open Items

Read this first in any new session. It holds the working rules, what was decided,
where each decision lives in the code, what is still open, and what went wrong
before. Detailed rules are in articles 10 and 11. Engineering history is in
`../PROJECT_NOTES.md`.

Last updated: 2026-11-18.

---

## 1. How the owner works

- Ask before any destructive action (deletes, migrations, password changes).
  State the target database first. Take a backup first (`db.sqlite3.bak-...`).
- The owner says "test environment" about `db.sqlite3`, but `lakrish_mart/settings.py`
  labels it `PROD` by default (`LAKRISH_DB_MODE=prod`). Confirm the database name
  before any delete. `db_demo.sqlite3` is used with `LAKRISH_DB_MODE=demo`.
- Log every owner request and answer in `kb/11`. Mark every change of plan as
  **PLAN CHANGE**.
- Keep the knowledge base current. Update the matching article in the same turn
  as the code change.
- Use plain language. The owner reads English with some Tamil-English phrasing.
  Short answers with the exact screen, URL, or count help most.
- "Log every action everywhere" is a standing rule. See section 4.
- The owner asks for a fix once the cause is clear. When the decision is
  consequential, give options and a recommendation, then ask.

## 2. Domain rules (stable)

Supply chain: **Supplier -> Stockist -> Distributor (code `wholesaler`, UI
"Distributor") -> Marketplace (code `distributor`, UI "Marketplace" = shop) ->
Customer**. Code and UI names differ. See `kb/02`.

- A shop's own login has role `distributor`. It is linked to the shop by the same
  foreign key as its customers, so count customers only with `role='customer'`.
- Customers always buy singles. Bundle rules apply only between businesses.
- Stock is one count per tier. A bundle is a count, not a separate stock. A bundle
  of 10 takes 10 units. Nothing is packed or unpacked. (Owner: "bundle is nothing
  but matching the count of loose with the bundle count.")
- A tier's stock rises only through a delivered restock request. Manual changes
  can only reduce it (except a supplier's own stock and a shop's own products).
- Customers never pay below the chain cost (supplier price, stockist charges, and
  distributor margin). The shop price is raised to that floor.
- A supplier's product shows to shops only after its stockist confirms the
  handling charges (`charges_confirmed`). A stockist releases a product on
  `/stockist/processing-charges/`, and only if a charge with value above 0 applies.
- A suspended supplier's products show only where the shop still has downstream
  stock (a price row).

## 3. Decisions and where they live

| Topic | Decision | Code / doc |
|---|---|---|
| Bundle chain | Each tier (stockist, distributor) has global, supplier and product settings: Singles allowed, Bundle only, or Follow upstream. Most specific wins. | `products/bundle_chain.py`, `TierBundleRule`, `kb/10` |
| No buffer stock | One switch on the Distributor's My Stock page covers pack and loose requests. ON: shop requests go straight to the stockist. OFF: they wait at the Distributor. | `Wholesaler.no_buffer_stock`, `routes_via_wholesaler()`, `kb/10` |
| Bottom-up search | When no tier enables bundles, the search runs from the shop upward. The supplier is the last stop. (PLAN CHANGE from "goes to supplier".) | `kb/10`, `kb/11` |
| Checkout stock | A line filled from shop stock does not reduce the supplier's stock. | `orders/views.py`, `orders/test_shop_stock_checkout.py` |
| Bulk actions | Customers page: multi-select with Enable, Disable, Delete, Assign marketplace, Restore, Delete permanently. Each asks for confirmation. | `admin_panel/views.py::customer_bulk_action`, `kb/05` |
| Assign confirmation | Every marketplace assignment asks first. Cancel resets the dropdown. The prompt shows the distributor (line 2) and stockist (line 3). Missing links are left out. | `templates/admin_panel/customer_list.html` |
| Marketplace chain | Each customer row shows the distributor and stockist above the marketplace. | `Distributor.upstream_*`, `chain_label` |
| Distributor customer count | Counts customers only. The shop's own login is in the Username column. | `admin_panel/views.py::distributor_list` |
| Variant count | Admin products list counts live variant types and live packs (2 variants · 8 packs for GREE-NUTS-001). | `admin_panel/views.py::product_list` |
| Storefront supplier status | Suppliers have only `approved` and `suspended`. The storefront shows products of any non-suspended supplier. | `products/views.py::_product_qs` |
| Stock unit storage | Stock is stored in grams (`master_unit='g'`). Entering kilograms saves grams. `kg` and `g` are separate filter choices. | `products/forms.py`, `kb/11` |
| 50 g rule | A grams product whose active packs are all 50 g or more is in the kilogram filter group. Any pack under 50 g keeps it in grams. Millilitres are unchanged. | `products/stock_unit.py`, `kb/11` |
| Pack size filter | Lists the effective unit and pack size (`kg:1000`, `g:10`, `ml:1000`). Follows the Stock unit filter. | `suppliers/views.py::product_list` |
| Request logging | Every request is logged with its posted fields. Passwords and tokens show as `***`. Free text is logged as a count only. | `admin_panel/middleware.py`, `kb/06` |
| Bulk action logs | `CUSTOMER_BULK | RECEIVED` and `| DONE` lines in the activity log. | `admin_panel/views.py` |

## 4. Logging (standing rule)

- Request log: `logs/requests_<server start time>.log`. One line per request.
  POST lines list field names and safe values (IDs, action, status).
- Business log: `logs/activity.log` (orders, payments, users, stock, bulk events).
- Not logged: clicks that send no request. A browser-side logger would be needed.
- Tests write to the same log files, so `admin@test.com` entries come from tests.

## 5. Environment facts

- Dev server: `python manage.py runserver 9000` (owner uses port 9000). Activate
  the venv with `.venv-win\Scripts\activate`.
- Database: `db.sqlite3` (git-ignored). Backups: `db.sqlite3.bak-before-*`.
  Always make a new one before a migration or delete.
- Tests: `python manage.py test` (196 at last run). Targeted runs, for example
  `python manage.py test admin_panel.test_customer_bulk`.
- Browser testing: Microsoft Edge is installed. Playwright lives in
  `%USERPROFILE%\pwtest` (outside the project). Node.js is not installed. Use
  `channel="msedge"`. Name scripts so they don't shadow stdlib modules (not
  `inspect.py`).
- The owner's logins: supplier `9100000010` (Green Farms Organics), stockist
  `9000000004`, distributor `9710000004` (Coimbatore Wholesale), shop
  `9100000020` (Anna Nagar Organics), customer `9100000033`. Admin `9000000005`.
  Passwords are not stored here. Ask the owner, or reset with the owner's approval.
- Demo data: `scripts/bundle_demo_setup.py` (GREE-STXX-001/002/003).
  `scripts/bulk_singles_sim.py` runs real customer checkouts and checks the stock.
  `scripts/bulk_test_orders.py` creates test orders.

## 6. Current state (2026-11-18)

- Test suite: 196 tests. Nine failures existed before the bundle work and are
  unchanged. They are in `products` (bulk source visibility), `stockist`
  (price-flow trace, loose supply, manual increase), `suppliers` (direct loose
  supply, pack rules). Fix or accept them with the owner.
- Orders: the old orders were hard-deleted on 2026-11-17 (18 orders, 39 items,
  13 shipments, 14 payments). Backup kept. Four bulk test orders exist.
- Uncommitted work: many files, including the bundle feature, several older
  migrations (`products` 0031–0038, `distributors` 0042–0043, `stockist` 0008,
  `suppliers` 0009), and this knowledge base. Nothing has been pushed. A new
  machine needs these committed first. Ask the owner before committing or pushing.
- Shop `Anna Nagar Organics` (id 6): 4 customers. Distributor Coimbatore Wholesale
  (stockist "Stockist"). Its listing still includes a deleted product (Shop Soap
  082731). It is hidden, but the listing should be cleaned.
- GREE-NUTS-001 (Cashews (Whole), Green Farms Organics, product 112): 3 variant
  types (W450, W320, W180). W180 has no active packs. Stock unit `g`. Its packs
  are 100 g, 250 g, 500 g and 1 kg. It is under Kilograms by the 50 g rule. No shop
  lists it yet.
- GREE-ORGA-002 (Organic Ghee, product 111): allow_loose is off, so the Stock unit
  dropdown is locked. The owner asked why. The answer: the unit only applies to
  loose stock, and loose is off. The owner has not decided whether to make the
  unit editable when loose is off.
- "Almonds New" (product 116): stored unit `g`, live packs 1 kg and 2 kg. Under
  Kilograms by the 50 g rule.

## 7. Open items (ask the owner)

1. **Stock unit when loose is off** (product 111): allow editing the unit when no
   loose stock exists? Proposed, not decided.
2. **Kilogram storage**: keep grams storage with the filter rule (current), or
   store `kg` and `l` as the unit? That needs changes wherever `g` and `ml` are
   checked (for example, loose request conversion in `distributors/views.py`).
3. **Millilitres**: apply the 50 ml rule to Litres, as for grams?
4. **Browser checks not done**: the Customers page confirms, the Delete flow after
   the duplicate-checkbox fix, and the supplier Pack size list. Node is not
   installed, so the page script has not been syntax-checked by a JS engine. Edge
   checks worked (see `kb/11`, 2026-11-18).
5. **Nine pre-existing test failures** (see section 6).
6. **Shop listing cleanup**: remove the deleted product from Anna Nagar's listing
   automatically, or leave it?
7. **Commit and push**: needs the owner's go-ahead. Pushing may trigger a deploy
   through `render.yaml`.
8. **Unlogged clicks**: add a browser-side logger for clicks that send no request?

## 8. Mistakes to avoid (from this project's history)

- Running migrations without a fresh backup (2026-11-17). Now back up first.
- Assuming a status value exists. Supplier status is only `approved` or
  `suspended`. Filtering on `active` hid every product from every customer.
- Storing kilograms as `kg` in the form while the filters expected it, so no
  product matched "Kilograms" (2026-11-18). Now handled by the 50 g rule.
- Two scripts on one page. The shared `static/js/datatable.js` and
  `base_dashboard.html` handled the bulk buttons. Pages that run their own bulk
  script must set `window._bulkScriptOwner = true`, and their buttons must not use
  `data-confirm`. Use `data-bulk-confirm`.
- Nested forms. Use the HTML `form` attribute instead.
- Expectations in tests that didn't match the factory data (label "500 g", not
  "500g"). Check the fixture before changing code.
- Fabricated output. Verify each claim with a command or a test. Say "not verified"
  when a browser check was not done.
- Storing passwords in notes or reports. Never write a password into the KB.

## 9. Where to look

| Need | File |
|---|---|
| Bundle rules and settings | `kb/10-bundle-supply-chain.md` |
| Full decision log (owner's words) | `kb/11-requirements-decision-log.md` |
| Testing, backups, logs | `kb/06-testing-and-ops.md` |
| Features (bulk actions, logs) | `kb/05-features.md` |
| Supply chain and naming | `kb/02-domain-model-and-roles.md` |
| Pricing and stock flow | `kb/04-pricing-and-stock-flow.md` |
| Shipment routing | `kb/07-supplier-shipment-routing.md` |
| Suspension and account lifecycle | `kb/08`, `kb/09` |
| Engineering history | `../PROJECT_NOTES.md` |

## 7. Pending at the end of this session (2026-11-18)
- **Apply migrations** products 0039, orders 0016 and stockist 0009 to db.sqlite3. Back up first and ask the owner. Until then the stock page, stockist pages, checkout and the stock sheet can fail on the dev database.
- **Distributor to shop blocking** (article 13, section 4). Ask the owner before building.
- **Customer quantity increase** should take stock (article 13, section 3).
- **Loose request blocking** for distributor to stockist requests.
- The 4 old test failures (bulk source, pack rules) are unchanged.
## 8. Update 2026-11-20
- Migrations applied to db.sqlite3 (see article 13 section 6). Backup kept.
- No-buffer: distributor restock form hidden and refused when ON (article 10 and 13).
- Customer quantity increase refused (article 13 rule 5).
- Still open: article 13 section 5 (checkout nearest-first, distributor-to-shop blocking, badge on free stock, confirmation of blocking for testing).
- Test status: stockist, distributors, orders all pass except 3 old stockist errors; suppliers and products pass except 4 old errors (bulk source, pack rules).
## 9. Update 2026-11-20 (later)
- Render deploy runs migrate automatically (render.yaml buildCommand). Backup the Render database before deploying if it has live data.
- Not pushed to GitHub yet. Commit and push after owner confirms.
- Open: nearest-first checkout (not built), distributor-to-shop blocking (not built), loose blocking (not built), 7-day hide of zero-stock products on the customer page (not built, needs a stock-zero date), bundle rule for nearest-first (owner to decide).
## 10. Update 2026-11-20 (build)
- Nearest-first checkout, distributor-to-shop blocking, customer hide (no Out of Stock badge), returns to the tier that gave the packs: built. Details in article 13 section 7.
- Open: loose blocking, automatic shop-request blocking, full checkout test. Git push: see the next step in the chat.

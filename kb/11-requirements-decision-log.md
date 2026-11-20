# 11 - Requirements and Decision Log

> Start with `12-handover-owner-context.md` for the rules, the current state and
> the open items. This file is the detailed history.

A running record of the owner's requests, answers and plan changes. Newest
entries are at the bottom. When a decision changes, the entry is marked
**PLAN CHANGE** and states what it replaces. `PROJECT_NOTES.md` remains the
engineering changelog. This file holds the owner's words and why each change
was made.

---

## 2026-11-17: Session 1 (supplier product filters)

**Request.** Add filters to the supplier product list (`/supplier/products/`)
for bundle, loose distribution, MRP and unit.

**Done.** Filters were added to `suppliers/views.py::product_list` and
`templates/suppliers/product_list.html`.

**Owner feedback.** The unit list lacked kilograms.

## 2026-11-17: Unit list

**Request.** "Kg is missing. Whatever I add under Units should appear in the
filter automatically."

**Done.** `Product.UNIT_CHOICES` now contains `g`, `kg`, `ml`, `l`, `qty`.
The filter reads this list, so it updates automatically.

> **PLAN CHANGE:** The first version treated `kg` as a stored unit. This
> conflicted with `master_stock_grams`, which holds base units. The owner's
> answer: keep grams as the stored value. Show kilograms as the main figure
> with grams in brackets. Keep `kg` and `g` as separate choices in the list.

## 2026-11-17: Bundle supply chain

**Request.** Bundle rules should apply down the chain: Supplier -> Stockist ->
Distributor -> Marketplace. Future roles must follow the same rules.

**Owner answers, in order:**

1. A stockist may break a bundle and sell singles to a distributor, even when
   the supplier is bundle-only. A stockist may also choose forward-only mode,
   which keeps no loose stock and passes the supplier's bundle settings
   through.
2. Settings are configured at three levels: global, supplier, and product.
   Each level can forward the supplier's method.
3. If the supplier sets delivery to the stockist only, the order stays with
   the stockist.
4. Bundle flags are set on each stockist and distributor's own page. Each
   distributor decides for itself.
5. Existing orders and stock: delete existing orders (hard delete) because
   this is a test environment. Apply the new rules to new orders.

See article 10 for the resulting rules.

## 2026-11-17: Database reset and bulk test orders

**Request.** Delete older orders and create new bulk test orders.

**Before the delete.** The settings label `db.sqlite3` as `PROD` by default.
The owner called it a test environment. I asked for confirmation and the
owner confirmed.

**Done.**

- Backup: `db.sqlite3.bak-before-order-reset-20261117-123738`.
- Hard-deleted 18 orders, 39 order items, 13 supplier shipments and 14
  payments.
- Created 4 bulk COD test orders with `scripts/bulk_test_orders.py`:
  - Customer 9100000020 at Anna Nagar Organics: Premium Almonds 1 kg x10,
    Cashews 1 kg x5.
  - Customer 9100000033 at Anna Nagar Organics: Cashews 1 kg x25.
  - Customer 9100000034 at Anna Nagar Organics: Organic Ghee 1 litre x5.
  - Customer 9711010200 at North Chennai Daily Needs: Premium Almonds 1 kg
    x12.
- Test orders do not deduct stock. This matches the existing seed scripts.

## 2026-11-17: Migrations and backups

**Earlier problem.** Migrations `0035` and `0036` ran on `db.sqlite3` without
a fresh backup.

**Owner answer.** Take a backup at my discretion.

**Done.** A backup was taken before the order delete (see above).

## 2026-11-17: Bottom-up order routing (answer to routing questions)

**Owner answer (paraphrased from a long reply, interpretation to confirm):**

- When no tier above the customer has bundle enabled, the order is offered
  from the bottom of the chain upward. Each tier offers single or bundle
  depending on the stock it holds.
- Example: the supplier sells only in bundles. A bundle of 10 units sits at
  the marketplace. A customer orders one unit. The marketplace may fulfil as a
  bundle (it holds one) or as a single unit. If it holds no stock, the request
  moves to the distributor, which may order a bundle or a single.
- If the marketplace ordered a bundle, the distributor orders a bundle too,
  and the bundle flows upward to the stock holder.
- If the marketplace ordered only singles, the request moves to the distributor
  again. If the distributor wants bundles, the request goes to stock. If stock
  has only bundles, the bundle is the only option.
- If a tier has no loose stock and loose orders are refused, that tier breaks
  or combines units itself. The owner wrote that loose units are "made into a
  bundle automatically". The exact rule is still open.

> **PLAN CHANGE:** Earlier, a stockist with no bundle flag was to pass bundle
> orders to the supplier (see the bundle-chain entry above). The owner now
> says the search runs bottom-up through each tier's own stock, and the supplier
> is the last stop. Article 10 is updated to match.

**Third round (owner):**

- Question 3 example (Almonds, bundle of 10, distributor with no buffer): the
  owner confirmed the example as correct.
- Question 6: the owner confirmed "make a bundle" means a numeric count. Loose
  units can also be counted as 10 and treated as a bundle. No packing step is
  involved.
- Question 7 resolved. Owner: "bundle is nothing but matching the count of
  loose with the bundle count. Do not confuse bundle and loose for counts."
  So a bundle order is filled from any tier whose count covers it, whether
  the tier thinks of that stock as loose or as bundles. Neither option (a)
  nor (b) from my question applied.

**Open questions raised with the owner:**

1. Does "bottom" mean the marketplace (shop), with the search moving up
   through distributor, stockist and supplier?
2. When a tier holds both loose units and whole bundles, which one is used
   first?
3. "Automatically make a bundle": does this mean a tier with loose units
   packs them into a bundle, or only that an order is converted to a bundle
   quantity?

## 2026-11-17: Routing answers (second round)

**Owner answers:**

- Each tier decides singles or bundle for its own orders. A tier whose
  upstream is bundle-only must order bundles only.
- A bundle-only tier sets this in its own login. Stockists have no singles
  option at all. A stockist that does not want loose stock enables bundle-only
  there.
- Customers always buy singles. The bundle rule applies only between businesses.
- At the marketplace, the buyer chooses single or bundle for each new order
  when both are allowed.
- Distributor "no buffer stock" global toggle: the distributor holds no stock,
  so marketplace orders and restock pass straight to the stockist. The options
  shown to the marketplace come from the stockist.
- When a bundle is opened, the tier gives one unit to the customer and keeps the
  remainder as its own loose stock.
- Each tier that holds stock decides manually per order, unless a tier above it
  is bundle-only.

> **PLAN CHANGE:** Bundle orders from a stockist without the bundle flag no
> longer go straight to the supplier. The order walks upward through each
> tier's own stock. The supplier is the last stop, reached only when no tier
> below it holds stock.

## 2026-11-17: Bundle chain built

**Owner instruction.** "For kilogram we will discuss later. Complete the
remaining."

> **PLAN CHANGE:** The kilogram display fix (kilograms first, grams in
> brackets) is **deferred** at the owner's request. Until it is done, products
> whose stock unit is set to `kg` may show wrong stock figures.

**Done.** See article 10, "How it works now".

- Bundle settings pages for stockist and distributor: global, supplier and
  product levels.
- Distributor **No buffer stock** switch.
- Shop restock routing by buffer setting, with fill and forward actions at the
  distributor.
- Bundle step enforced on every restock path, including auto-restock.
- Backups: `db.sqlite3.bak-before-bundle-chain-20261117-161928` and
  `db.sqlite3.bak-before-bundle-migrate-20261117-163153`.
- Migrations: `distributors.0043_wholesaler_no_buffer_stock`,
  `products.0038_tierbundlerule...`.

> **PLAN CHANGE (existing behaviour):** Before this change, a shop's *pack*
> restock request always went straight to the stockist and skipped the
> distributor. Only *loose* requests waited at the distributor. Following
> the owner's rule ("if the marketplace has no stock, the request moves up to
> the distributor"), pack requests now also wait at the distributor unless it
> turns on **No buffer stock**. To get the old behaviour back for one
> distributor, turn the switch on.

**Tests.** The 20 new tests pass. The full suite has 156 tests. 11 tests were
already failing before this work, and the same 11 still fail; none are new.
They come from earlier uncommitted changes, for example tests that still use
the removed field `supplier_supplies_loose`.

## 2026-11-17: No buffer switch covers bundle and loose; portability

**Owner.** "The stock switch on the Distributor is common for both bundle and
loose. Keep the KB updated, and it should work if I use this project
somewhere else."

**Done.**

- Confirmed that one switch already drives both paths through
  `routes_via_wholesaler()`. Added two loose tests (22 total, all pass).
- The page text and messages now say "pack and loose".
- `makemigrations --check` reports no missing migrations.
- Article 10 now has "Using this project on another machine", which lists the
  setup steps and the files that must be committed.

**Open.** The new files, and several older migrations they depend on, are
uncommitted. On another machine the feature will work only after they are
committed and pushed. This needs the owner's go-ahead.

## 2026-11-17: Bug fix, every storefront was empty

**Owner.** "No products for customer 9100000033. Why?"

**Cause.** This was my own bug from the earlier suspended-supplier work. In
`products/views.py::_product_qs`, the filter used
`supplier__status='active'`. `Supplier.status` is only ever `approved` or
`suspended`, so the filter hid every product from every customer of every
shop.

**Fix.** Show products of any supplier that is not suspended. Products of a
suspended supplier still show only when the shop has a price row for them
(downstream stock). Regression tests are in
`products/test_storefront_supplier_status.py`.

**Result.** Customer 9100000033 (Anna Nagar Organics) now sees 5 products.
The full suite has 162 tests. Two older failures are now fixed, both
empty-storefront tests. 9 older failures remain, none of them new.

**Note on GREE-NUTS-001 (Cashews, Green Farms Organics).** No shop lists it,
so no customer can buy it until a shop adds it and sets prices.

## 2026-11-17: Bulk demo products

**Owner.** "Bring some bulk products and I will check how this works."

**Done.** `scripts/bundle_demo_setup.py` created three products from Green
Farms Organics, listed and priced at Anna Nagar Organics:

- Rice (bundle 10, supplier bundle-only)
- Sugar (bundle 25, stockist bundle-only)
- Oil (bundle 12, distributor bundle-only)

Backup: `db.sqlite3.bak-before-bundle-demo-20261117-173355`. Article 10
"Demo data for manual testing" lists the test steps.

**Correction.** Login 9100000020 is the Anna Nagar Organics shop account, not
a customer. The earlier bulk test order filed under 9100000020 was placed as
that account.

## 2026-11-17: Customer singles simulation, two bugs fixed

**Owner.** "Do many bulk transactions from customers as singles, test how it
works, and fix any issues yourself."

**Method.** `scripts/bulk_singles_sim.py` ran 12 real customer checkouts
through the cart and checkout views, for customer 9100000033 on the demo
products. After each order it checked stock, totals and item quantities.
Backups: `db.sqlite3.bak-before-singles-sim-20261117-174003`.

**Bug 1 (fixed): a shop sale also reduced the supplier's stock.** Checkout
deducted the supplier variant's stock for every cart line, including lines
filled from the shop's own stock. The supplier's stock had already left the
supplier when it was delivered down the chain, so each shop sale was counted
twice. In the first run, Rice supplier stock was 484 where 489 was correct.
Fix in `orders/views.py`: lines filled from shop stock skip the supplier
stock check and deduction. Supplier-sourced lines are unchanged. Regression
tests: `orders/test_shop_stock_checkout.py` (2 tests).

**Bug 2 (fixed in the demo data, not the code): demo prices below the chain
cost.** Customers never pay less than the chain cost (supplier price, stockist
charges and distributor margin). The demo sets prices below that, so checkout
charged more than the listed price. `scripts/bundle_demo_setup.py` now raises
each listed price to that floor. New prices: Rice 105, Sugar 90, Oil 199.

**Behaviour to know (not changed).** An order is filled from shop stock only
when the shop holds enough of every line. If one line is short, the whole
order goes to the supplier. Partial shipment is off for this shop. Turning
`allow_partial_shipment` on splits the order.

**Results.** 12 orders, all checks pass, and supplier stock now drops only
for supplier-sourced lines. The full suite has 164 tests. The same 9 failures
from before this session remain, and no new failures.

## 2026-11-17: Multi-select bulk actions on admin Customers page

**Owner.** "In this page allow me to do multi select, this will help to do
multi action like bulk delete, enable etc. Needed in all buttons on this page."
Page: `/admin-panel/customers/`.

**Done.**

- Checkbox per row (desktop and mobile), plus select-all in the toolbar and in
  the table header. The count shows unique customers.
- Active tab buttons: **Enable**, **Disable**, **Delete** (soft delete), and
  **Assign marketplace** (choose a marketplace, then Assign).
- Deleted tab buttons: **Restore** and **Delete permanently**.
- Every bulk button asks for confirmation, and only applies to customers in
  the matching state (for example, Enable skips deleted customers).
- Endpoint: `POST /admin-panel/customers/bulk/` (`customer_bulk_action`). It
  only changes role=customer accounts. The main admin and superusers are never
  changed. Duplicate ids count once.
- Row-level buttons (View, Assign inline, Delete, Restore, Purge) are unchanged.

**Tests.** `admin_panel/test_customer_bulk.py`, 12 tests, all pass.

**Not verified:** the page script was not run in a browser, because Node.js is
not installed here. Click through the buttons once to confirm.

**Follow-up (owner).** "While assigning to any marketplace, prompt to confirm."
The per-row "Not Assigned" dropdowns submitted as soon as a marketplace was
picked, so no prompt appeared. Now every row dropdown asks first. Cancel puts
the dropdown back to "Not Assigned". The bulk Assign button also asks first.
Test: `test_inline_marketplace_dropdowns_confirm_before_submit`.

## 2026-11-17: Show the chain above each marketplace (admin Customers page)

**Owner.** "List who is the distributor above this marketplace and the stockist
name too, so I get an idea before I move it."

**Done.**

- Each customer row shows its marketplace's **Distributor** and **Stockist**
  (desktop and mobile).
- Each marketplace option in the assign dropdowns carries the same chain.
- The assign prompt shows the chain of the target marketplace. The bulk prompt
  also says how many selected customers already have a marketplace and will be
  moved.
- `Distributor.chain_label`, `upstream_wholesaler_name`,
  `upstream_stockist_name` are properties (no migration).
- **Owner (follow-up):** do not show "none" for a missing link. The Distributor
  and Stockist lines are hidden when there is nothing to show. The suspended
  marketplaces on the live page have no distributor linked, so their rows show
  only the status badge. The confirm prompt also leaves out missing lines.
- **Owner (follow-up):** the confirm prompt should show the question on line 1,
  the distributor on line 2, and the stockist on line 3. Done with separate
  lines in `confirmLines()`. Each marketplace option carries `data-distributor`
  and `data-stockist`.

**Tests.** `admin_panel/test_customer_bulk.py`: 15 tests, all pass.

## 2026-11-18: Bulk Delete did nothing; logging on every action

**Owner.** "In this page I tried to delete by select all but it did not delete.
Check the log, or enable the log, and I will do it again and check the log.
Log every action in this project everywhere."

**Cause (found in Edge, reproduced with Playwright).** Three scripts fought over
the same buttons:

1. `static/js/datatable.js` (loaded by every page) also manages `#selectAll`,
   the header checkbox and the count. It counts each row checkbox, so each
   customer counted twice ("10 selected" for 5 customers). Its own confirm then
   cancelled the submit. The fix that user_list uses is
   `window._bulkScriptOwner = true`, which tells datatable.js to stay out.
2. `templates/base_dashboard.html` runs a global handler for any element with
   `data-confirm`. The bulk buttons had that attribute, so a second prompt
   appeared. They now use `data-bulk-confirm`.
3. No POST reached the server, so the request log had no bulk entry. This is why
   the log showed nothing.

**Fix.** The Customers page sets the owner flag, and bulk buttons use
`data-bulk-confirm`. The browser test now shows "5 selected", one prompt that
ends "(5 selected)", and one POST with `action=delete` and the 5 ids. The test
blocked the POST, so no customers were deleted.

**Logging added.**

- `logs/requests_<start time>.log`: every request, now with the posted fields.
  IDs, `action`, `status` and similar short fields show their values. Free text
  (address, notes) shows a count only. Passwords, tokens and CSRF values show as
  `***`. Example line for login:
  `... POST 302 ... /accounts/login/password/ | posted: csrfmiddlewaretoken=***; password=***`.
- `logs/activity.log`: each bulk request writes `CUSTOMER_BULK | RECEIVED`
  (with ids and actor) and `CUSTOMER_BULK | DONE` (applied and skipped counts).
  Empty selections write `NO_SELECTION`.
- Tests write to the same log files. Entries from `admin@test.com` are from
  the test suite.

**Not logged.** A click that never sends a request (the case above) reaches no
server log. Logging such clicks would need a browser-side logger.

**Tests.** `admin_panel/test_customer_bulk.py`: 19 tests, all pass.

## 2026-11-18: Variant counts on admin lists

**Owner.** "Why does GREE-NUTS-001 show 12 variants on the Products page? Fix it."

**Cause.** The Variants column counted every pack row, including inactive ones.
Cashews (GREE-NUTS-001) has 3 variant types (W450, W320, W180) and 12 pack rows.
W180 has no active packs.

**Fix.** The column now shows live variant types and live packs:
`2 variants · 8 packs` for GREE-NUTS-001. A variant type counts only if it has
at least one active pack. Implemented with annotations in
`admin_panel/views.py::product_list`. Test: `admin_panel/test_product_variant_count.py`.

**Distributors page.** The customer count excludes the shop's own login (role
`distributor`), which is already shown in the Username column. Anna Nagar
Organics now shows 4. Test: `admin_panel/test_distributor_list_counts.py`.

## 2026-11-18: Supplier product filters: Stock unit and Pack size

**Owner.** "Why does GREE-NUTS-001 show under the grams filter on
`/supplier/products/`?" Owner's choice: "do as per your best suggestion."

**Cause.** The filter matched the product's stock unit (`master_unit`, default
`g`), not its pack sizes. GREE-NUTS-001 is kept in grams and has 100 g, 250 g,
500 g and 1 kg packs.

**Done.**

- The filter is labelled **Stock unit**.
- A new **Pack size** filter lists each pack size the supplier has in active
  packs. It shows products with an active pack of that size.
- The pack size option uses the supplier's own label for that size.
- Test: `suppliers/test_pack_filter.py` (4 tests, all pass).

**Follow-up (owner, 2026-11-18).** With Stock unit = Litres, the Pack size list
still showed 100 g, 250 g, 500 g and 1 kg, and GREE-NUTS-001 still appeared.
Fixed:

- The pack size list follows the Stock unit filter. With Litres selected, only
  litre pack sizes are listed.
- Each pack size is keyed by its stock unit and base units (`g:1000`,
  `ml:1000`). A 1000 ml pack is no longer matched by "1 kg".
- Each size appears once, with the label set on the product page.
- Tests: 7 in `suppliers/test_pack_filter.py`, all pass.

## 2026-11-18: Kilogram filter follows pack sizes (50 g rule)

**Owner.** "I need this in kilograms, which starts from 50 g packs. If a product
has a 1 g or 100 g pack it stays under grams. Anything starting from 50 g is
always kilogram."

**Rule (implemented in `products/stock_unit.py`).** A product stored in grams is
in the **kg** filter group when every active pack is 50 g or more. It is in the
**g** group when any active pack is under 50 g, or when it has no active packs.
Millilitre products are unchanged, because the owner only asked about kilograms.

**Stored data is not changed.** `master_unit` and `master_stock_grams` stay as
they are. Only the Stock unit and Pack size filters use the rule.

**Checked on live data.** Almonds New (1 kg, 2 kg) and Cashews (100 g and up) are
in the kg group. Organic Ghee (500 g and 1 kg) is in the kg group.

**Tests.** `suppliers/test_pack_filter.py`: 10 tests, all pass.

## Standing rules for future changes

- Back up `db.sqlite3` before every migration or delete.
- Show the owner the target database name before any destructive step.
- Record every owner request and answer here, and mark any change of plan as
  **PLAN CHANGE**.

## 2026-11-18: Stock blocking, customer-only cancel, and return to source (PLAN CHANGE)
**Owner, in order of the discussion:**
- Stock shown to a tier is its free stock: stock minus blocked. A request from a lower tier blocks the packs at once. The upper tier keeps the packs (custody) until it ships. Shipping lowers stock and releases the block. Rejecting releases the block. (PLAN CHANGE: the earlier plan lowered stock only at dispatch and did not block.)
- Zero free stock is hidden from ordering and restock lists everywhere. Stockist charges and bundle rules are never erased. The pack reappears when stock comes back.
- Cancel and return: only the customer can cancel. A cancelled or reduced item returns to whoever gave it to the customer. Shop items return to the shop. Supplier items return to the supplier. No stage-level return option. (PLAN CHANGE: the distributor cancel button is removed.)
- Stockist to distributor delivery: packs leave the stockist's own stock. Delivery is refused if the stockist is short. Earlier the stock was added to the distributor but not taken from the stockist.
- Distributor requests to the stockist should also block, and the Blocked count should show in the stock sheet. The owner asked that the block apply at every tier that holds stock.
- Owner asked: every decision goes in this log and the knowledge base.
**Built:** see article 13 for status by rule. Migrations are generated but not yet applied (owner approval needed).
**Open:** customer quantity increase does not take stock; shop requests to distributor not blocked yet; admin cancel options not checked.
## 2026-11-18 (later): Blocking at every tier, Blocked column, customer-only cancel confirmed
**Owner.** "Block count should work everywhere where stock is held." Distributor requests to the stockist should also block. The Blocked count should show in the stock sheet. "Do wherever I told" for cancel: admin pages were checked and no cancel action was found; the distributor cancel button was removed.
**Owner questions answered in this session.** Stockist to distributor delivery takes the packs out of the stockist's stock and is refused when the stockist is short (so a delivery is never promised twice). Customer quantity increase does not take stock (open gap, listed in article 13). Distributor requests to the stockist are now blocked at request time.
**Built:** distributor to stockist blocking (stockist/supply_requests.py::create_wholesaler_request), Blocked count in the stock sheet and on the stock page, manual reduce cannot go below blocked.
**Open:** distributor to shop blocking; loose (grams) request blocking; customer quantity increase; migrations not applied to the dev database (owner approval and backup needed).
**Article 13** holds the rules and their status. Check it before every stock change.
## 2026-11-20: Migrations applied, no-buffer hides restock, customer cannot increase quantity
**Owner.** Apply the migrations to the production database. Done: db.sqlite3, backup db.sqlite3.bak-before-blocking-20261120-172021.
**Owner.** When the distributor's no-buffer switch is ON, the distributor keeps no stock and has no restock option. Built: the restock form and auto-restock section are hidden, and the server refuses a restock action in that state.
**Owner.** Customer quantity increase: refuse it, and let the customer place a new order for extra packs. (PLAN CHANGE: earlier plan was to take the extra stock.) Built.
**Owner.** Blocked count in Excel: good.
**Owner.** Admin cancel: "cancel" means the action that sets an order to Cancelled. The distributor's Cancel Order button was removed. No admin cancel action was found.
**Owner.** Points still to decide, all recorded in article 13 section 5: block at every tier (still testing); nearest-first checkout; distributor to shop blocking; loose blocking (owner: yes); Out of Stock badge on free stock.
**Explained to owner.** Each pack is in exactly one tier's count, so counts can be added. A tier orders up to the free stock of the tier above. Checkout currently skips the stockist and distributor, which is a gap against the owner's rule.
**Open:** decisions in article 13 section 5.
## 2026-11-20 (later): Git, Render, nearest-first clarified, 7-day customer hide
**Owner.** Only this workspace has the project. GitHub has an old copy. Asked how to push. Steps given: review git status, commit, push to origin main. db.sqlite3 and .env are git-ignored and are not pushed. Not pushed yet: owner must confirm.
**Owner.** Distributor-to-shop blocking means: when a shop order comes to its distributor, the distributor's stock is held at once, so no other shop can take it. Not built yet.
**Owner.** Stockist stock should be used before supplier stock, because the stockist is the nearer tier. Owner is right. Checkout currently skips the stockist. Clarified: nearest-first decides which stock is held for a customer order. The request still moves one level up at a time, and each level confirms before packs move. Those two rules do not conflict.
**Owner.** Customer page: no Out of Stock badge. A product with no stock for 7 days is hidden from the customer page. Not built. Needs a record of when stock became zero, which does not exist yet.
**Owner.** Bundles: the owner wants to decide how bundle orders follow the nearest-first rule. Open.
**Render.** ender.yaml build command runs python manage.py migrate on every deploy, so the new migrations run on deploy. Owner should take a backup of the Render database first if it holds live data.
## 2026-11-20 (build): owner decisions carried out
**Owner.** Push to Git as I think right. Nearest-first: my decision. Distributor-to-shop blocking: build. 7-day hide: build; immediate hide allowed. Bundles: if not available at the nearer level, go to the next level. Build missing things. Render/prod: update only the production database.
**Built:** see article 13 section 7. Migrations applied to db.sqlite3 with a backup.
**Still open:** loose blocking, automatic shop-request blocking, full checkout test.
## 2026-11-20 (loose, logging): owner decisions carried out
**Owner.** Loose (weighed) stock must block too. Built. Automatic shop requests: explained and now block. Full order test: owner will place orders from each role. Activity log for every role on orders: added where missing.
**Owner.** "If the shop cannot cover the whole order, only no stock, then request from the upper level?" Answer: yes, the trigger is no free stock at the shop. The build takes packs straight from the upper tier, which skips the request-and-confirm step. Correction recorded in article 13 section 8.
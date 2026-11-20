# 10 - Bundle Supply Chain

Status: **implemented 2026-11-17** for restock between businesses (stockist,
distributor, shop). Customer checkout is unchanged: customers always buy
singles. See "How it works now" and "Where the code lives" below.

## Why this is being built

Until now, bundles existed only on the supplier side:

- `Product.allow_bundle` lets a supplier offer bundles.
- `ProductBundle` defines a bundle (for example, 25 x 1 kg packs).
- `ProductVariant.only_bundle` makes a pack orderable only in bundle quantity.

Stockists and distributors have no bundle setting, and orders are not routed by
bundle rules. Real trade works in bulk. A supplier packs goods in cases, and
the stockist and distributor buy the same cases. Each tier should decide
whether it buys and sells whole bundles or loose units, and orders should
follow those decisions up the chain.

## The chain

Supplier -> Stockist -> Distributor -> Marketplace (shop) -> Customer

The chain is stored as an ordered list of tiers, not hard-coded. A new role
added between two existing tiers must receive the same bundle behaviour
without code changes to each rule.

## Rules agreed with the owner

1. **Stockist may break a bundle.** Even when the supplier marks a product
   bundle-only, a stockist can open a bundle and sell singles to a distributor.
2. **Forward-only mode (stockist).** A stockist can choose to keep no loose
   stock. In this mode it takes the supplier's bundle settings and passes
   whole bundles straight through, without holding loose units.
3. **Settings levels for a stockist (and for a distributor).** Each tier has
   a global button and can override it at each level:
   - a global setting that applies to all suppliers' products,
   - a supplier-level setting, which can forward the supplier's setting,
   - a product-level setting, which can also forward the supplier's method.
4. **Each tier sets its own flag.** Stockist and distributor flags live on
   their own pages. Each distributor decides for itself whether to mark a
   product as bundle-only. Settings are not inherited by default.
5. **Routing by delivery target.** If the supplier sets delivery to the
   stockist only, the order stays with the stockist. Bundle changes do not
   override that.
6. **Bottom-up search when no tier enables bundles.** Search starts at the
   marketplace and moves upward through distributor, stockist and supplier.
   At each tier, the order is fulfilled as a single or a bundle, based on the
   stock that tier holds. A tier with bundle-only stock can offer only bundles.
   (Replaces the earlier wording "goes to the supplier". See article 11.)
   **Resolved (question 7):** Bundle and loose are the same stock counted as
   one number. See "Bundle is a count" below.

## Bundle is a count (owner's final rule)

A bundle is not a separate kind of stock. It is the bundle size matched
against the loose count. A tier holding 30 loose units of a product with
bundle size 10 holds 3 bundles. Bundle and loose are never treated as
different stock.

Consequences:

- A bundle order of 10 is filled from any tier whose count is 10 or more.
- A tier is never refused a bundle order because it "holds only loose units".
- The order moves up to the next tier only when the tier's count is below the
  quantity ordered.
- Bundle-only (set by the supplier or by a tier's own login) restricts what
  the buyer below can ask for: whole multiples of the bundle size only.
- Example: the stockist holds 30 loose units, bundle size 10. A shop orders one
  bundle (10). The stockist fills it from its 30 and keeps 20.

## Confirmed in the second round

- **Bundle means a count.** A bundle is a number of units recorded as one
  quantity, for example 10. It is not a packing step. The tier holds the
  count and does not pack loose units. The count can be 10 loose units, and it
  is still treated as a bundle.
- **Example (no buffer at distributor):** Shop orders 1 kg. The distributor has
  no buffer, so the request goes to the stockist. The stockist, not bundle-only,
  opens one bundle of 10 kg. It sends 1 kg through the distributor to the shop,
  and 9 kg stays as stockist loose stock. Confirmed by the owner.

## How it works now

**Settings (each tier on its own page).**

- Stockist: sidebar, **Bundle Settings** (`/stockist/bundle-settings/`).
- Distributor: **My Stock**, **Bundle Settings** (`/wholesale/bundle-settings/`),
  and the **No buffer stock** switch on My Stock.
- Each page has a global setting, supplier overrides and product overrides.
  The most specific wins: product, then supplier, then global. With nothing
  set, the mode is **Singles allowed**.
- Modes: **Singles allowed** (buyers order any count; the tier breaks
  bundles), **Bundle only** (buyers order whole bundles), **Follow upstream**
  (use the tier above's setting; for a stockist that is the supplier's
  bundle-only flag).

**Order step.** A buyer's restock quantity must be a whole multiple of the
bundle size when the tier that actually sells to it is bundle-only. The bundle
size is the smallest active `ProductBundle.pack_quantity` for that pack
(1 when none is defined). Buying from the supplier keeps the existing rule,
`ProductVariant.supplier_order_multiple`.

**Routing of a shop's restock request.**

| Distributor setting | Where the request goes | Whose bundle rule applies to the shop |
|---|---|---|
| Buffer kept (default) | Waits at the Distributor (`awaiting_stock`) | The Distributor's |
| No buffer stock | Straight to the stockist (`pending`) | The stockist's |

At the Distributor, a waiting pack request shows under **Pack requests from
your Marketplaces** with two actions:

- **Fill from my stock**: moves the units from the Distributor's stock to the
  shop. Refused when the Distributor holds fewer units than requested.
- **Request from stockist**: files one request to the stockist. The quantity
  is rounded up to the stockist's bundle step. Extra units stay in the
  Distributor's stock. Then the Distributor fills the shop request.

**The No buffer stock switch is common to pack (bundle) and loose
requests.** Owner's rule, 2026-11-17. One switch decides both:

- OFF: pack and loose shop requests both wait at the Distributor.
- ON: pack and loose shop requests both go straight to the stockist. The
  stockist sees them on its Marketplace Requests page and delivers them.

There is no separate switch for loose. Both paths call
`routes_via_wholesaler()` in `products/bundle_chain.py`, so they cannot drift
apart. Tests: `test_loose_request_waits_at_distributor_with_buffer` and
`test_loose_request_goes_to_stockist_without_buffer`.

Direct-supply products skip the chain as before. Auto-restock uses the same
step and routing as manual requests.

**Worked example.** Bundle of 10. The stockist is bundle-only. The
Distributor keeps a buffer, set to singles. A shop orders 3. The request waits
at the Distributor. It holds none, so it forwards: the stockist request is 10.
On delivery, the Distributor fills the shop's 3 and keeps 7.

## Open points

- Stock units (kilograms vs grams display): **deferred by the owner on
  2026-11-17.** See article 11.
- The stockist's Marketplace Requests page can still list `awaiting_stock`
  requests if the stockist picks that status filter by hand. It cannot act on
  them.

## Customer checkout and stock sources

- A customer always buys singles. Bundle steps never apply at checkout.
- Checkout fills a cart from the shop's own stock when the shop holds enough
  of every line. Otherwise the whole order goes to the supplier (partial
  shipment is off by default).
- A line filled from shop stock does not change the supplier's stock. That
  stock already left the supplier when it was delivered down the chain.
- Customers never pay below the chain cost (supplier price, stockist charges
  and distributor margin). A shop price below that floor is raised to it.
- Simulation: `scripts/bulk_singles_sim.py` runs real checkouts and checks
  the stock invariants. Regression tests: `orders/test_shop_stock_checkout.py`.

## Demo data for manual testing

Script: `scripts/bundle_demo_setup.py`. It is safe to re-run, and each run
resets the demo stock and rules. Chain used: Green Farms Organics (supplier)
-> Stockist -> Coimbatore Wholesale (Distributor) -> Anna Nagar Organics
(shop) -> customer 9100000033.

| SKU | Product | Bundle | Rule | Order step: stockist / distributor / shop |
|---|---|---|---|---|
| GREE-STXX-001 | Demo Bulk Rice 1 kg | 10 | Supplier bundle-only, stockist singles | 10 / 1 / 1 |
| GREE-STXX-002 | Demo Bulk Sugar 1 kg | 25 | Stockist bundle-only | 1 / 25 / 1 |
| GREE-STXX-003 | Demo Bulk Oil 1 L | 12 | Distributor bundle-only | 1 / 1 / 12 |

Starting stock: supplier 500, stockist 30 / 50 / 24, distributor 0, shop 5.

Things to try:

1. Customer 9100000033 buys 1 of each. Customers always buy singles.
2. Shop (login 9100000020), My Stock: request 3 Oil. It is refused (steps of
   12). Request 12, and it waits at the Distributor.
3. Shop requests 3 Sugar. It waits at the Distributor. Distributor (login
   9710000004), My Stock, **Request from stockist**: the stockist request is
   25. After delivery, fill the shop's 3; the Distributor keeps 22.
4. Shop requests 3 Rice. Forwarding gives a stockist request of 3 (stockist
   sells singles). Stockist (login 9000000004), Restock Requests: ordering
   Rice from the supplier must be in steps of 10.
5. Distributor turns **No buffer stock** ON. Shop requests (pack and loose)
   now go straight to the stockist. Oil is no longer bound to steps of 12,
   because the Distributor's rule no longer applies.

## Using this project on another machine

The feature lives entirely in code and migrations. Nothing is stored only in
the local database. `db.sqlite3` is git-ignored, so each machine builds its
own database.

1. Get the code. The new files must be committed and pushed first (see below).
2. Create and activate a virtual environment, then
   `pip install -r requirements.txt`.
3. Run `python manage.py migrate`. This creates the bundle tables
   (`products.0038`) and the No buffer stock column (`distributors.0043`).
   Defaults: no bundle rules (everything **Singles allowed**) and No buffer
   stock **OFF**.
4. Optional test orders: `python manage.py shell -c "exec(open('scripts/bulk_test_orders.py').read())"`.
   The script uses fixed customer usernames and variant IDs from this
   machine's data, so on a new database edit `BULK_PLAN` first.
5. Check: `python manage.py test products.test_bundle_chain` (22 tests).

Files that must be in the repository for the feature to work:

- `products/bundle_chain.py`, `products/test_bundle_chain.py`
- `products/migrations/0031` through `0038`,
  `distributors/migrations/0042` and `0043`,
  `stockist/migrations/0008`, `suppliers/migrations/0009`.
  Several of these predate this feature but are still uncommitted, and later
  migrations depend on them.
- `templates/partials/bundle_settings.html`,
  `templates/partials/bundle_rule_rows.html`,
  `templates/stockist/bundle_settings.html`,
  `templates/distributors/wholesaler_bundle_settings.html`
- `kb/10-bundle-supply-chain.md`, `kb/11-requirements-decision-log.md`

Status on 2026-11-17: these files are **not yet committed**. Nothing has been
pushed.

## Where the code lives

- `products/bundle_chain.py`: the chain registry and every rule
  (`effective_seller`, `rule_mode`, `required_multiple`,
  `routes_via_wholesaler`), plus the shared settings-page handlers. **Add a new
  tier here.**
- `products/models.py`: `TierBundleRule` (one table for every tier's rules),
  `ProductBundle`, `ProductVariant.only_bundle`.
- `distributors/wholesaler_models.py`: `Wholesaler.no_buffer_stock`.
- `distributors/views.py` (`my_stock`): shop restock step and routing.
- `distributors/wholesaler_views.py`: `_handle_marketplace_packed`,
  `bundle_settings`, the buffer switch, the step on Distributor restock.
- `stockist/views.py`: `bundle_settings`; loose requests from no-buffer
  Distributors' shops now reach the stockist.
- `stockist/auto_restock.py`: same step and routing for automatic requests.
- Templates: `templates/partials/bundle_settings.html`,
  `templates/partials/bundle_rule_rows.html`,
  `templates/stockist/bundle_settings.html`,
  `templates/distributors/wholesaler_bundle_settings.html`,
  `templates/distributors/wholesaler_my_stock.html`.
- Tests: `products/test_bundle_chain.py` (22 tests).

## Update 2026-11-20
When no-buffer is ON the distributor has no stock and no restock option. Its shops' requests go straight to the stockist. The restock form and the auto-restock section are hidden, and a restock action is refused on the server (article 13 rule 9).
# 13 - Stock Availability, Blocking and Returns

**Source of truth for how stock is promised, held and returned.** Read this with
article 04 (pricing and stock flow), article 10 (no-buffer policy) and article 12
(handover). Owner decisions are logged in article 11. Keep this article current in
the same turn as any code change, even when the owner does not ask.

Last updated: 2026-11-20.

---

## 1. The chain and where each count lives

Packs move down the chain: **Supplier -> Stockist -> Distributor (`wholesaler`) ->
Shop / Marketplace (`distributor`) -> Customer**. Each tier has its own count of
packs. A pack is in exactly one count at a time, so counts can be added together
without double counting:

| Tier | Count | Changes when |
|---|---|---|
| Supplier | `ProductVariant.stock` | Supplier edits stock; checkout takes stock for supplier-filled orders; supplier ships to stockist |
| Stockist | `StockistInventory.quantity` | Supplier delivers in; stockist reduces manually; stockist delivers to distributor |
| Distributor | `WholesalerStock.quantity` | Stockist delivers in; distributor fills a shop request |
| Shop | `DistributorStock.quantity` | Shop receives a delivery; checkout fills a customer line |

## 2. The rules (owner-approved)

1. **A tier can order only up to the free stock of the tier above.** Free = stock
   minus blocked. The upper tier does not need to hold packs for the lower tier in
   advance.
2. **A request blocks the packs at once.** The upper tier keeps the packs until it
   ships or delivers. Shipping or delivering releases the block and lowers the count.
   Rejecting releases the block.
3. **Two requests cannot take the same free packs.** The second is refused, and the
   message says how many packs are free.
4. **Customer order.** A line is filled from the shop's own stock first. If the shop
   is short, the line is filled from supplier free stock. Stock is taken at checkout.
   A line more than the free stock is refused.
5. **Customer edits.** A customer may remove packs or reduce a quantity on a pending
   order. The removed packs return to the source (shop items to the shop, supplier
   items to the supplier). A customer **cannot increase** a quantity. A new order is
   needed for extra packs. (Decision 2026-11-20.)
6. **Only the customer can cancel.** No other role has a cancel action on orders.
7. **Zero free stock means hidden.** A pack with no free stock upstream is hidden from
   that tier's ordering and restock lists. Nothing is deleted. Charges, bundle rules
   and inventory rows stay in the database.
8. **Delivery takes stock out of the sender.** Delivery is refused when the sender
   does not have enough packs.
9. **No-buffer policy (distributor).** When a distributor's no-buffer switch is ON,
   it keeps no stock and has no restock option. Its shops' requests go straight to
   the stockist. See article 10.

## 3. Status (2026-11-20)

| Rule | Status | Where |
|---|---|---|
| 1, 2, 3 supplier to stockist | **Built.** | `products/models.py` (`blocked_stock`, `available_stock`), `stockist/supply_requests.py`, `suppliers/views.py` |
| 1, 2, 3 stockist to distributor | **Built for packs only.** Loose (grams) requests are NOT blocked yet. The owner asked for it, so it is the next build item. | `stockist/supply_requests.py::create_wholesaler_request`, `stockist/views.py`, `distributors/wholesaler_views.py`, `stockist/auto_restock.py` |
| 1, 2, 3 distributor to shop | **Not built.** Needs decision (section 5). | - |
| 4 checkout | **Built** for shop then supplier. **Not built**: nearest-first through distributor and stockist (section 5). | `orders/views.py` |
| 5 edits: no increase | **Built.** | `orders/views.py::order_edit` |
| 5 edits: return to source on remove or reduce | **Built.** | `orders/stock_return.py` |
| 6 only customer cancels | **Built.** Distributor's Cancel Order button removed. No admin cancel action found. | `distributors/views.py`, `templates/distributors/order_detail.html` |
| 7 hide zero free stock from stockist list | **Built** for the stockist supply list. | `stockist/views.py::inventory` |
| 8 delivery refused when short | **Built** for stockist to distributor. | `stockist/views.py` |
| 9 no-buffer hides distributor restock | **Built** (form, auto-restock section, and server refusal). | `templates/distributors/wholesaler_my_stock.html`, `distributors/wholesaler_views.py` |
| Stock page shows Blocked | **Built.** | `templates/suppliers/partials/stock_product_card.html` |
| Stock sheet shows Blocked (information) | **Built.** Column H. | `suppliers/stock_excel.py` |

## 4. Known gaps

- **Checkout skips tiers.** A customer line that the shop cannot fill is taken from
  supplier stock even when the stockist or distributor holds free packs. Under
  rule 1 the nearest free stock should be used first. Needs decision (section 5).
- **Badge uses total stock.** The supplier's "Out of Stock" badge and filter use
  total stock, not free stock. Packs fully blocked still show as in stock. Needs
  decision (section 5).
- **Old orders.** Orders placed before `stock_source` existed default to `supplier`.

## 5. Decisions needed from the owner

1. Is the 2026-11-18 blocking rule final for the test phase? (Owner: "still under testing".)
2. Customer quantity increase: decided as **refused** (section 2, rule 5).
3. Nearest-first rule for checkout: a customer line should use shop free stock, then
   distributor free stock, then stockist free stock, then supplier free stock.
   Returns then go to the tier that gave the packs, so the `stock_source` choices must
   include distributor and stockist. Confirm before building.
4. Distributor to shop blocking: same block idea as stockist to distributor. Build
   after decision 3.
5. Loose (grams) requests: owner said block them too. **Not built yet.** The loose path in `distributors/wholesaler_views.py::restock_loose_for_marketplace` still creates requests without a block. Next build item after the owner confirms the pool-level design.
6. "Out of Stock" should use free stock (`available_stock`), so fully blocked packs are
   shown as out of stock. Confirm before changing the stock page filter.

## 6. Migrations

Applied to `db.sqlite3` (the production database) on 2026-11-20, after a backup
`db.sqlite3.bak-before-blocking-20261120-172021`:

- `products` 0039 `productvariant_blocked_stock`
- `orders` 0016 `orderitem_stock_source`
- `stockist` 0009 `stockistinventory_blocked_quantity`

Any new migration needs a backup first and must be listed here and in article 12.

## 8. Customer page (owner rule, 2026-11-20)
- No Out of Stock badge on the customer page.
- A product with no stock for 7 days is hidden from the customer page. Not built. Needs a timestamp for when stock became zero.
## 7. Build update 2026-11-20 (owner approved: nearest-first, distributor-to-shop blocking, customer hide)

**Built and tested (orders/test_nearest_first.py, 11 tests):**
- **Nearest-first checkout.** A customer line is filled from the shop first (existing rule). If the shop is short, the nearest upper tier that can cover the WHOLE line supplies it: Distributor (wholesaler) first, then Stockist, then the supplier path. Code: orders/upstream_stock.py::pick_upper_tier, consume_upper, wired into orders/views.py checkout. A bundle line is a count of packs, so it follows the same rule. If the whole line cannot be covered by one upper tier, the existing supplier path applies (orders/views.py).
- **Returns go back to the tier that gave the packs.** OrderItem.stock_source can now be shop, wholesaler, stockist or supplier. orders/stock_return.py returns to each. Owner rule kept: customer-only cancel.
- **Distributor-to-shop blocking.** When a shop request waits at its Distributor (waiting_stock), the Distributor's free packs are blocked at once (WholesalerStock.blocked_quantity, flag RestockRequest.wholesaler_blocked). Released when the Distributor fills the request from stock, or when it forwards the request up to the Stockist. Code: distributors/shop_blocking.py, hooked into distributors/views.py (request creation) and distributors/wholesaler_views.py (fill and forward).
- **Customer page.** No "Out of Stock" badge. A product is hidden at once when nothing is free for it anywhere the shop can reach: the shop, the Distributor, the Stockist or the supplier (products/views.py::_orderable). The owner allowed immediate hiding, so no 7-day date is recorded.
- **No-buffer (article 10).** Distributor restock hidden and refused when ON.
- **Migrations applied to db.sqlite3** (backup db.sqlite3.bak-before-nearest-first-20261120-181024): distributors 0044, orders 0017. Render build also runs migrate.

**Still open (not built):**
- **Loose (grams) blocking.** Owner said block loose too. Not built: stockist-to-distributor loose requests (distributors/wholesaler_views.py::restock_loose_for_marketplace) and shop-to-distributor loose requests still create without a block.
- **Automatic shop requests.** stockist/auto_restock.py::check_marketplace creates shop requests without the distributor block.
- **Checkout integration test.** Helpers are tested. A full cart-to-order test is not written yet.
- **Bundle rule on the 7-day / loose paths.** Owner to confirm if bundle lines need anything beyond the whole-line rule.
## 8. Update 2026-11-20 (loose blocking, automatic requests, logging)
**Built and tested (stockist/test_loose_blocking.py, 5 tests):**
- **Loose (weighed) blocking, stockist to distributor.** Stockist loose stock has StockistLooseStock.blocked_grams. A distributor's loose request blocks grams at once (stockist/supply_requests.py::block_loose). Delivery or rejection releases them (elease_loose).
- **Loose blocking, shop to distributor.** WholesalerLooseStock.blocked_grams. A shop's loose request blocks the distributor's grams when it waits there. Released when the distributor fills it or forwards it (distributors/shop_blocking.py).
- **Automatic shop requests.** The stockist's auto-restock (stockist/auto_restock.py::check_marketplace) now blocks the distributor's packs when it files a shop request. Before, those requests did not block anything.
- **Activity log for orders, by role:** customer edits (item removed, quantity reduced, increase refused), stock taken from an upper tier at checkout (	aken_from_<tier>), stock returned to a tier (eturned_to_<tier>), distributor fill and forward (distributor_filled_shop_request, distributor_forwarded_shop_request), and stockist delivery to distributor (delivered_to_distributor). Earlier logs already covered: placed (customer), shop status changes, stockist picked and at-distributor, supplier shipment events, admin user actions. Log file: logs/activity.log.
- **Migrations applied to db.sqlite3** (backup db.sqlite3.bak-before-loose-blocking-20261120-182534): stockist 0010, distributors 0045.

**Explained to owner: automatic shop requests.** The stockist's auto-restock files a request to the distributor when a shop's own stock drops below its threshold. The request is created automatically, without anyone clicking. It now blocks the distributor's packs at once.

**CORRECTION (important): nearest-first is not yet the agreed flow.** The owner's rule: when a shop cannot cover the whole order, the packs are allotted (blocked) at the nearest tier that has them, and a request still goes to the next level, which must confirm before packs move. The build (section 7) instead takes the packs straight from the upper tier at checkout. The request-and-confirm step is missing. Next step, after owner confirmation: block at checkout and create the upper-level request that the upper tier fills or forwards.

**Still open:**
- Nearest-first: block plus request plus confirm (see CORRECTION).
- Full checkout test: owner will place a real order from each role; the test checklist is to be written with the owner.
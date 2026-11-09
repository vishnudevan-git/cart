# 04 — Pricing & Stock Flow

## The price pipeline

Resolver: `stockist.models.get_stockist_price(product, variant, stockist, enterprise=None)`.
It builds the shop's **true cost (floor)** by walking the chain, then the shop
sets its selling price on top.

```
Supplier price
  + Stockist charges   (fixed ₹ and/or %; priority: product > supplier > global)
  + Distributor margin (per-quantity ₹/unit OR per-weight ₹/kg — never a flat %)
  = Marketplace TRUE COST (the floor)
  + Marketplace margin  (the shop's own markup)
  = Customer price       (clamped: min(sell, MRP) then max(sell, floor))
```

### Worked example (verified end-to-end)

Supplier ₹200 → +Stockist (₹15 fixed + 10%) = ₹235 → +Distributor margin (₹8/kg)
= ₹243 (shop's true cost / floor) → shop sells ₹250 (₹7 margin).
- MRP cap: an MRP of ₹300 would cap a ₹260 attempt back to ₹260.
- Floor wins if MRP < cost (degenerate case).

Trace lives in `stockist/test_price_flow_trace.py` (run with `-v2` to watch it).

## Special pricing rules

- **Global Pricing gate:** a shop must configure Global Pricing once before its
  pricing/catalogue pages unlock. Own-products are exempt (no supply chain).
- **MRP vs Suggested price (mutually exclusive):** a product has a printed MRP
  (hard cap — cannot exceed) **or** a supplier *suggested* price (guidance only —
  a soft "above suggested" warning, never enforced, never shown to customers as
  MRP). Fields: `ProductVariant.suggested_price`, `Product.show_suggested_price`.
- **Direct supply (e.g. eggs):** `Product.direct_supply` — stockist and
  Distributor are skipped entirely (zero commission). `get_stockist_price` returns
  the supplier price with an empty breakdown. The **stockist** controls this flag
  (`/stockist/direct-supply/`), not the supplier. It's global per product.
- **Own products:** the price the shop types **is** the customer price (no chain).

## The stock rule (core invariant)

> Manual stock edits may only **REDUCE** (offline sales / own use). Stock only
> **increases** through a delivered order/restock.

Enforced per tier:
- **Stockist** — raising a qty is rejected; increases come from a
  `StockistRestockRequest` fulfilled by the **supplier** (supplier ↓, stockist ↑).
- **Distributor (wholesale)** — `WholesalerStock` ledger +
  `WholesalerRestockRequest`; delivery from its stockist is the only increase.
- **Marketplace (shop)** — per-row manual reduce only; restock requests (packed =
  FIFO batches, loose = grams) add stock only when delivered.
- **Exceptions (sources, free both ways):** Suppliers set their own stock freely;
  shop **own-products** get a free "Set" control (the shop is their own source).

## Auto-restock (opt-in per tier)

Stockist, Distributor, and Marketplace each have an "Auto-restock when low" card
(enable + threshold + reorder qty). When a row dips to the threshold,
`stockist/auto_restock.py` files the tier's normal restock **request** (never adds
stock directly, preserving the increase-only rule). One open request per variant.

Guards against over-ordering expensive items:
- **Price cap** `auto_restock_max_price` per profile.
- **Per-item switch** `auto_restock_exempt` on each stock row.

Triggers: after manual reduce, after checkout consumes stock
(`transaction.on_commit`), on settings save, and via
`python manage.py auto_restock` (add to cron, e.g. `0 6 * * *`).

## Settlements

On delivery/payment, settlements are auto-created with exact math between
adjacent tiers (shop → stockist, stockist → supplier). Surfaced in the
**Payments Center** (active + a "Completed Settlements" section).

## Suggested price rules (products without an MRP sticker)

- A pack's **suggested price can never be below that pack's supplier price**. The form lifts a too-low entry up to the price, flags it in red while typing, and blocks the save. The server enforces the same rule (`ProductVariantForm.clean`), but only while the suggested-price toggle is on (and no MRP overrides it).
- **Editing one size sets the markup for the rest.** Entering a suggested price on one pack works out its markup (suggested ÷ supplier price) and applies it to every size the supplier has not typed themselves. A newly added size, or a size whose price changes, follows the same markup. Sizes the supplier edited by hand are never overwritten, and a rejected (too low) entry does not change the markup.
- The markup is a ratio, not a per-gram rate, so it still holds when packs have manual or bulk-discounted prices.

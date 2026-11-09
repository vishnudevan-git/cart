# 05 — Features Catalogue

Quick map of the notable, non-obvious features. Chronology and rationale live in
`../PROJECT_NOTES.md`.

## Shop own-products (shop-private catalogue)
`/distributor/my-products/` — a shop adds/edits its own goods (multiple packs
each), fully isolated from the supply chain.
- `Product.owner_enterprise` FK marks ownership.
- Default manager `Product.objects` hides them from suppliers, stockist, other
  shops, admin approval and the shared catalogue. `Product.all_objects` is the
  escape hatch for the owning shop + its customers.
- Each shop gets a hidden in-house `Supplier` row (`is_house_account`) just to
  satisfy the schema — it is a data holder, **never a login role**.
- No supply chain → no supplier cost, no stockist charge, no wholesale margin.
  The price the shop types is the customer price. SKUs sequence via `all_objects`.

## Rent feature (app `rentals`)
Rentable equipment inside each shop. Renters: customer, shop, Distributor.
Publishers: the shop (live immediately), its customers and its Distributor (both
need the shop's approval — the shop is gatekeeper).
- **Master switch:** `SiteSettings.rentals_enabled` is the ONLY global control
  (`/rentals/admin/`). Off → all rent pages 302 to home, **but** in-flight
  bookings can still be closed out/refunded (booking-management pages aren't
  gated), so no wallet money gets stranded.
- **Wallet = real money** (`rentals.Wallet` per customer+distributor), loaded by
  paying the shop cash. **This is NOT reward points** — the two systems never mix.
- Day-granularity slots (min 1 day); per-unit calendar, no overbooking beyond
  quantity; owners can block away-days.
- **Zero commission:** customer-owned item → full rent to owner's wallet;
  shop-owned → simply redeemed; Distributor's item → ₹ recorded, settled offline.
- Lifecycle state machine: `services.transition_booking(booking, action)`
  (requested → confirmed → handed over → returned → closed). Refund on
  reject/cancel-before-start.
- Delivery/pickup are **offline by design**. Renter contact is shared with the
  owner. On damage/loss the renter pays the item cost.
- V1 excludes: hourly slots, late fees, guest rentals.

## Reward points
Separate per-shop points wallet (1 pt = ₹1), auto-granted after orders + manual
adjustments. Kept entirely distinct from the rentals money wallet.

## Direct supply
See [04 — Pricing & Stock Flow](04-pricing-and-stock-flow.md). Stockist-controlled
per-product flag that bypasses stockist + Distributor commissions.

## Multi-stockist routing
`Wholesaler.stockist` FK; `stockist.models.stockist_for(marketplace)` resolves
shop → Distributor → stockist (falls back to first active, so single-stockist
installs need zero config). Stockist queues show only their own chain.

## Admin panel
Custom sidebar admin (`/admin-panel/`) grouped People / Catalog / Commerce /
Support & ops / Settings. Includes user soft-delete/restore, role grants,
category master list, wholesale-Distributor management, and `SiteSettings`
(brand/tagline/rentals switch) + admin self password change.

## i18n
`LocaleMiddleware` + `locale/` catalogs; `compile_messages.py` compiles them.

## Removed features (do not reintroduce without asking)
Supplier Earnings page, standalone Settlement History page (merged into Payments
Center), and the **entire Telegram bot** feature were deliberately removed. See
`../PROJECT_NOTES.md` "Removed features log".

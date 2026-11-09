# 08 — Supplier Suspension

What happens when an admin suspends a supplier, and how that supplier's products
drain out of the supply chain without breaking buffer stock that other tiers
already hold.

> **This document is the source of truth for supplier-suspension behaviour.**
> Any change to the rules below must be agreed against this file first. If a
> future request conflicts with what is written here, raise the conflict before
> changing code.

---

## Naming reminder (code vs UI)

The code names do **not** match the UI labels. Everywhere below, both are given.

| UI label | Code model | Stock table | Status field |
|---|---|---|---|
| Supplier | `suppliers.Supplier` | `ProductVariant.stock` (base) | `status` = `approved` / `suspended` |
| Stockist | `stockist.Stockist` | `stockist.StockistInventory.quantity` | `status` = `active` / `suspended` |
| Distributor | `distributors.Wholesaler` | `distributors.WholesalerStock.quantity` | `status` = `active` / `suspended` |
| Marketplace / Shop | `distributors.Distributor` | `distributors.DistributorStock.quantity` | `status` = `active` / `suspended` |
| Customer | a `User` | — | — |

Supply chain, upstream → downstream:

**Supplier → Stockist → Distributor (Wholesaler) → Marketplace/Shop (Distributor) → Customer**

---

## The core principle

Suspension does **not** delete or overwrite any data. It is a **computed rule**
layered on top of the existing status/stock fields. Reactivating a supplier
instantly restores normal behaviour with nothing to repair.

When a supplier is suspended:

1. **The supplier is cut off as a source.** No tier may order fresh stock from
   that supplier. The supplier's own base stock (`ProductVariant.stock`) is
   treated as **zero / unavailable** — it no longer counts as supply.
2. **Existing buffer stock keeps selling.** Any stock already held downstream
   (stockist, distributor, shop) continues to be sold and can still flow
   **downward** between tiers until it drains to zero.
3. **A product hides per level as that level's reachable stock hits zero.** When
   no reachable buffer remains and no order needs it, the product disappears at
   that level. When it is zero everywhere, the product is gone for everyone.
4. **The supplier cannot log in** — on production and on the dev test-login.

So suspension = "freeze the tap, sell what's already in the pipes, then the
product quietly vanishes."

---

## Rule 1 — Suspended partner cannot log in (prod + test)

**Applies to EVERY partner tier that has a suspend status**, not just suppliers:
`supplier_profile`, `stockist_profile`, `wholesaler_profile` (UI "Distributor"),
and `distributor_profile` (UI "Marketplace/Shop"). Customers have no suspend
status (gated by `is_active`/`is_deleted`) and are unaffected.

- The check lives in one helper: `suppliers/suspension.py :: is_user_suspended_partner(user)`
  — true when the user owns any partner profile whose `status == 'suspended'`.
- Normal login: the auth backend (`accounts/backends.py :: CaseInsensitiveBackend`)
  rejects a user for whom `is_user_suspended_partner` is true.
- Dev utilities that bypass the backend — `quick_test_login` and `demo_login`
  (`accounts/views.py`) — perform the same check before calling `login()`.
- The **Quick Test Login** page excludes suspended partners from its user list
  (`_users_with_role` filters them out for every role).
- Message shown: *"This account is suspended. Contact the admin."*

An already-logged-in supplier is additionally blocked at the portal by the
existing `supplier_required` guard (it already rejects any status != approved).

> There is also a supplier-only helper `is_user_suspended_supplier(user)` kept
> for Rules 2 & 3, which are supplier-specific.

---

## Rule 2 — Cannot suspend while an order is in progress

Before a supplier is suspended (single **and** bulk action), the system checks
for **in-progress supplier shipments** for that supplier:

```
SupplierShipment.objects.filter(supplier=supplier)
    .exclude(status__in=['received', 'unavailable', 'cancelled'])
```

The non-terminal statuses that **block** suspension are:
`awaiting`, `delayed`, `confirmed` (= Ready), `dispatched`.

If any exist → **suspension is refused** with a warning that names the blocking
order(s)/line(s). The admin must let those lines reach `received` / `cancelled`
(or `unavailable`) first. In a bulk action, suppliers with no in-progress
shipments are still suspended; the blocked ones are reported and skipped.

---

## Rule 3 — Buffer-stock-aware product visibility (the cascade)

This is the heart of the feature. For a **suspended** supplier's product, each
tier's catalogue shows the product **only while stock is reachable** for that
tier. The supplier's own base stock never counts (Rule, principle #1).

### "Total available stock" for a product on a shop's site

For one Marketplace/Shop (`Distributor`) `D`, served by wholesale `Distributor`
(`Wholesaler`) `W`, served by `Stockist` `S`, the stock available to that shop's
site for a product `P` is the **sum across the three downstream tiers**:

```
available(P, D) =
      Σ DistributorStock.quantity   where distributor = D,  variant.product = P
    + Σ WholesalerStock.quantity    where wholesaler   = W,  variant.product = P
    + Σ StockistInventory.quantity  where stockist     = S,  variant.product = P
```

- There is normally **one common stockist**, so the stockist term is shared
  across all shop sites, but each shop's total still differs by its own and its
  wholesaler's holdings.
- The supplier's `ProductVariant.stock` is **excluded** for suspended suppliers.
- "Reachable buffer" respects the normal chain — a shop can still pull from its
  stockist's buffer; the stockist can still pull from the wholesaler's buffer;
  buffer only flows **downward**; nobody pulls from the suspended supplier.

### Visibility decision per tier

For a suspended supplier's product `P`:

- **Customer storefront (shop site):** visible on shop `D` iff `available(P, D) > 0`
  (shop + its wholesaler + its stockist), **or** a pending order references `P`.
- **Shop / Distributor catalogue:** visible iff it can still be sourced from the
  stockist/wholesaler buffer, i.e. the stockist + wholesaler (+ shop) stock for
  `P` is > 0.
- **Distributor (Wholesaler) catalogue:** visible iff stockist (+ wholesaler)
  stock for `P` is > 0.
- **Stockist catalogue:** visible iff the stockist still holds stock of `P` > 0.

In all cases: **zero reachable stock + no pending order ⇒ hidden at that level.**
Zero everywhere ⇒ hidden everywhere.

### Multiple products from one supplier

Each product is judged independently. On suspension:

- Products with **no reachable buffer anywhere below and no pending order** are
  hidden **immediately** across all levels.
- Products that still have buffer **or** a pending order stay visible at the
  levels that can still reach them, and drain out tier-by-tier as stock falls to
  zero.

### Pending-order interaction

- A product with an **open order** stays visible at the relevant level(s) until
  the order is resolved, even transiently — so in-flight orders are never
  orphaned. (This is the same signal Rule 2 uses to block suspension in the
  first place; it is repeated here for the post-suspension drain window.)
- If an order exists **and** sufficient stock to fulfil it is present at a bottom
  level, the product may still be hidden from browsing/new-ordering at upper
  levels while that order fulfils from the bottom buffer.

---

## Reactivation

Setting the supplier back to `approved` removes the computed suspension gate
entirely and immediately:

- Login works again.
- The supplier's base stock counts as supply again.
- Products reappear in every catalogue per the normal (non-suspension) rules.

No data migration or stock repair is needed because suspension never mutated
product status or stock quantities.

---

## Where this is implemented

| Concern | Location |
|---|---|
| Central helper (is-suspended check, reachable-stock aggregate, visibility gate) | `suppliers/suspension.py` *(new)* |
| Login block (normal) | `accounts/backends.py` |
| Login block (dev utilities) + test-login list filter | `accounts/views.py` |
| Suspend guard (in-progress orders) — single + bulk | `admin_panel/views.py` (`supplier_suspend`, `bulk_supplier_action`) |
| Catalogue gating — customer storefront | `products/views.py` |
| Catalogue gating — cart | `cart/views.py` |
| Catalogue gating — shop/distributor | `distributors/views.py` |
| Catalogue gating — stockist | `stockist/views.py` |
| Downstream stock visibility (buffer) | `products/stock_display.py :: attach_downstream_stock`; shown on admin Stock, supplier Products, supplier Stock pages via `templates/partials/product_stock_table.html` + `stock_manage.html` |

---

## Explicitly out of scope / decided

- Suspension does **not** zero or edit `ProductVariant.stock` — the supplier's
  number is left intact and simply ignored while suspended.
- Suspension does **not** change `Product.status`.
- Buffer flows **downward only**; no tier ever sources from the suspended
  supplier during suspension.

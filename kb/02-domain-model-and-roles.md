# 02 — Domain Model & Roles

> Foundational rule (see `../DOMAIN_MODEL.md`): **the platform is
> distributor-based.** Every storefront belongs to a shop; products only ever
> appear inside a specific shop's context — never in a generic catalogue. A
> logged-out public page therefore shows **no products** and leads with
> Login / Sign up.

## The supply chain

```
Supplier → Stockist → Distributor (wholesale) → Marketplace (shop) → Customer
```

- **Supplier** — owns products, sets supplier price, holds source stock.
- **Stockist** — optional processing/charges layer; holds inventory. Multiple
  stockists exist, but each Distributor buys from exactly **one** stockist.
- **Distributor (wholesale)** — a wholesale parent. One Distributor → many shops
  (one-to-many). Adds a per-weight or per-quantity margin. Its MAIN job is to
  find and create shops.
- **Marketplace (shop)** — the customer-facing storefront. Shops are **isolated**
  from each other (separate customers, stock, data). Adds its own margin.
- **Customer** — self-registers; tied to one shop (via login or invite code).

## The 6 roles (and the critical naming map)

The UI labels were renamed twice, but **code identifiers were never changed**.
This mismatch is the single most important thing to know when reading the code.

| Code role (`User.role`) | Django display | UI label shown to users | What it is |
|---|---|---|---|
| `admin` | Admin | Admin | The sole platform operator. "Admin is not a user." |
| `supplier` | Supplier | Supplier | Product source. |
| `stockist` | Stockist | Stockist | Processing/charges + inventory tier. |
| `distributor` | Marketplace | **Marketplace** | The shop / storefront. |
| `wholesaler` | Distributor | **Distributor** | The wholesale parent tier. |
| `customer` | Customer | Customer | The shopper. |

**Watch out:**
- Code `distributor` = UI **"Marketplace"** = a shop. (Was labelled "Enterprise"
  until 2026-07-27, and plain "Distributor" before Stage 1.)
- Code `wholesaler` = UI **"Distributor"** = the wholesale tier (model
  `distributors.Wholesaler`, URLs under `/wholesale/`).
- In code comments/docstrings the shared product pool is called the
  **"shared catalogue"** (the word "marketplace" was freed up for the shop tier).

Source of truth: `accounts/models.py` → `User.ROLE_CHOICES`.

## Roles & joining rules

- **Customer self-registers** — the default role for everyone.
- **Supplier / Stockist / Distributor / Marketplace are granted by admin only.**
- **One account = one role.** Multi-role was removed (Stage 4);
  `has_multiple_roles()` is hard-coded `False`, the role switcher is gone, and an
  account can never hold zero roles. A shop that also sells its own goods uses the
  **Own Products** feature, not a second role (see [05 — Features](05-features.md)).
- **Admin is not a customer** — profile / addresses / rewards / cart surfaces are
  gated to customers only.

## Password-reset permissions

- Admin can reset any user's password and change their own in-app.
- Customers cannot reset anyone else's password.
- A Distributor can reset passwords only for **its own** shops.

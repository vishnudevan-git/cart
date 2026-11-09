# 03 — Apps & Architecture

## Django apps (from `INSTALLED_APPS`)

| App | Responsibility |
|-----|----------------|
| `accounts` | Custom `User` (roles, soft-delete, pincode, addresses), auth, password reset, factories for tests. |
| `suppliers` | Supplier dashboard, products, stock, restock. Hosts hidden "house account" suppliers for shop own-products. |
| `products` | `Product` / `ProductVariant`, categories, storefront, product detail, shared-catalogue managers. |
| `cart` | Session + DB cart, cart pricing floor. |
| `orders` | Checkout, order lifecycle, fulfilment. |
| `payments` | Payments Center, settlements between tiers. |
| `admin_panel` | Custom admin UI (sidebar modules), `SiteSettings` singleton, demo guards, runbook. |
| `delivery` | Delivery/pickup views + a DRF API (`api_views.py`, `serializers.py`). |
| `distributors` | **Two tiers in one app:** the shop (`Distributor` model, `/distributor/`) and the wholesale tier (`Wholesaler` model, `wholesaler_*` modules, `/wholesale/`). |
| `stockist` | Stockist inventory, processing charges, pricing resolver (`get_stockist_price`), direct-supply control, auto-restock engine. |
| `support` | Support/ops surfaces. |
| `transactions` | Transaction records + role management ("Change Role"). |
| `rentals` | Rent feature: `Wallet` (real money), `RentalItem`, `RentalBooking`, services state machine. Gated by a master switch. |

Plus framework apps: `admin`, `auth`, `contenttypes`, `sessions`, `messages`,
`staticfiles`, `rest_framework`, `rest_framework.authtoken`, `axes`.

## URL map (`lakrish_mart/urls.py`)

| Prefix | Included from |
|--------|---------------|
| `/` | `products.urls` (storefront is the home) |
| `/accounts/` | `accounts.urls` |
| `/supplier/` | `suppliers.urls` |
| `/cart/`, `/orders/`, `/payments/` | respective apps |
| `/admin-panel/` | `admin_panel.urls` (custom admin) |
| `/django-admin/` | Django's built-in admin |
| `/delivery/`, `/support/`, `/stockist/`, `/transactions/` | respective apps |
| `/distributor/` | `distributors.urls` (the **shop** tier) |
| `/wholesale/` | `distributors.wholesaler_urls` (the **wholesale** tier) |
| `/rentals/` | `rentals.urls` |
| `/runbook/`, `/product-lifecycle/` | interactive explainers |

## Middleware notes

Standard Django stack plus `LocaleMiddleware` (i18n),
`admin_panel.middleware.DemoTransactionGuardMiddleware` (guards demo data), and
`axes.middleware.AxesMiddleware` (login throttling).

## UI architecture

- Shared base templates + a `partials/` folder (121/141 templates extend a base).
- Mobile-first redesign layer: `static/css/modern-shop.css` loaded after
  `premium-ui.css`, toggled via a `{% block body_class %}` hook — additive, so it
  doesn't touch app logic. Reference look: Blinkit/Zepto/BigBasket.
- Sidebar modules are configured in `lakrish_mart/ui_config.py`.

## Core data policies

- **No hard delete (soft-delete everywhere).** Every "delete" marks the row
  removed; records are admin-restorable. Models carry `is_deleted` + `deleted_at`;
  default managers hide deleted rows. Django-admin hard-delete is blocked via a
  `NoHardDeleteMixin`. A feature declared "not required", however, is deleted from
  the code fully (no dead code).
- **Shop isolation via managers.** `Product.objects` (default manager) hides
  shop-private own-products; `Product.all_objects` is the escape hatch used only by
  the owning shop. `ProductVariant.objects.shared()` must be used by upstream
  surfaces so shop-private packs don't leak.
- **Single database** (see [01](01-project-overview.md)).

See `../PROJECT_NOTES.md` §3b for the full delete-policy history.

## SKU format

`{SUPPLIER(4)}-{CATEGORY(4)}-{SEQUENCE(3)}`, for example `NUTR-NUTS-013`.

- **Supplier code** (`Supplier.code`): 4 characters, **unique per supplier**, generated from the company name (`NutriHub Traders` becomes `NUTR`; if taken, the last character becomes a digit: `NUT2`, `NUT3`...). Built by `lakrish_mart/codegen.py`.
- **Category code** (`Category.code`): 4 characters, unique, generated from the category name (`Nuts` becomes `NUTS`, `Dry Fruits` becomes `DRYF`, `Spices` becomes `SPIC`; `Organic Products` becomes `OPRG` because `ORGA` is taken). Names shorter than 4 letters are padded with `X` (`Oil` becomes `OILX`).
- **Sequence**: counts that supplier's products in that category, starting at `001`. It is 3 digits (up to 999) and simply grows to 4 digits (`1000`, `1001`...) if a supplier ever passes that. Shop-private products count too.
- **Issued once** when a product is first saved and **never changed** afterwards, even if the category or supplier name changes. If two saves ever collide on the same number, the second retries with the next one.
- Products created in the old format (for example `NTNU0013`) were converted with `python manage.py reissue_skus` (use `--dry-run` to preview). It fixes SKUs whose shape is right but padding is off (`NUTR-NUTS-00013` becomes `NUTR-NUTS-013`, number kept) and gives any other SKU the next free number, then writes an old-to-new CSV to `logs/`. Running it again changes nothing. No other table stores a SKU, so nothing else needed updating.
- A product with no category uses `XXXX`; a category code shorter than 4 characters is padded with `X`.

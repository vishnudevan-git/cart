# Project Notes & Decisions Log

> **Structured reference:** an organized knowledge base lives in `kb/` (start at
> `kb/README.md`). This file stays the chronological, append-only decisions log
> (the "why/when"); `kb/` is the rewritten "how it works" handbook. When a
> behaviour changes, append the decision here AND update the relevant `kb/`
> article. _(KB added 2026-10-29.)_

## ROLE RENAME + NEW TIER — 2026-07-25 (confirming before building)
- **DECIDED:** the storefront/customer-facing role (currently "distributor")
  → public label **"Enterprise"** (label-only rename; internal code keeps
  "distributor" unless a full rename is later chosen). Same pattern as
  VyaparDock→Shop.
- **PROPOSED (awaiting user confirmation):** insert a NEW **"Distributor"** tier
  between Stockist and the Enterprises. New chain:
  Supplier → Stockist → Distributor(new wholesale parent) → Enterprise(many
  storefronts, each with own customers) → Customer.
  - One Distributor → many Enterprises (one-to-many).
  - Enterprises are ISOLATED from each other (separate customers/stock/data,
    "no interconnection").
  - Pricing gains an extra margin tier (Distributor margin, then Enterprise
    margin) — work out after structure is confirmed.
- This is a structural/data-model change, not just a label swap — plan carefully.

### Stage 1 DONE (2026-07-25) — label rename distributor → "Enterprise"
- Templates: all capital "Distributor" → "Enterprise" (33 files) + ~14 files of
  lowercase visible phrases → "enterprise". Code identifiers UNTOUCHED
  (`{{ distributor.x }}`, `distributors:` URLs, `active_role=='distributor'`,
  CSS classes, JS `data.distributor`/`distributorGain`, model class names).
- Python: 4 user-facing flash messages in admin_panel/views.py → "Enterprise".
- Django-admin `verbose_name` left as-is (kept Stage 1 migration-free; that page
  is internal-only).
- Verified: full crawl 201 pages → 0 errors; "Enterprise code" shows on register;
  price-lifecycle JS page renders with no console errors.
- Interactive artifact updated to the 5-tier model with a two-margin simulator
  (same URL). Internal code word "distributor" still = the storefront/Enterprise;
  the NEW wholesale "Distributor" tier is Stage 2 (not built yet).
### Stage 2a DONE (2026-07-25) — new Distributor (wholesale) tier: data foundation
NAMING MAP: code `Distributor` = UI "Enterprise" (storefront); NEW code
`Wholesaler` = UI "Distributor" (wholesale tier). Roles: code `distributor` =
UI "Enterprise"; NEW code `wholesaler` = UI "Distributor".
- New model `distributors.wholesaler_models.Wholesaler` (company, area, status,
  default_margin_percent for Stage 3).
- `Distributor.wholesaler` FK added (SET_NULL) → one Wholesaler : many
  Enterprises (`wholesaler.enterprises`). Enterprises stay independent.
- New role `wholesaler` in User.ROLE_CHOICES + `User.is_wholesaler()`.
- Registered in Django admin: "Distributor (wholesale)" list/add + editable
  `wholesaler` column on the Enterprise list to assign. Admin can create &
  assign NOW via /django-admin/.
- Migrations: accounts.0016, distributors.0034. Verified: check clean, crawl 201
  pages 0 errors, created a demo Wholesaler ("Chennai Wholesale Distributors",
  8%) with both Enterprises assigned — enterprise_count()=2 works.
- Demo login for that wholesale Distributor: user 9000000099 / test1234.
### Stage 2b DONE (2026-07-25) — wholesale Distributor role is functional
- `distributors/wholesaler_views.py`: `wholesaler_required` decorator + dashboard
  (shows the Distributor's Enterprises, active count, wholesale margin, territory).
- `distributors/wholesaler_urls.py` under `/wholesale/` (namespace `wholesaler`).
- Home routing: active_role `wholesaler` → `wholesaler:dashboard`.
- Templates: wholesaler_dashboard.html, wholesaler_suspended.html.
- Verified: crawl 202 pages 0 errors; demo-login `?role=wholesaler` → dashboard
  renders (Chennai Wholesale Distributors, 2 Enterprises), no console errors.
- **Stage 2 COMPLETE** — the Distributor wholesale tier exists, is manageable
  (Django admin), has a role + login dashboard, and one-Distributor→many-
  Enterprises works.
### Stage 3 DONE (2026-07-25) — two-margin pricing
- `stockist.models.get_stockist_price(product, variant, stockist, enterprise=None)`:
  added optional `enterprise` param. When the Enterprise has an active parent
  Wholesaler, its `default_margin_percent` is applied on top of the stockist
  cost (adds a "Distributor margin" breakdown line). Default None = unchanged
  (backward compatible).
- Call sites updated to pass the Enterprise so the wholesale margin flows into:
  cart floor (`cart/models.py` _current_stockist_floor + subtotal/unit_price),
  product display (`products/views.py` ×2), checkout (`orders/views.py`),
  and the Enterprise's own pricing screens (`distributors/views.py` ×5).
- Chain now: supplier → stockist charges → **+Distributor(wholesale) margin** →
  Enterprise cost → **+Enterprise margin** → customer; still floored at cost.
- Verified: Turmeric 100g ₹80 → ₹88.20 (stockist) → ₹95.26 (+8% Distributor) →
  customer priced ₹97 (above floor). Crawl 202 pages, 0 errors.
- **RESTRUCTURE COMPLETE** (Stages 1–3): Enterprise rename + new Distributor
  wholesale tier + two-margin pricing all live & verified.
- NOTE: both demo Enterprises are assigned to demo "Chennai Wholesale
  Distributors" (8%), so their costs rose 8% — real Enterprises may need to
  re-price to keep their own margin.

### UI fixes (2026-07-25)
- Quick Test Login: added missing **Distributor** (wholesaler) role badge +
  "Enterprise (shop)" label; view `roles` list now includes 'wholesaler'.
- Role switcher (base.html) labels fixed: `distributor`→"Enterprise",
  `wholesaler`→"Distributor".
- Removed the "Fresh quality checked" utility banner from the logged-in customer
  home (products/home.html) and the product-list utility strip.
- Product list: category menu is now **sticky** (`.sticky-cats`, stays on scroll)
  and the left filter is **sticky** on desktop (`.sticky-filter`). Verified:
  category bar stays pinned while products scroll; crawl 209 pages 0 errors.

### Distributor self-service + weight/quantity margin DONE (2026-07-25)
Business model recorded in memory ([[distributor-enterprise-roles]]):
Distributor does no processing, just supplies its shops; MAIN role = find &
create shops; earns via margin. Customer location-switch = future (not now).
- **Distributor creates shops (Enterprises):** `/wholesale/shops/new/`
  (`create_shop`) — auto-links new shop to this Distributor (wholesaler=self),
  role 'distributor', auto invite code. Dashboard has a "Create Shop" button.
- **Distributor resets its shops' passwords:** `/wholesale/shops/<pk>/reset-
  password/` (`reset_shop_password`) — scoped to OWN shops only
  (get_object_or_404(..., wholesaler=w)). Per-shop link on the dashboard.
- **Margin by WEIGHT or QUANTITY (not %):** Wholesaler gained `margin_mode`
  (per_quantity ₹/unit | per_weight ₹/kg) + `margin_value`; legacy
  default_margin_percent kept but unused in pricing. `get_stockist_price` now
  applies: per_quantity → +₹value; per_weight → +₹value×(unit_grams/1000).
  Admin create form + list/detail/dashboard show `margin_display()`.
  Migration distributors.0035. Verified: per-qty ₹10 → Turmeric100g cost ₹98.20;
  per-weight ₹40/kg → ₹4 → ₹92.20. Crawl 209 pages 0 errors.

### Admin-panel Distributor management DONE (2026-07-25)
- Custom `/admin-panel/wholesale-distributors/` UI (no Django admin needed):
  list, create, detail (assign/unassign Enterprises), suspend/reactivate.
  Views `wholesaler_*` in admin_panel/views.py; templates wholesaler_list/
  create/detail.html; sidebar now shows **Distributors** (wholesale) above
  **Enterprises**.
- Verified: crawl 207 pages 0 errors; list/create/detail 200; created+deleted a
  test Distributor via the form; page renders with no console errors.

## UI REDESIGN — 2026-07-25 (mobile-first, modern grocery-app look)
Target: users on **mobile Chrome**. Reference apps: Blinkit/Zepto/BigBasket.
Approach: additive `static/css/modern-shop.css` theme layer (loaded after
premium-ui.css) + `{% block body_class %}` hook in base.html — low-risk, doesn't
touch the working app's logic.
- **DONE: customer storefront** (`templates/products/storefront.html`, body
  class `ms-shop`): compact rounded hero, sticky search, horizontal-scroll
  category chips, 2-col product tiles (image, 2-line name, weight, price + MRP
  strikethrough + Deal badge + rating + **ADD** button), and an app-like fixed
  **bottom nav** (Home/Search/Cart/Account). Verified at 375px: renders clean,
  no console errors, no horizontal overflow, full-width.
- **NEXT (same design language):** product detail, cart, checkout, public
  landing, logged-in customer home. Then optionally polish role dashboards.

## AUDIT LOG — 2026-07-24 (post-migration health check)

- **URL error crawl:** wrote a crawler (`scratchpad/crawl.py`) that logs in as
  each role (admin/supplier/distributor/stockist/customer + anon) and GETs every
  route with real DB ids. **201 URLs checked → 0 HTTP 500s, 0 crashes.** The
  migrated app is logically healthy; every page renders.
- **Duplication (DRY) assessment:** already good. 121/141 templates extend a
  shared base; 6 layout bases (global + dashboard + per-role) + a `partials/`
  folder. No rampant header/search duplication. Minor future cleanup only:
  6 bases share some header markup; `storefront.html` & `product_detail.html`
  carry their own nav — could be factored to a shared partial (low priority).
- **Database decision:** **KEEP A SINGLE DATABASE.** Splitting one app's models
  across multiple DBs breaks joins/transactions/foreign keys and adds large
  complexity for no benefit here. Per-instance isolation (the docking-station
  vision) is achieved by **separate deployments, each with its own DB** (the
  Docker/federation path) — NOT by splitting this app's models. Revisit only if
  a concrete scaling/tenancy need appears.
- Known cosmetic: `/favicon.ico` 404 in logs (no favicon shipped) — trivial.

### Deep multi-method audit — 2026-07-25 (fixes)
Methods run & clean: check --deploy (only expected dev warnings), makemigrations
--check (no drift), compileall (no syntax errors), collectstatic, full test
suite (15 pass), authorization crawl (144 gated URLs, 0 bypass), data integrity
(0 issues / 8 checks), security (no hardcoded prod secrets, .env gitignored).
- **BUG FIXED:** `distributors.reset_customer_password` GET rendered a missing
  template `distributors/reset_customer_password.html` → 500. (Normal use is a
  POST modal, so it hid; direct GET crashed.) Created the template; GET now 200.

**Full method list run 2026-07-25 (14 methods):** (1) URL crawl all roles — 201
pages, 0 crash; (2) check --deploy — only expected dev warnings; (3)
makemigrations --check — no drift; (4) compileall — no syntax errors; (5)
collectstatic — ok; (6) test suite — 15 pass; (7) authorization crawl — 144
gated URLs as wrong role, 0 bypass; (8) data integrity — 8 checks, 0 issues;
(9) security — no hardcoded prod secrets, .env gitignored; (10) template refs
(url/include/extends) — all resolve; (11) python template refs — all exist;
(12) static asset refs — all exist; (13) live front-end — no console errors, all
assets 200; (14) query counts — 2–18/page, no N+1. **Result: 1 real bug found &
fixed; app otherwise healthy.**
- To review later (not a crash, a business choice): the documented "5% minimum
  margin on distributor repricing" appears not enforced in code (only a 0% floor
  / never-below-cost). Decide whether to enforce 5% or keep as-is.

### UI walkthrough — 2026-07-25
- Programmatic UI-integrity scan of **116 rendered pages**: 0 issues (no POST
  forms missing CSRF, no leaked template syntax, no object-repr leaks, no error
  text).
- Visual spot-checks (all clean, polished, consistent design system):
  admin dashboard/products/orders, supplier dashboard, distributor dashboard,
  stockist dashboard + processing-charges, public storefront (browse), product
  detail (variants/price/stock), and the account-suspended pages.
- **No UI errors found to fix.** Observations (data/config, not bugs):
  seed product images don't match names; a stray "Smoke Checks" category in
  seed; invite/store links render `localhost:8000` (correct for dev, needs real
  domain in prod); minor horizontal overflow on admin product list at ~760px.
- Note: seed has the only stockist and 1 of 2 distributors **suspended**; their
  dashboards are reachable once activated by admin.


> **⚠ MAJOR PIVOT (2026-07-24):** The plan changed from "fresh from-scratch
> rebuild" to **migrating the whole Lakrish project into Cart as-is** and
> adapting one feature at a time. The from-scratch code we wrote earlier
> (accounts/admin_panel/catalog/core/vyapardock) is backed up in the scratchpad
> `cart_scratch_backup/` and has been removed from Cart. Cart now runs the copied
> Lakrish (settings module `lakrish_mart`, seeded `db.sqlite3`). Sections below
> from before the pivot describe the abandoned from-scratch build — kept for
> history. Current source of truth starts at "MIGRATION" near the top.
>
> **Provider model:** user = software PROVIDER / city "docking station"; the SOLE
> admin. Suppliers & distributors run on the user's instance or self-host their
> own and collaborate. Rebrand Lakrish→"Shop" is a DEFERRED later pass.
>
> **Dev admin:** username `9000000005` / password `admin123` (set for dev).


This file is the running record of what we've decided for this project. It is
updated as we discuss. Before doing anything new, this log is checked first —
if a new request conflicts with something here, it is flagged and the prior
decision is cited, not silently overwritten.

_Last updated: 2026-07-24_

---

## 1. Identity & branding

| Item | Decision |
|------|----------|
| Internal codename | **VyaparDock** — used only for folder, Python package (`vyapardock/`), Docker image names. **Never shown to users.** |
| Public brand | **Shop** — the customer-facing name. Controlled by one setting: `SITE_BRAND` in `vyapardock/settings.py`. |
| Folder name | Stays `Cart` for now (session working directory). Renaming is a later, separate step. |

## 2. Architecture

| Item | Decision |
|------|----------|
| Deployment model | **One deployment, API-first internally.** Each role is a self-contained module talking to others only through a defined API — so it can later split into separate services. |
| Federation | **Phase 2 (future).** Independent parties self-hosting their own instances and connecting over the internet comes later. Not built now. |
| Docker | Yes. Each module containerized so a business owner can later self-host their own instance and "dock" into the network. `Dockerfile` + `docker-compose.yml` exist. |
| Database | Postgres in Docker; SQLite for plain local dev (auto-switch). |
| The new piece vs. old project | A **many-to-many connection layer** — any approved distributor can connect to any approved supplier's catalog (old project was a fixed one-supplier→one-distributor pipeline). This is the key difference from Lakrish. |

## 3. Roles & joining model

| Item | Decision |
|------|----------|
| Customer | **Self-registers.** Default role for everyone. |
| Supplier / Distributor / Stockist | **Granted by admin only.** A user cannot self-assign these. |
| Multi-role | One user account can hold several roles at once (e.g. a distributor is also a customer). |

## 3b. Data policy — NO HARD DELETE (noted 2026-07-24)

- **Nothing is ever permanently deleted.** Every "delete" is a **soft delete** —
  the record stays in the DB, marked removed/inactive. Applies to ALL entities:
  accounts, role profiles, products, categories, orders, everything.
- No hard/permanent account-deletion option anywhere.
- New models get `is_deleted` + `deleted_at` + a manager hiding deleted rows.
- Restorable: YES — soft-deleted items are admin-restorable (chosen 2026-07-24).
- **Implemented 2026-07-24:**
  - Users ✅ soft (Deactivate/Restore).
  - Role profiles ✅ Suspend/Reactivate (non-destructive; no delete option).
  - Categories ✅ now soft — `Category.is_deleted`/`deleted_at`, `soft_delete()`
    /`restore()`, default manager hides deleted; admin panel "Remove" → Archived
    section with Restore. (was hard-delete)
  - Django admin hard-delete **blocked** everywhere via `core.admin_mixins
    .NoHardDeleteMixin` (has_delete_permission=False + removes delete_selected)
    on User + 3 role profiles + Category → /django-admin delete pages now 403.
  - Verified: soft-delete keeps row, restore works, django-admin delete 403.
  - Migration `catalog.0002` applied.
- An adversarial whole-codebase audit workflow was run. It confirmed the used
  paths are safe, and flagged two MINOR remaining gaps the user chose to DEFER
  ("good enough for now", 2026-07-24):
    1. Django's built-in `auth.Group` + DRF `authtoken.Token` admins at
       /django-admin/ still allow hard delete (not wrapped by NoHardDeleteMixin).
    2. Enforcement is UI/admin-deep only — the ORM `.delete()` itself is not
       overridden, so raw code could still hard-delete. (No such code exists.)
  Revisit if/when it matters. Fix sketch for #1: unregister+re-register Group and
  TokenProxy with NoHardDeleteMixin. For #2: a SoftDeleteModel base overriding
  .delete().

## 4. Permission rules (password reset) — noted 2026-07-24

- **Admin can reset any user's password.**
- **Customer CANNOT reset an admin's password.** (Customers cannot reset other users' passwords at all.)
- **Admin must be able to reset their OWN password from inside the app.** → _Not built yet. See To-Build._

## 5. Home page — RESOLVED 2026-07-24

- Request: reuse the same home page from the old project (Lakrish).
- **Decision: copy the old layout/design, rebrand to "Shop", show static
  placeholders** for categories/featured products until the products +
  distributors modules exist.
- Old layout being replicated: utility strip (quality/delivery/secure) → green
  "Fresh. Organic. Delivered." hero with search + "Shop All Products" → "Shop by
  Category" grid → "Featured Products" grid.
- Rebranding applied: no "Lakrish" name, no `lakrish.js`; title/hero use "Shop".
- Search bar + "Shop All" + category links are visual placeholders for now
  (product list/search not built yet).

---

## 6. Build order (agreed)

1. ✅ accounts — identity & roles
2. ✅ home page + admin role
3. ⬜ suppliers — catalog (a supplier manages own products)
4. ⬜ connections — distributor↔supplier many-to-many (the new piece)
5. ⬜ stockist — optional processing layer
6. ⬜ distributor storefront
7. ⬜ cart & orders
8. ⬜ payments & settlement
9. ⬜ delivery, support, admin oversight

## 7. Current status

- Running locally at `http://localhost:8000` (SQLite dev DB).
- Admin login: username `admin`, password `admin` (dev only — weak, never use in production).
- Built & verified: home page ("Shop" banner), admin login/dashboard/grant-role/users list, accounts API (register/login/me/grant-role).

## 8. To-Build (flagged, not yet started)

- [ ] In-app "change my own password" for admin (and later, per role). _§4_
- [x] Resolve home-page reuse vs. "Shop" branding conflict. _§5 — done_

### Requested 2026-07-24

- [x] **Header buttons:** "Admin" replaced with customer **Login** + **Sign up**.
  Built customer login + signup pages (`/accounts/login/`, `/accounts/signup/`)
  with session auth and routing (staff → admin dashboard, customer → home).
  Logged-in header shows "Hi, <user>" + Log out (+ Admin link if staff). _Done._
- [x] **Public home cleaned up:** removed product search bar, "Shop All
  Products", and the distributor-scoped category/featured sections (per the
  distributor-based model, DOMAIN_MODEL.md). Home now leads with Login / Sign up
  and three info cards. _Done._
- [ ] **Admin manages "Shop by Category":** categories must be admin-editable,
  not hardcoded. Needs a **Category** model + admin CRUD screen. (First slice of
  the products module — needs a new migration.) NOTE: under the distributor-based
  model, categories are ultimately distributor-scoped; confirm where they live
  (global master list vs. per-distributor) before building. _Pending._
- [x] **Signup fields from Lakrish** (user picked all four): mobile-or-email
  identifier (auto-detects & stores as phone or email), first/last name, confirm
  password (with live match hint), pincode, and distributor code. Distributor
  code is stored as `User.pending_distributor_code` (raw, to be linked when the
  distributors module exists); pincode as `User.pincode`. Migration 0002 applied.
  Skipped Lakrish's IP-based country-code auto-detect and pincode area-lookup
  (external calls). _Done & verified (email/mobile/invalid paths)._
- [x] **Admin panel with sidebar** (each activity = a module). Sidebar groups:
  People / Catalog / Commerce / Support & ops / Settings. Future modules shown
  greyed as "Soon". Built & verified:
    - **People:** Users list (soft-delete/restore + reset any user's password);
      Suppliers/Distributors/Stockists lists with grant + suspend/reactivate.
    - **Catalog:** Categories master list (add/edit/delete) — new `catalog` app,
      `Category` model. _("Shop by Category" admin edit from earlier request.)_
    - **Settings:** Site settings (admin-editable brand/tagline via new
      `admin_panel.SiteSettings` singleton; overrides settings.py default,
      reflects on public site); **My password** = admin self-service change
      (requires current password) — the §4 gap, now DONE.
  Migrations: `catalog.0001`, `admin_panel.0001` applied.
- [x] **Consolidated to a single login page** (2026-07-24). Removed the
  duplicate `/admin/login/` (now 404) and its view/template; admin guard's
  `login_url` now points at `accounts:login`. Added `?next=` support so a
  logged-out visit to an admin page returns there after login. Verified:
  /admin/ → /accounts/login/?next=/admin/ → back to /admin/; customer login
  still goes home.
- [x] **RESOLVED — guest product visibility:** The platform is
  **distributor-based** (see DOMAIN_MODEL.md). Products only exist inside a
  distributor's context, so a logged-out public home has nothing to show →
  **remove the public product search bar**; the public home leads with
  **Login / Sign up**. Product browsing happens after login or inside a
  distributor's store/invite link.

---

## Stage 4 — Single-role policy + Enterprise own-products (2026-07-27)

**Decision (user, 2026-07-27):** multi-role is removed. *"Each person should have
any one role only... you have to remove that option entirely."* A shop that also
has its own goods gets a **feature inside its Enterprise login**, not a supplier
role. And *"Admin is not a user"* — no customer surfaces in the admin login.

### 4a. One account = one role
- `User.set_roles()` / `add_role()` collapse to a single role; `has_multiple_roles()`
  is hard-coded `False`.
- Role **switcher removed** from `base.html` and `base_dashboard.html`;
  `accounts:switch_role` route+view deleted; `templates/accounts/pick_role.html`
  deleted. `accounts:pick_role` kept as a redirect-only shim so old links still land
  on the user's own role home.
- Login no longer branches on "more than one role" — it goes straight to the
  role's landing page.
- Admin user **create/edit** now use a single-role radio group (was: supplier /
  enterprise checkboxes + a customer radio). `_ensure_role_profile()` creates the
  one profile the chosen role needs (Supplier / Distributor / Wholesaler).
- `transactions` role management: "Add Role" → **"Change Role"** (replaces the
  existing one); `remove_role` is disabled — an account can never hold zero roles.
- Enterprise `save()` no longer auto-adds a 'customer' role.
- Data: the 2 pre-existing multi-role users were collapsed. **0 multi-role users,
  0 role/roles mismatches** remaining.

### 4b. Admin is not a customer
- Profile / Delivery Addresses / Rewards views are wrapped in a new
  `accounts.views.customer_only` decorator — every non-customer role is redirected
  to its own dashboard (verified: 302 for admin/supplier/stockist/distributor/
  wholesaler, 200 for customer).
- The profile icon is gone from the dashboard topbar for non-customers; the cart
  offcanvas + its JS render only for guests and customers.
- Verified per role: the only remaining "/orders/" links on the admin and supplier
  dashboards are their own order-management pages, not "My Orders".

### 4c. Enterprise own-products (shop-private catalogue)
New: **My Own Products** in the Enterprise sidebar (`/distributor/my-products/`) —
add / edit / show-hide / soft-remove, with multiple packs per product, mobile-first.

Ownership & isolation:
- `Product.owner_enterprise` FK → the one shop that owns it.
- `Product.objects` (**default manager**) filters owned products **out**, so
  suppliers, the stockist, other Enterprises, the marketplace and admin approval
  can never see or source them. `Product.all_objects` is the deliberate escape
  hatch, used only by the owning shop's screens and its own customers' storefront
  (`Meta.base_manager_name = 'all_objects'` keeps FK traversal working).
- `Product.supplier` is schema-required, so each shop gets one hidden in-house
  `Supplier` row flagged `is_house_account`. `Supplier.objects` excludes house
  accounts, so they never appear in supplier lists / procurement / reports. It is a
  **data holder, never a login role** — the account stays single-role Enterprise.
- No supply chain behind an own product → no supplier cost, no stockist charge, no
  wholesale margin. The price the shop types **is** the customer price.
- The old supplier-side "Sell only in my own store" toggle is retired (a supplier
  account can never own a store now). `self_distribute_only` is kept on the model so
  historic rows keep their meaning.

Verified end-to-end: shop A's own product is invisible to the default manager, to
shop B's own-list and catalogue, to admin's product list, to the stockist inventory
and to the supplier product list; shop A's customer sees it and can add it to cart;
shop B's customer neither sees it nor can cart it (blocked); shop B gets 404 on
edit/delete. The own-product URLs 302 for every role except Enterprise.

**Verification:** `manage.py check` clean, **15/15 tests pass**, migrations applied
(`products.0016/0017`, `suppliers.0004/0005`), and a full crawl of **212 URLs × 7
roles = 1,484 requests → 0 HTTP 500s**. Mobile (375px): no horizontal overflow.

Also fixed in passing: two multi-line `{# … #}` template comments were leaking as
visible text (Django `{# #}` is single-line only) — switched to `{% comment %}`.

---

## Stage 5 — Hardening: backup script, real test suite, indexes (2026-07-27)

### 5a. backup.py (project root)
One-command full-project backup: stops this project's dev/prod server (matched
by /proc cwd so it never kills an unrelated one), checkpoints SQLite WAL, zips
everything INCLUDING db.sqlite3 / media / .env (it's a restore backup, not a git
export; only __pycache__/venv/.git/staticfiles are skipped), verifies the
archive CRC, and never prunes old backups (no-delete policy). Output:
`../Cart-backups/Cart_<timestamp>.zip`. Flags: `-o DIR`, `--keep-running`,
`--list`.

### 5b. Permanent test suite (was: throwaway scratchpad scripts)
- `accounts/factories.py` — shared `build_world()` fixture: every role, two
  rival shops, a customer under each, one shared marketplace product.
- `accounts/tests.py` (14) — single-role invariants (set_roles collapses to
  one, no zero-role accounts, role/roles columns stay in sync), switch-role URL
  gone, pick-role is a shim, customer_only gating per role, dashboards carry no
  profile link, no `{#`/`#}` leaks to rendered pages.
- `distributors/tests.py` (17) — own-product creation/pricing, cross-shop and
  upstream isolation (admin product list + stock + low-stock, stockist
  inventory, supplier list), customer of shop B blocked from carting, rival
  edit/toggle/delete → 404, owner delete is soft.
- `lakrish_mart/test_smoke.py` (1 big) — walks EVERY resolvable GET URL
  (200+) as all 7 roles, fails on any 5xx. The old crawl script, made permanent.
- settings.py: MD5 password hasher during `manage.py test` only (suite runs in
  ~44s instead of many minutes).
- **47 tests total, all green.**

### 5c. Three real bugs the new tests caught (all fixed)
1. **Global-pricing gate locked new shops out of own-products** — a brand-new
   Enterprise with no priced catalogue was redirected away from My Own Products.
   Fix: own_product_* views added to ALLOWED_BEFORE_GLOBAL_PRICING_SETUP
   (own products have no supply chain, global pricing is irrelevant to them).
2. **SKU collision on the 2nd own product** — `_generate_sku` sequenced via
   `Product.objects`, which can't see shop-private rows, so the next own product
   in the same category got a duplicate SKU → IntegrityError. Fix: sequence via
   `Product.all_objects`.
3. **Variant-level privacy leak** — pages that query ProductVariant directly
   (stockist inventory; admin stock / low-stock / CSV + Excel reports /
   dashboard counts) showed shop-private packs, because only Product had a
   filtered default manager. Fix: `ProductVariant.objects.shared()` helper +
   applied at all 7 call sites. NOTE: variant default manager is deliberately
   NOT filtered (a shop's own storefront reads `product.variants`); upstream
   surfaces must call `.shared()` — the isolation tests pin each one.

### 5d. Indexes (products.0018) — measured before adding, at 40k rows
- `product_shared_lookup_idx (status, is_deleted, owner_enterprise)` 9.3→0.7ms
- `product_owner_idx (owner_enterprise, is_deleted)` 1.2→0.05ms
- `variant_product_active_idx (product, is_active)` 16.4→3.9ms
- A candidate `(stock, is_active)` index made low-stock SLOWER (6.2→12.4ms) and
  was rejected. Don't add it back without re-measuring.

---

## Stage 6 — Rename tier "Enterprise" → "Marketplace" (2026-07-27)

User decision, revising Stage 1 (which had renamed this tier Distributor →
"Enterprise"). **UI-label-only, same as Stage 1**: code role stays
`distributor`, URLs/models/related_names untouched.

**Naming map now:**
- role `distributor` = UI **"Marketplace"** (a shop; was "Enterprise")
- role `wholesaler`  = UI **"Distributor"** (wholesale tier, unchanged)

Changed: ROLE_CHOICES display + ROLE_LABELS ("Marketplace Portal"), 119 template
replacements across 38 files + ~25 lowercase prose spots, admin messages,
help_texts/verbose_names (migrations accounts.0017 / distributors.0036 /
products.0019), and 33 code-comment mentions so the codebase speaks one language.
"Raj Enterprises" in seed data is a company NAME — left alone.

**Collision handled:** "marketplace" previously meant the SHARED product pool.
To keep the term unambiguous now that it names the shop tier:
- Admin's "Marketplace (View Only)" page → **"Catalogue Prices (View Only)"**
  (sidebar: "Catalogue (Prices)"); url_name `marketplace` unchanged (code).
- "supply the marketplace" helper text → "supply products to other shops".
- In code comments/docstrings the shared pool is now called the
  **"shared catalogue"** (products/models.py managers, own_product_views.py).

Verified: 47/47 tests green; sweep of 212 URLs × 7 roles → 0 pages containing
"enterprise" in any case, 0 HTTP 5xx; live check of register page + admin
user list + shop dashboard + wholesale dashboard.

---

## Stage 7 — Per-tier stock, stock rules, multi-stockist, direct supply (2026-07-27)

User spec (verbatim intent): every tier can keep stocks; manual stock changes
can only REDUCE (offline sales / own use) — never increase ("only based on the
order"); Marketplace own-products and Suppliers are the exception (they are
sources — free both ways); multiple stockists exist but one Distributor buys
from exactly ONE stockist; some products (eggs) go supplier → marketplace
directly with NO commissions for stockist or Distributor.

### 7a. Multiple stockists — one per Distributor
- `Wholesaler.stockist` FK (distributors.0037). Admin assigns it on the
  Distributor detail page. `stockist_for(marketplace)` in stockist/models.py
  resolves shop → its Distributor → its stockist (falls back to first active,
  so single-stockist installs need zero config). Replaced all 11
  `Stockist…first()` call sites (cart floor, checkout, storefront pricing,
  catalogue, auto-list).
- Routing: stockist "Marketplace Requests" + customer-order pickup lists show
  only requests/orders from marketplaces on THAT stockist's chain (unassigned
  chains visible to all as fallback). Tested: stockist B can neither see nor
  act on stockist A's request.

### 7b. Stock-rule enforcement (the core rule)
Manual stock edits may only REDUCE; increases arrive only via delivered orders:
- **Stockist** inventory grid: raising a qty is rejected with an explanation;
  the increase path is a StockistRestockRequest **fulfilled by the SUPPLIER**
  (new section on supplier restock page): supplier stock ↓, stockist stock ↑.
- **Distributor (wholesale)** NEW ledger `WholesalerStock` +
  `WholesalerRestockRequest` + "My Stock" page (/wholesale/my-stock/): view,
  manual reduce (clamped), request restock from ITS stockist; stockist has a
  deliver/reject section — delivery is the only increase path.
- **Marketplace** my_stock: per-row manual reduce (packed = FIFO batches,
  loose = grams); the old DIRECT loose-add (stock typed into existence) is
  REMOVED — loose is now a RestockRequest with `loose_grams` (distributors.0038)
  that lands in the loose pool only when delivered.
- **Own products** on my_stock: fixed to show the variant's real stock (was
  showing 0 from the empty DistributorStock row) and get a free "Set" control
  (both directions — the shop is their source, "a local own supplier"). The
  multi-role "you are both supplier and distributor" banner is gone; own rows
  no longer offer a chain Request button.
- **Supplier**: unchanged — sets own product stock freely (both directions).

### 7c. Direct supply (eggs) — products.0020 `Product.direct_supply`
- **THE STOCKIST HAS FULL CONTROL** (user decision 2026-07-27, revising the
  first cut where the supplier toggled it): new stockist page
  `/stockist/direct-supply/` ("Direct Supply" in the sidebar) lists every
  chain product with Set direct / Take into chain buttons. The supplier's
  form toggle was removed — supplier POSTs smuggling `direct_supply` are
  ignored (tested). Suppliers see a read-only DIRECT badge on their product
  list. Flag is global: a product is direct for everyone or for no one.
- `get_stockist_price` returns the SUPPLIER PRICE with empty breakdown — no
  stockist charges, no Distributor margin ("no commissions for both").
- Visible to shops/customers WITHOUT `charges_confirmed` (storefront listing,
  product detail, admin storefront preview, bulk pricing) — the stockist never
  touches these products, so they're excluded from stockist inventory, stockist
  restock, and the stockist's marketplace-request queue.
- Marketplace restock for a direct product is fulfilled by the SUPPLIER
  ("Deliver (direct)" on the supplier restock page): supplier stock ↓,
  marketplace DistributorStock batch ↑.

### 7d. Fixed in passing
- Shop-own product DETAIL page 404'd for the shop's own customers (default
  manager + charges gate) — now uses all_objects.visible_to_enterprise.
- `.table-wrap` had no overflow-x rule on dashboard pages (premium-ui.css) →
  wide tables scrolled the whole page on mobile. Rule added; page-level
  horizontal scroll gone (checked 507px viewport).

### 7g. Auto-restock (added same day, user request)
Opt-in per tier — Stockist, Distributor (wholesale) and Marketplace each get an
"Auto-restock when stock is low" card on their stock page: enable + low-stock
level + reorder quantity (fields auto_restock_enabled/threshold/quantity on
Stockist / Wholesaler / Distributor; stockist.0006, distributors.0039).
`stockist/auto_restock.py` files the tier's normal restock REQUEST when a row
dips to the threshold — it NEVER adds stock directly, so increase-only-by-order
still holds. One open request per variant (idempotent). Skips: own products
(shop is the source), direct-supply for stockist/wholesaler (never theirs);
marketplace direct-supply requests go to the supplier as usual.
Triggers: after every manual reduce (all 3 tiers), after customer checkout
consumes shop stock (transaction.on_commit), on settings save, and via
`python3 manage.py auto_restock` — add to server cron, e.g.
`0 6 * * * cd /path/to/project && python3 manage.py auto_restock`.

**High-value fix (2026-07-27, user request):** the global setting alone would
auto-order expensive items too. Two guards added, global settings kept as-is:
- **Price cap** `auto_restock_max_price` on all three profiles — "Skip items
  above ₹X (supplier price, 0 = no limit)" input in each settings card.
- **Per-item switch** `auto_restock_exempt` on StockistInventory /
  WholesalerStock / DistributorStock — an "auto ✓/✗" toggle on each stock row
  (stockist grid uses a named button inside the qty form; toggle is handled
  BEFORE the qty-save branch). Exempt rows are never auto-ordered regardless
  of settings. Migrations stockist.0007, distributors.0040. 6 new tests.

### Verification
- 20 + 3 (stockist direct-control) + 7 (auto-restock) tests in
  stockist/tests.py (stock rules per tier, routing, direct supply end-to-end) — **67/67 total green**, no migration drift.
- Crawl 213 URLs × 7 roles → 0 errors. Live checks: marketplace My Stock shows
  rules banner, own product with true stock and Set control; no console errors.

Deferred (not requested): customer-order fulfilment does not yet DRAW from
WholesalerStock (the ledger is for the Distributor's own bookkeeping);
supplier-side UI for editing direct-supply on existing products is the normal
product edit form.

---

## Removed features log

- **2026-07-28 — Supplier Earnings page** (`/supplier/earnings/`): user decision
  "not required". Removed entirely: `suppliers.views.earnings`, the
  `suppliers:earnings` URL, `templates/suppliers/earnings.html`, and its
  sidebar entry in `lakrish_mart/ui_config.py`. Supplier money surfaces that
  REMAIN: dashboard Total Earnings stat, Payout History, Payments Center,
  Settlement History, and the Telegram bot's earnings command (deliberately
  kept — separate surface).
- **2026-07-28 — Settlement History page** (`/payments/history/`): user asked
  if it duplicated the Payments Center. They were two halves of one thing
  (Center = active/actionable settlements, History = completed read-only), so
  History was MERGED into the Payments Center as a "Completed Settlements"
  section at the bottom (context key `completed_settlements`, capped 100,
  same role scoping + filters) and the separate page removed: `views.history`,
  the `payments:history` URL, `templates/payments/history.html`, the supplier
  nav entry, and the in-page link (now an anchor to the section).
- **2026-07-28 — Entire Telegram feature**: user decision "remove entire
  telegram option". Removed: `telegram_bot/` package (multi-role bot),
  `suppliers.views.telegram_link` + URL + `telegram_link.html`, the supplier
  nav entry, `run_bot` management command (and the now-empty
  suppliers/management scaffolding), `TELEGRAM_BOT_TOKEN` from settings/.env/
  .env.example/README, and `User.telegram_id` (migration accounts.0018 —
  column dropped with the feature; pre-removal DB exists in the Desktop
  backup zip). ACTION FOR USER: revoke the old bot token in @BotFather —
  the bot no longer runs but the token was live.
- Standing instruction (memory `remove-unneeded-code-fully`): a feature
  declared not required is deleted fully in one pass — no dead code.

---

## E2E exercise log — 2026-07-28 (create users + full transaction, via real forms)

Drove the platform end-to-end on the dev DB using the actual HTTP forms (no ORM
shortcuts). New chain created: supplier 9600000001, stockist 9600000002
("E2E City Stockist", via the dedicated admin page), wholesale Distributor
9600000003 (assigned to that stockist), Marketplace 9600000004 ("E2E Fresh
Store", created BY the Distributor), customer 9600000005 (self-registered with
the shop invite code). All passwords Test@12345.

Flow verified, all green: supplier adds product (E2E Sambar Powder, 2 packs) →
admin approves → stockist seeds stages, sets ₹5 global charge, releases →
shop hits the Global Pricing gate (correctly blocked), applies 20% global
pricing, lists → customer sees ₹78 (60+5 charge+20%), orders 2 → COD order
#26 ₹156 → confirmed → stockist pick → deliver → shop delivers. Payment row
'paid'; settlements auto-created with EXACT math (shop→stockist ₹130,
stockist→supplier ₹120). Restock chains: shop packed restock delivered (+10);
auto-restock fired at threshold (x12 request); stockist restock fulfilled by
supplier (inv +15, supplier stock −15); wholesaler restock delivered (+5).
Manual stock increase correctly refused on the new stockist.

Findings: NO real bugs. One UX gap fixed — the generic admin user-create form
deliberately excludes the stockist role (dedicated page collects company info)
but said nothing; it now links to "Create Stockist". Note: supplier form's
master-stock sync overwrites per-variant stocks when master total is set —
by design (master wins), just something to know when seeding.

The E2E entities remain in the dev DB for inspection.

---

## Stage 8 — RENT feature (PROPOSED 2026-07-28, not built yet)

User idea + answers captured; awaiting go-ahead after the interactive explainer.
- Rentable equipment (electrical hardware, cleaning machines, tools) INSIDE each
  Marketplace. Renters: customer, marketplace, Distributor only.
- Publishers: the shop itself (live immediately), its customers and its
  Distributor (both need the SHOP'S approval — shop is gatekeeper).
- Slot booking: day-granularity, min 1 day, multi-day; per-unit calendar, no
  overlapping bookings beyond item quantity.
- Customer pays in POINTS ONLY (existing per-shop wallet, 1pt=₹1); deduct at
  booking, auto-refund on reject/cancel-before-start. NO COMMISSION to the
  shop: customer-owned item → full points transfer to owner's wallet; shop's
  own item → points simply redeemed; Distributor's item → ₹ recorded, offline
  settle. Marketplace/Distributor as renters pay offline (recorded, owner
  marks received).
- Lifecycle: requested → confirmed → handed over → returned → closed.
- V1 excludes: hourly slots, late fees/damage penalties, guest rentals.
- Booking → a BANNER on the customer's home page (renter sees their booking,
  owner sees a "Confirm" prompt). Owner confirms from there.
- Owner (rent-giving customer) can BLOCK days they're away → item unbookable
  those days (block-out dates on the item's calendar).
- On booking, the renter's CONTACT is shared with the owner to call & discuss.
- Delivery & pickup are OFFLINE — explicitly NOT coded (no logistics feature).
- Every item carries a COST; on damage/loss the RENTER pays that cost, the
  marketplace is NOT responsible.
- Note: points supply — shops may need to raise points_earn_percent /
  welcome bonus so customers can afford rentals (existing knob).
- Interactive explainer kept in sync at each rule addition (artifact
  rent_plan.html); user is adding rules "one by one" before build.

### Stage 8 BUILD STARTED (2026-07-28) — user: "you can start enable to code"
**CRITICAL money-model correction (user 2026-07-28):** WALLET != reward points.
- WALLET = real money, loaded by paying the marketplace CASH (only method now).
  Rent is paid from the wallet. New app `rentals`: Wallet + WalletTransaction
  per (customer, distributor), mirroring reward shape but MONEY not points.
- REWARD POINTS = the existing separate system (auto after order + manual),
  untouched. The two never mix (test asserts a wallet load doesn't create a
  RewardPoints row).
**Master switch (user's explicit #1):** `SiteSettings.rentals_enabled` — the
ONLY global control. `rentals.services.rentals_enabled()` + `@rentals_on`
decorator gate every rent view; off → all rent pages 302 to home. Admin page
/rentals/admin/ toggles it (admin sidebar shows "Rentals · off" badge).
**Built (8.1 + core 8.2):**
- Models: RentalItem (owner_kind shop/customer/distributor, item_cost, units,
  status pending/approved/hidden, soft-delete), RentalBlock (owner away-days),
  RentalBooking (day-range, wallet-paid flag, damage_charged, lifecycle).
- services.py centralizes money + availability: credit/debit_wallet (no
  overspend), availability_reason (units + block-days), create_booking
  (customer debits wallet atomically), refund_booking, settle_return (owner
  gets FULL rent = zero commission; customer-owner → wallet, shop-owner keeps,
  damage → renter pays item_cost).
- Screens: admin switch+oversight; marketplace items/approval-queue/bookings/
  wallet-load(cash); customer browse/item+book/my-rentals/publish/block-days.
  Nav: admin sidebar, marketplace sidebar (gated), customer navbar+dropdown
  (all gated by site_settings.rentals_enabled).
- Migrations: rentals.0001, admin_panel.0003. INSTALLED_APPS += rentals.
- Tests: 16 in rentals/tests.py (switch gates all, wallet no-overspend, wallet
  vs points separation, no double-booking, block-day refuse, reject/cancel
  refund, zero-commission owner credit, damage charges renter, shop isolation,
  publish needs approval). 102/102 total green; crawl 223 urls x7 roles 0 err.
- Live E2E verified: switch off→302; admin on; shop adds item; loads ₹1000
  cash to customer wallet; customer books 3d ×₹200 → wallet 1000→400; confirm→
  handover→returned. Dev switch left OFF; E2E entities remain.
### Stage 8.2 DONE (2026-07-28) — Distributor side + owner controls
- Distributor (wholesale) rents from its shops & publishes into them:
  /rentals/dist/ (browse across its shops), /rentals/dist/<pk>/ (book OFFLINE —
  no wallet, amount recorded), /rentals/dist/my-bookings/, /rentals/dist/publish/
  (into a chosen shop, owner_kind='distributor', pending that shop's approval).
  Rentals button on the wholesaler dashboard (gated).
- Owner-confirms: a customer who LISTED items manages their bookings at
  /rentals/my-items-bookings/ (confirm/handover/return/damage); linked from
  customer rentals pages.
- Shop block-days on its OWN items: /rentals/shop/<pk>/blocks/ ("Block days"
  button on live shop-owned items). _handle_blocks shared by customer & shop.
- CORRECTNESS FIX: owner wallet credit now requires paid_from_wallet AND
  customer-owner — a business (offline) renter of a customer-owned item no
  longer phantom-credits the owner's wallet (test covers it).
- Tests: 19 in rentals (added distributor offline booking, publish-into-shop,
  business-renter no-phantom-credit). 105/105 total green; crawl 229 urls x7
  roles 0 err. Live E2E: Distributor published Scaffold Set into its shop →
  shop approved → Distributor booked 2d ₹700 offline (paid_from_wallet=False)
  → shop confirmed.
- **Stage 8 feature-complete for V1.** Out of scope (unchanged): hourly slots,
  late fees, guest rentals. Interactive plan page still says "points" for rent
  — reword to "wallet" on next docs pass.

Superseded pending note (kept for history): [Distributor screens + owner
block-days were the 8.2 remainder — now done above.]

## Stage 8.3 — Rent quality pass + master-switch safety (2026-07-28)

**Quality pass (/simplify, 4 agents):** applied — removed dead code
(`services.get_wallet`, `services.owner_distributor`); `_committed_counts` uses
`values_list`; `admin_rentals` wallet total is a DB `Sum` aggregate; the
derivable `RentalBooking.paid_from_wallet` field became a property (migration
rentals.0002 drops the column); `owner_gets_wallet_credit` is a property;
shared `parse_decimal`/`parse_int`/`parse_day` added to accounts/utils.py and
reused (rentals dropped its local copies); the three publish forms collapse to
`_save_item_from_post`, the two renter-booking POSTs to `_place_booking`; the
whole booking lifecycle now lives in ONE `services.transition_booking(booking,
action)` state machine (views no longer hand-roll status writes). Skipped
(reasoned): `amount` kept as a money snapshot; per-view `@rentals_on` kept over
middleware (explicit/greppable); `admin_required` re-declared via the shared
factory (avoids a heavy import); the availability "row lock" comment corrected
to say best-effort rather than adding select_for_update (a concurrency change,
out of a simplify pass's scope).

**Master-switch safety (user question — turning off while rentals active):**
turning OFF now stops NEW rentals only. The four booking-management pages
(shop_bookings, customer_bookings, customer_owned_bookings, dist_bookings) are
deliberately NOT gated by @rentals_on, so any in-flight booking can still be
confirmed / returned / cancelled (with wallet refunds) after the switch is off
— nothing and no wallet money gets stranded. Admin sees an "In progress" count
and a warning naming how many bookings remain open when disabling. Tests:
`test_off_still_allows_closing_out_bookings` + updated
`test_off_blocks_new_rental_pages`. 106/106 total green; live-verified (booking
blocked when off, open booking still cancellable + refunded).

## Rent — "My Rentals" tab in customer account (2026-07-28)
- Removed "Rentals" from the customer TOP navbar (kept only in the avatar
  dropdown → Rentals, which opens the browse/book page). A customer books a
  rental (incl. items OTHER customers listed, once the shop approves them) from
  that browse page /rentals/.
- Added "My Rentals" as a tab in the customer account sidebar (Personal Details
  / My Account), → rentals:customer_bookings, gated by site_settings.rentals_enabled.
- Dedup: the account sidebar was copy-pasted across profile/addresses/my_rewards
  and had DRIFTED (addresses was missing Change Password + Reward Points).
  Extracted to templates/accounts/partials/account_sidebar.html (one `active`
  param); all three now share it, fixing the drift and adding the tab once.

## Suggested price (no-MRP-sticker goods) — 2026-07-28
- New per-variant `ProductVariant.suggested_price` + product flag
  `Product.show_suggested_price` (products.0021). For goods with NO printed MRP,
  the supplier sets a *suggested* selling price so the shop keeps prices low.
- **Guidance, not a cap.** Unlike MRP (hard `max`, "cannot exceed"), the shop
  CAN price above the suggested value — the catalogue shows an info badge
  "₹X suggested" and a soft yellow warning ("above suggested ₹X"), never blocks
  and never enforced, never shown to customers as an MRP.
- **Mutually exclusive with MRP in the add/edit form** — a product has a printed
  MRP *or* a suggested price, never both; turning one toggle on unchecks the
  other. Rides the same variant formset (`variants-N-suggested_price`) as MRP.
- Shop sees it on /distributor/catalogue/ (the Marketplace pricing page), MRP
  column relabelled "MRP / Suggested". Tests: suppliers/tests.py
  SuggestedPriceTests (save round-trip + flag-off).
- Live-search box also added to /supplier/stock/ (client-side type-to-filter).

## Price-flow verification + manual-price bug fix — 2026-07-28
- Verified the rupee value at EVERY supply stage with a real end-to-end trace
  (stockist/test_price_flow_trace.py, run -v2 to see it):
  Supplier ₹200 → +Stockist charges (₹15 fixed + 10%) ₹235 → +Distributor
  margin (₹8/kg) ₹243 (= marketplace TRUE COST / floor) → Marketplace sells
  ₹250 (margin ₹7). MRP cap: ₹300→₹260. Floor: ₹100→₹243. Direct-supply eggs:
  ₹90→₹90, zero commission (stockist + distributor both skipped). Uses the REAL
  get_stockist_price() and the REAL save_product_listing view.
- Pricing pipeline confirmed: stockist charge priority product>supplier>global;
  distributor margin by weight/qty only (never flat %); shop price clamped
  min(sell, MRP) then max(sell, base) — floor wins if MRP<cost (degenerate).
  Shop pricing pages are gated until the shop configures Global Pricing once.
- BUG FIXED (was corrupting the flow): product_add ran sync_variant_stocks()
  WITHOUT the `pricing_mode=='auto'` guard that product_edit has, so a supplier
  adding a product in MANUAL mode had every hand-typed pack price silently
  overwritten by the auto price. One-line guard added; regression test
  suppliers/tests.py::test_manual_prices_not_overwritten_on_add.
## Product save-path data-loss fixes — 2026-07-28
Fixed three defects the Add-Product form audit found (all in suppliers/views.py
+ product_form.html; tests in suppliers/tests.py::VariantSaveIntegrityTests):
- **product_edit no longer hard-deletes variants.** It used to
  `ProductVariant.objects.filter(product=product).delete()` on EVERY save then
  recreate with new pks — which CASCADE-wiped every DistributorVariantPrice,
  DistributorStock, VariantPriceHistory, stockist inventory, cart item and
  transaction pointing at the pack, and SET_NULL'd order lines. Now it
  VALIDATES the new pack set first (invalid → saves nothing, no data loss) then
  reconciles by unit_grams: update matching packs IN PLACE (pk preserved →
  relations intact), create genuinely new ones, and DEACTIVATE (is_active=False)
  removed packs — never hard-delete. Whole edit is transaction.atomic.
  Honours [[no-hard-delete-policy]].
- **product_add won't save a zombie product.** Now requires
  `form.is_valid() AND formset.is_valid()` before persisting — an empty/invalid
  pack set saves nothing and re-renders with errors (was: product saved with no
  packs).
- **Removed the 0.5g pack** from SIZE_TREE (product_form.html) — unit_grams is a
  whole-number field, so 0.5 invalidated the whole formset. All sizes are now
  integers.

---

## How we work together (process notes)

- Keep this log updated as we discuss; mark new points as "noted".
- Reconcile every new request against this log first.
- If a request conflicts with a prior decision, **flag it and cite the prior
  decision** — do not proceed blindly, and do not re-ask something already settled.
- Only change a settled decision when the user explicitly says to.

---

## UI glitch fixes — 2026-10-29

Static UI-integrity pass over customer-facing templates (known glitch classes:
leaking `{# #}` multi-line comments, missing CSRF, dead theme vars, misleading
controls). `{# #}` comments were all single-line (clean); POST forms all carry
`{% csrf_token %}`. Five concrete glitches found and fixed:

1. **Storefront bottom nav "Account" link** (`templates/products/storefront.html`)
   always pointed at `accounts:login`, so a logged-in customer tapping Account
   was sent to the login page. Now → `accounts:profile` when authenticated,
   `accounts:login` as a guest (label changes to "Login").
2. **Storefront bottom-nav cart badge missing** — `modern-shop.css` defines a
   `.ms-cart-count` badge but the storefront never rendered it. Added the badge
   (bound to `cart_count` via `data-cart-badge`, hidden at 0) so it matches the
   top-nav cart count.
3. **Misleading "ADD" button** on storefront tiles — the `.ms-add` control looked
   like a grocery-app add-to-cart button but only `href`s to the product detail
   page. Relabelled **"VIEW"** to match its real behaviour (adding needs the
   variant flow on the detail page).
4. **Dead distributor theme colour on the storefront hero** — `modern-shop.css`
   forced the hero gradient green with `!important`, overriding the inline
   `--theme-color`. Gradient now starts from `var(--theme-color, var(--ms-green))`
   so a distributor's brand colour shows again.
5. **Guest cart "Continue/Start Shopping" lost store context**
   (`templates/cart/cart.html`) — both linked to `products:product_list`, which
   redirects a guest OUT (to login/home). For guests they now link to
   `products:home`, which correctly returns a guest-with-store-context to their
   shop. Logged-in customers keep `products:product_list`.

**Verification:** fresh Windows venv (uv, py3.11); `manage.py check` clean;
**118/118 tests pass** (incl. the full-URL smoke crawl across 7 roles — renders
the edited storefront + cart with 0 HTTP 5xx). Note: the 2 price-flow *trace*
tests only "fail" when the Windows console is cp1252 (they print the ₹ glyph);
with `PYTHONUTF8=1` they pass — not a code issue. Temp `winvenv/` was gitignored
(a stale OS file lock blocked auto-delete; remove it manually when convenient).

---

## Project-wide quality cleanup — 2026-10-31

A full static + runtime quality pass covered Django views/URLs/models, templates,
CSS/JS, scripts, test/operation docs, and the standalone URL smoke tool. A
verified restore backup was taken first and retained at
`../Cart-backups/Cart_2026-10-31_094639.zip` (37.8 MB; server kept online).

### Correctness and safety fixes
- **No state changes by GET:** admin product approve/reject/delete/restore,
  supplier suspend/activate, Marketplace suspend/activate, Distributor
  suspend/activate, commission-rule actions, category delete route, and
  user restore now require CSRF-protected POSTs. The admin templates now submit
  forms, not state-changing links. Product row actions safely reuse the existing
  bulk-form CSRF token via `formaction`; `datatable.js` now recognizes these as
  individual actions instead of treating them as bulk actions.
- **No hard delete from normal admin flows:** Marketplace delete now archives by
  suspension (all customers, orders, pricing, inventory and settings survive;
  admin can reactivate). User delete is soft-delete for every role; removed the
  hard-delete endpoint/buttons/bulk action. Commission rule "delete" now retires
  the rule (`is_active=False`) and it can be re-enabled. Category deletion is
  disabled until a proper archive lifecycle is built, avoiding irreversible data
  loss. This reconciles the UI with the no-hard-delete policy.
- **Lifecycle consistency:** every user delete route uses `User.soft_delete()` so
  `deleted_at` + `deleted_by` are always captured; transactions product
  delete/restore now call `Product.soft_delete()` / `restore()` (correctly
  changes status) and product detail explicitly excludes deleted products;
  transaction role changes now atomically provision the profile required by the
  destination Supplier / Marketplace / Distributor role.
- **Deleted users remain recoverable:** the earlier fix using `User.all_objects`
  on deleted-list/recovery paths was retained; deleted users are visible,
  viewable and restorable in admin.

### UI / frontend fixes
- **Repaired `static/css/premium-ui.css`:** its motion/profile/card/button rules
  were syntactically corrupted, causing browsers to discard declarations.
  Rebuilt the block as valid CSS, preserving the intended visual behavior.
- Removed the duplicate copy of `datatable.js`, prevented column preferences
  from hiding every table column, excluded data-table cards from scroll-reveal,
  and added a reveal safety-net. Updated dashboard/base CSS + JS cache versions
  so browsers fetch the repaired assets.
- Fixed the malformed Auto List spinner attribute in the Marketplace catalogue,
  the Customers-list Clear link (preserves the Suspended Marketplace filter),
  and its conditional empty-table `colspan`. Removed unused
  `.table-responsive-stack` shared CSS.

### Scripts / operations cleanup
- `scripts/minimal_seed.py` and `scripts/mass_seed_scale.py` are now guarded:
  they refuse to run unless both `LAKRISH_DB_MODE=demo` and
  `LAKRISH_ALLOW_DESTRUCTIVE_SEED=YES` are explicitly set. The mass seed no
  longer creates impossible multi-role accounts.
- Rebuilt `scripts/setup_smoke_users.py` around the single-role model: one
  dedicated account + required profile for customer, supplier, Marketplace,
  stockist, wholesale Distributor, and admin.
- Repaired `scripts/url_smoke_audit.py`: it now starts from the project root,
  covers guest + all six authenticated roles (including wholesale Distributor),
  and exits non-zero if any role fixture is missing instead of silently skipping
  it.
- `backup.py` now leaves servers running by default; `--stop-server` is explicit
  and documented as Linux `/proc` only. Backup ignores local `runvenv` /
  `winvenv` directories. Standardized seed instructions on `venv\\Scripts` and
  corrected the PowerShell command for `full_test_seed.py`. Ignored the generated
  transaction-flow report.

### Verification
- `manage.py check`: clean.
- `makemigrations --check --dry-run`: no migration drift.
- Python `compileall`: clean across all apps, scripts and backup utility.
- Full test suite: **118/118 passed**.
- Standalone URL audit: **402 named URLs reversed, 0 reverse failures, 0 HTTP
  500s** for guest + customer, supplier, Marketplace, wholesale Distributor,
  stockist, and admin. Expected 405 responses now prove state-changing URLs
  reject unsafe GET requests.
- JavaScript syntax was manually reviewed; Node.js is not installed on this
  Windows machine, so automated `node --check` was unavailable.

---

## COD-only + payment close-out + integrity audit — 2026-10-31

### Payments are COD-only now
- Checkout removed the UPI option; the only payment method shown is Cash on
  Delivery. `orders.views.checkout` forces `payment_method='cod'` server-side
  and ignores any other posted value (COD stays 'pending' at order creation).
- `Payment.METHOD_CHOICES` keeps 'upi'/'card' relabelled "(legacy)" only so any
  historical rows still render; no new order can use them. Migration
  payments.0006.
- The separate stockist↔supplier `ProcurementPayment` system (cash/upi/bank/
  cheque) is untouched — that is B2B billing, not customer checkout.

### COD close-out on delivery
- When a shop marks an order delivered (`delivery.views.mark_delivered`), a
  pending COD payment is now set to 'paid' with `paid_at` + a "COD collected on
  delivery by <user>" note, and tier settlements re-sync. Previously COD orders
  stayed 'pending' forever. Verified: delivering an order flips its payment to
  paid with a timestamp. Backfilled 1 pre-existing delivered order.

### manage.py audit_integrity (NEW, read-only)
`admin_panel/management/commands/audit_integrity.py` scans for silent bad states
that return no error and pass the smoke test:
  1. customers with orders but no shop link
  2. orders with no distributor
  3. soft-deleted users missing deleted_at/deleted_by
  4. soft-deleted products missing deleted_at
  5. approved products never released by the stockist
  6. shop selling price below the cost floor (selling at a loss)
  7. order-timeline template referencing a status that doesn't exist
     (guards the admin_picked-style bug class)
  8. non-COD customer payments
  9. delivered orders with COD still pending
Read-only; exits non-zero on error-level findings so it can gate CI/pre-deploy.
Run: `venv\\Scripts\\python.exe manage.py audit_integrity`.

Verified: 118/118 tests pass, system check clean, audit clean except one
informational leftover (test product #35 unreleased).

---

## Product template library (reuse) for Marketplace own-products — 2026-10-31

**Need:** in a multi-marketplace system, many shops sell the same common items
(soap, shampoo, rice, dal, eggs, water, biscuits...). Instead of each shop
re-typing the same product, a shop can reuse a shared template.

**Design decisions (AI-made, for multi-marketplace):**
- New `ProductTemplate` + `ProductTemplateVariant` library in products/models.py
  (brand, name, category, image, default packs with *suggested* prices,
  use_count, source admin/shop/supplier). Unique on (brand, name).
- **Reuse = independent copy.** Picking a template pre-fills the own-product
  form; the shop edits price/stock and saves a product PRIVATE to that shop
  (owner_enterprise). No link back to the template, so one shop's edits never
  affect another — preserves shop isolation.
- **Library grows two ways:** admin-curated (Django admin + `seed_templates`
  command, 18 starter items) AND auto-contributed whenever a shop saves an
  own-product (`_contribute_template`, deduped by name; prices stored only as
  suggestions). Images copied, not shared.
- Scope: Marketplace own-product screens only. Suppliers unchanged.

**Surfaces added (all under `/distributor/my-products/`):**
- `template_search` — type-to-search JSON picker on the Add-product form;
  choosing one pre-fills name/category/image/packs, then the shop sets its price.
- `template_library` — browse + **bulk import** many templates into the store in
  one action (each becomes an own-product; dedupes against existing names).
- Django admin: ProductTemplate with inline variants (admin curation).
- Management command `seed_templates` (idempotent).

**Verified:** migration products.0022 applied; search/library/bulk-import/
auto-contribution all work via the real views; `makemigrations --check` clean;
118/118 tests pass; system check clean.

---

## Pricing-chain transparency + chain-integrity audit — 2026-10-31

Confirmed the supplier → stockist → wholesale Distributor → shop price chain
works end-to-end (verified a real product: Supplier Rs.80 + Packing Rs.5 +
Handling Rs.3.20 = floor; wholesale margin applied only for ACTIVE wholesalers,
correctly skipped for a suspended one). Added two things on top:

### 1. Chain-integrity checks in `manage.py audit_integrity`
Five new supply-chain checks:
  10. active shops linked to a SUSPENDED wholesale Distributor (margin silently
      lost) — flagged Anna Nagar + Velachery under suspended Chennai Wholesale.
  11. active wholesale Distributors with no stockist assigned.
  12. active wholesale Distributors with margin_value = 0 (likely unconfigured).
  13. active shops that price products but have NO stockist resolvable (ERROR).
  14. active shop prices flagged stale (supplier changed price, not re-priced).

### 2. Admin "Price Trace" page  (/admin-panel/price-trace/)
Pick any Marketplace + one of its products and see the price built stage by
stage: supplier price → + each stockist charge → + wholesale Distributor margin
→ cost floor → MRP cap → shop selling price, per pack. Highlights when a shop
sells BELOW floor (loss) and warns when the shop's wholesaler is suspended or no
stockist resolves. Read-only; uses the real `get_stockist_price()` breakdown.
Sidebar: Admin Panel → "Price Trace". View `admin_panel.views.price_trace`.

Verified: system check clean, no migration drift, 118/118 tests pass; audit
catches the real suspended-wholesaler + zero-margin cases; price-trace renders
the full breakdown and the suspended-wholesaler warning.

---

## Partial delivery + per-item status + partial COD — 2026-10-31

### Problem
For a multi-item order, items could be at different stages (some at stockist,
some at the shop, some being delivered). The customer saw only one order-level
timeline with no per-item visibility. Partial deliveries were also not supported
— the whole order had to move as one unit, and COD couldn't be partially
collected on a first batch.

### What was built

**1. Per-item fulfillment status on the customer order page**
The items table now shows a status badge per item (Delivered ✓ / On the way /
Coming soon / Preparing) using the existing `OrderItem.fulfillment_status` field.
Works on both desktop table and mobile card. Also shows partial payment info
("₹98 collected · ₹232 pending") in the Order Info panel when applicable.

**2. Partial delivery flow for the shop (`/delivery/<id>/partial-deliver/`)**
The shop's delivery detail page now shows a checkbox per item. The shop selects
which items they have with them on this trip, submits, and the view:
- marks those items `fulfillment_status='delivered'`
- creates a `PaymentInstalment` record for the collected cash
- bumps `Payment.amount_collected`
- sets order to `partially_delivered` (or `delivered` when all items done)
- adds delivery charge to the last batch so the total collected always equals
  the order total
- flips `Payment.status` to `paid` when `amount_collected >= amount`
- syncs ledger + settlements

The shop can also still "Mark ALL as delivered" in one action (full delivery).

**3. COD Payment Instalment tracking**
New model `PaymentInstalment` (payments/models.py) — one row per cash-collection
event. New field `Payment.amount_collected` tracks the running total. New
`Payment.amount_outstanding` property. New `OrderItem.delivery_instalment` FK
links each item to the instalment in which it was collected.

**4. Delivery detail page rebuilt** (templates/delivery/delivery_detail.html)
Full redesign: item checkboxes for partial delivery, per-item status badges,
COD collection history table, partial vs full delivery actions, payment summary.

**Migrations applied:** payments.0007, orders.0011

**Verified:** 118/118 tests pass, system check clean, no migration drift.
End-to-end: 2-item order, deliver item 1 → `partially_delivered`, Rs.98/330
collected; deliver item 2 → `delivered`, Rs.330/330, payment `paid`, 2
instalments recorded.

---

## 3rd-eye overnight audit — 2026-11-01

Ran a full autonomous audit while the user was away. 12 checks across all roles
and every transaction type. Final result: **49 passed, 0 errors, 3 warnings**.

### Transactions exercised
- Customer registration via shop invite (live server) — OK
- Two-step login — OK
- Cart → checkout → order creation — OK
- Full lifecycle: pending → confirmed → stockist_picked → at_distributor →
  out_for_delivery → delivered — OK
- COD auto-close on delivery — OK
- Partial delivery (2-item order, batch 1 then batch 2) with COD instalment
  collection — OK
- All 11 admin pages (HTTP 200, no template leaks) — OK
- All 6 shop pages — OK
- All 3 supplier pages — OK
- Product template library search — OK
- COD-only enforcement — OK

### Fix applied during audit
- `mark_delivered` (full delivery) was not setting `amount_collected` on the
  Payment row. Fixed — now sets `amount_collected = amount` when the full order
  is delivered, keeping it consistent with the partial-delivery path.
- Backfilled 1 delivered order with pending COD (Order #35, legacy from earlier).

### Warnings (not errors — require a decision, not a code fix)
1. **Anna Nagar Organics** and **Velachery Fresh Store** are linked to
   **Chennai Wholesale Distributors (suspended)**. Their Distributor tier margin
   is not applied to the cost floor while the Distributor is suspended. Action:
   reactivate Chennai Wholesale Distributors or reassign these two shops.
2. Same issue flagged in the price chain check.
3. `new test (#35)` — an old test product that was never released by the
   stockist. Harmless; can be soft-deleted if no longer needed.

### Housekeeping done
- 34 old request log files deleted (kept last 7). Auto-rotation added to
  settings.py so future server starts clean old logs automatically (keeps 7).
- Audit script removed after use.
- 118/118 tests pass. System check clean. No migration drift.

---

## Deep 3rd-eye audit + full cleanup — 2026-11-01

### Cleanup done
- Deleted all test/audit accounts (9321000001, 9400*, Txn One) and their
  associated orders, payments, instalments, and delivery records.
- Final clean state: 37 suppliers, 2 stockists, 4 shops, 59 products,
  12 customers, 18 orders (6 paid, 12 pending). All real data.

### Deep audit — 12 transaction types, 33 passed, 0 errors
Covered every major operation:
  A. Admin creates a new supplier account ✓
  B. Supplier adds product (2 packs) → admin approves → stockist releases ✓
  C. Shop lists + prices the new product (verifies price ≥ floor) ✓
  D. Admin adds a new customer linked to a shop ✓
  E. Customer places order (new product, 2 units) ✓
  F. Full lifecycle: pending→confirmed→stockist_picked→at_distributor→
     out_for_delivery→delivered, COD fully paid, customer timeline 6/6 done ✓
  G. Partial delivery: 2-item order, batch 1 then batch 2, COD instalments ✓
  H. Shop own product via template picker (Lux Soap template), shop-isolation
     confirmed (not visible to other shop) ✓
  I. Integrity audit: 0 error-level ✓
  J. All 5 role dashboards: admin, supplier, stockist, shop, customer → 200 ✓
  K. Price chain: all 40 checked prices ≥ floor for all 4 shops ✓
  L. Request log active ✓

### Remaining warnings (data decisions, not code bugs)
- Anna Nagar + Velachery under suspended Distributor (Chennai Wholesale
  Distributors). Their margin is silently skipped. Needs owner decision:
  reactivate the Distributor or reassign shops to an active one.

### Also fixed
- `mark_delivered` (full delivery) was not setting `amount_collected` —
  fixed in delivery/views.py; now sets amount_collected=amount on full delivery.
- Auto-rotation of request logs: settings.py now keeps last 7, deletes the rest
  automatically at each server start.
- 118/118 tests pass. System check clean.

---

## Maximum activity logging added — 2026-11-01

Added a second log file `logs/activity.log` (rotating, max 10 MB, 5 backups)
that records every meaningful business event — not just HTTP requests.

### New file: `lakrish_mart/activity_logger.py`
A central `act` singleton with typed log helpers:
  - `act.order(event, order, by, detail)` — order status changes
  - `act.payment(event, order, amount, by)` — COD collection events
  - `act.user(event, user, by, detail)` — registration, soft-delete
  - `act.product(event, product, by, detail)` — approve/reject/submit
  - `act.stock(event, variant, qty, by)` — stock changes
  - `act.price(event, ...)` — price changes
  - `act.cart(event, user, variant, qty)` — add/remove
  - `act.auth(event, username, role, ip)` — login/logout/fail
  - `act.info/warn/error(msg)` — general messages

### Views wired with act. calls:
  - `orders/views.py` — order placed
  - `delivery/views.py` — out_for_delivery, delivered, partial delivery, COD collected
  - `distributors/views.py` — shop confirms/cancels/delivers order
  - `stockist/views.py` — stockist pick/deliver
  - `accounts/views.py` — login success/failure, customer registration
  - `cart/views.py` — add to cart
  - `suppliers/views.py` — product submitted for approval
  - `admin_panel/views.py` — product approved/rejected

### Log format (activity.log):
```
[timestamp] INFO activity ORDER | PLACED | #45 | status=pending | total=Rs.226 | shop=Anna Nagar | cust=9100000030 | by=9100000030(customer) | items=1
[timestamp] INFO activity CART  | ADDED  | 9100000030(customer) | Turmeric [100g] qty=2
[timestamp] INFO activity AUTH  | LOGIN_SUCCESS | 9578727271 | role=customer | ip=192.168.0.1
```

### Verified: 118/118 tests pass. System check clean.
Real events already appearing in activity.log (cart add, product submissions).

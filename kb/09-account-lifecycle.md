# 09 — Account Lifecycle (Suspend → Delete → Hard-delete)

The full lifecycle of a partner account (Supplier, Stockist, Distributor,
Marketplace) and of Customers: how an admin suspends, soft-deletes, and
eventually permanently archives an account, and how the grace-period purge
works.

> **This document is the source of truth for the account lifecycle.** Any change
> to these rules must be agreed against this file first. If a future request
> conflicts with what is written here, raise the conflict before changing code.
> Suspension-specific login/visibility rules live in `08-supplier-suspension.md`.

---

## Naming reminder (code vs UI)

| UI label | Code model | status values |
|---|---|---|
| Supplier | `suppliers.Supplier` | `approved` / `suspended` |
| Stockist | `stockist.Stockist` | `active` / `suspended` |
| Distributor | `distributors.Wholesaler` | `active` / `suspended` |
| Marketplace / Shop | `distributors.Distributor` | `active` / `suspended` |
| Customer | a `User` (role `customer`) | — (uses is_active/is_deleted) |

The soft-delete / purge state lives on the linked **`accounts.User`**, not the
profile, so every role shares one mechanism.

---

## The four stages

Tabs on each admin list page: **Active · Suspended · Deleted · All**

```
 Active ──suspend──▶ Suspended ──delete──▶ Deleted ──delete again──▶ (purge-due)
   ▲                    │                     │                          │
   └──── activate ◀──────┘        restore ◀────┘            grace period elapses
                                                                         │
                                                                         ▼
                                                              Hard-deleted (purged)
```

1. **Active** — normal operation.
2. **Suspended** — `profile.status='suspended'`. Cannot log in (see KB 08).
   Reversible with Activate. Products/stock drain per KB 08.
3. **Deleted (soft)** — admin deletes from the Suspended list. Sets on the
   user: `is_deleted=True`, `deleted_at=now`, `is_active=False`. Hidden from
   Active/Suspended lists and from login. Appears under the **Deleted** tab.
   Fully **restorable**.
4. **Hard-deleted (purged)** — admin clicks "Delete permanently" on the Deleted
   list, OR the grace period elapses. Sets `is_purged=True`, `purged_at=now`.
   Permanent archive: never restorable, hidden everywhere.

### Why hard delete is an archive flag, not a physical row delete

A partner/customer with past orders, payments and stock history cannot be
physically removed without corrupting order history, financial ledgers and
reports (cascading foreign keys). So "hard delete" = permanent archive
(`is_purged=True`): the row stays so historical integrity holds, but the account
is gone for every practical purpose and can never be restored.

---

## Soft delete — field contract (on `accounts.User`)

| Field | Meaning |
|---|---|
| `is_deleted` (bool) | Soft-deleted. `User.objects` hides these; `User.all_objects` sees them. |
| `deleted_at` (datetime) | When soft-deleted. Start of the grace clock. |
| `deleted_by` (str) | Who deleted it. |
| `is_purged` (bool) | Permanently archived (hard-deleted). Never restorable. |
| `purged_at` (datetime) | When purged. |

`User.soft_delete()` / `User.restore()` already exist. A new `User.purge()`
sets `is_purged=True`, `purged_at=now` (and keeps `is_deleted=True`).

Partner list queries:
- **Active / Suspended / All tabs:** `user__is_deleted=False` (+ status filter).
- **Deleted tab:** `user__is_deleted=True, user__is_purged=False`.
- Purged rows never appear in any admin list.

---

## The grace period + purge job

- **Setting:** `SiteSettings.account_hard_delete_after_days` (default **2**),
  editable in admin settings. A soft-deleted account becomes purge-eligible when
  `deleted_at + N days <= now`.
- **"Delete permanently" button** on the Deleted page can purge immediately
  (admin override), OR the account purges automatically once the grace period
  elapses.
- **Purge runs two ways** (either is enough; both are safe to run repeatedly):
  1. Management command `purge_deleted_accounts` — run on a schedule
     (cron / Windows Task Scheduler). Purges every account whose grace period
     has elapsed.
  2. Lazy call — when an admin opens a Deleted list page, the view runs the same
     purge first, so it works even with no scheduler configured.

The purge only sets `is_purged=True`; it never issues SQL `DELETE`.

---

## Customers (managed by the Marketplace)

- Customers are created/managed by their Marketplace (shop), not admin.
- A Marketplace can soft-delete its own customers (`User.soft_delete`).
- Customer accounts follow the **same grace-period auto-purge**: once
  `deleted_at + account_hard_delete_after_days` elapses, the purge job sets
  `is_purged=True`. No manual admin step is required for customers.

---

## Per-stage capability summary

| Stage | Can log in? | Shows in catalogues? | Restorable? | Admin actions available |
|---|---|---|---|---|
| Active | Yes | Yes | — | Suspend |
| Suspended | No | Per KB 08 (buffer drain) | — | Activate, Delete |
| Deleted (soft) | No | No | **Yes** | Restore, Delete permanently |
| Hard-deleted (purged) | No | No | No | none |

---

## Where this is implemented

| Concern | Location |
|---|---|
| Grace-period setting | `admin_panel/models.py :: SiteSettings.account_hard_delete_after_days` |
| Soft-delete / restore / purge on user | `accounts/models.py :: User.soft_delete/restore/purge` |
| Deleted tab + actions (all 4 partner roles) | common template `admin_panel/partner_list_base.html` + the four `*_list` views |
| Soft-delete / restore / purge partner actions | `admin_panel/views.py` |
| Purge command | `admin_panel/management/commands/purge_deleted_accounts.py` |
| Lazy purge on Deleted page | the `*_list` views |
| Customer delete (by marketplace) + auto-purge | distributor/marketplace customer views + the purge command |

---

## Out of scope / decided

- No physical row deletion of accounts with history — hard delete = `is_purged` archive.
- Grace period is a single global setting (not per-role) for now.
- Purge is idempotent and safe to run repeatedly.

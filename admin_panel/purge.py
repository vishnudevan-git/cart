"""Account grace-period purge (hard delete = permanent archive).

Source of truth: kb/09-account-lifecycle.md.

A soft-deleted account (User.is_deleted=True, not yet purged) becomes
purge-eligible once ``deleted_at + SiteSettings.account_hard_delete_after_days``
is in the past. Purging sets ``is_purged=True`` / ``purged_at=now`` — it never
issues a physical DELETE, so order/financial history stays intact.

Safe to run repeatedly (idempotent). Called both by the management command
``purge_deleted_accounts`` and lazily when an admin opens a Deleted list page.
"""
from __future__ import annotations

from datetime import timedelta

from django.utils import timezone


def purge_due_accounts():
    """Permanently archive every soft-deleted account whose grace period has
    elapsed. Returns the number of accounts purged."""
    from accounts.models import User
    from admin_panel.models import SiteSettings

    days = SiteSettings.get().account_hard_delete_after_days
    cutoff = timezone.now() - timedelta(days=days)

    due = User.all_objects.filter(
        is_deleted=True,
        is_purged=False,
        deleted_at__isnull=False,
        deleted_at__lte=cutoff,
    )
    from lakrish_mart.activity_logger import act
    count = 0
    for user in due:
        user.purge()
        act.user('hard_deleted', user=user, by='system(grace-period purge)',
                 detail='auto purge after grace period')
        count += 1
    if count:
        act.info(f'PURGE JOB | auto-archived {count} account(s) past grace period')
    return count

"""Permanently archive soft-deleted accounts past their grace period.

Run on a schedule (cron / Windows Task Scheduler), e.g. daily:
    python manage.py purge_deleted_accounts

Hard delete here means a permanent archive flag (is_purged), never a physical
row delete — see kb/09-account-lifecycle.md. Safe to run repeatedly.
"""
from django.core.management.base import BaseCommand

from admin_panel.purge import purge_due_accounts


class Command(BaseCommand):
    help = 'Permanently archive (hard-delete) soft-deleted accounts past the grace period.'

    def handle(self, *args, **options):
        count = purge_due_accounts()
        self.stdout.write(self.style.SUCCESS(f'Purged {count} account(s) past the grace period.'))

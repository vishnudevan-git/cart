"""Settle rentals whose time is up — pay owners for finished rentals and
auto-refund requests the owner never confirmed. Safe to run repeatedly.

Cron example:  0 1 * * *  cd /path/to/project && python3 manage.py settle_rentals
(The booking pages also settle lazily on view, so this is a safety net.)
"""
from django.core.management.base import BaseCommand

from rentals.services import settle_due


class Command(BaseCommand):
    help = 'Pay owners for finished rentals; auto-refund unconfirmed past-start requests.'

    def handle(self, *args, **options):
        completed, expired = settle_due()
        self.stdout.write(f'Settled {completed} completed rental(s); '
                          f'auto-refunded {expired} unconfirmed request(s).')

"""Nightly sweep: file auto-restock requests for every tier that opted in.

Cron example (server):  0 6 * * *  cd /path/to/project && python3 manage.py auto_restock
"""
from django.core.management.base import BaseCommand

from stockist.auto_restock import run_all


class Command(BaseCommand):
    help = 'File restock requests for every stock-holder whose stock is low (auto-restock).'

    def handle(self, *args, **options):
        totals = run_all()
        self.stdout.write(
            f"Auto-restock: {totals['stockist']} stockist, "
            f"{totals['wholesaler']} distributor, "
            f"{totals['marketplace']} marketplace request(s) created."
        )

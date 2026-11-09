"""Bring every product SKU into the current format.

    python manage.py reissue_skus --dry-run     # show old -> new, change nothing
    python manage.py reissue_skus               # do it

Current format: {SUPPLIER(4)}-{CATEGORY(4)}-{SEQUENCE(3)}, e.g. NUTR-NUTS-013
(the sequence is at least 3 digits and only grows past 3 beyond 999).

Two kinds of SKU get fixed:
- Right shape, wrong padding (e.g. NUTR-NUTS-00013): the number is KEPT and only
  the padding changes -> NUTR-NUTS-013.
- Anything else (e.g. the very old NTNU0013): given the next free number for its
  supplier + category.

A SKU is stored only on Product (no other table keeps a copy), so this breaks no
references. A CSV of old -> new is written to logs/ for the record.
"""
import csv
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from products.models import Product

SHAPE = re.compile(r'^([A-Z0-9]{4,6}-[A-Z0-9]{4}-)(\d+)$')


def canonical(sku, digits):
    """The current-format version of `sku` if only its padding is off, else None."""
    m = SHAPE.match(sku or '')
    if not m:
        return None
    return f'{m.group(1)}{int(m.group(2)):0{digits}d}'


class Command(BaseCommand):
    help = 'Reissue product SKUs in the current SUPPLIER-CATG-000 format.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show the changes without saving them.')

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        digits = Product.SKU_SEQ_DIGITS
        qs = Product.all_objects.select_related('supplier', 'category').order_by('pk')

        todo = []
        for p in qs:
            fixed = canonical(p.sku, digits)
            if fixed != p.sku:               # None (wrong shape) or different padding
                todo.append((p, fixed))
        if not todo:
            self.stdout.write(self.style.SUCCESS('Every product already has a current-format SKU.'))
            return

        changes = []
        with transaction.atomic():
            for p, fixed in todo:
                new = fixed or p._generate_sku()
                changes.append((p.pk, p.name, p.sku, new))
                # update() on purpose: save() never overwrites an existing SKU.
                Product.all_objects.filter(pk=p.pk).update(sku=new)
            if dry:
                transaction.set_rollback(True)

        for pk, name, old, new in changes:
            self.stdout.write(f'  #{pk:<4} {old:<16} -> {new:<16} {name}')

        if dry:
            self.stdout.write(self.style.WARNING(f'DRY RUN: {len(changes)} SKU(s) would change. Nothing was saved.'))
            return

        log_dir = Path(settings.BASE_DIR) / 'logs'
        log_dir.mkdir(exist_ok=True)
        path = log_dir / f'sku_reissue_{timezone.now():%Y-%m-%d_%H-%M}.csv'
        with path.open('w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['product_id', 'name', 'old_sku', 'new_sku'])
            w.writerows(changes)
        self.stdout.write(self.style.SUCCESS(
            f'Reissued {len(changes)} SKU(s). Old -> new list saved to {path}'))

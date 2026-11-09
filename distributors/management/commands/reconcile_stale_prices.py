"""
Management command: reconcile_stale_prices

Finds all DistributorVariantPrice records marked as price_stale=True that have been
stale for more than --hours-threshold (default 24) and recalculates their selling price
based on the current supplier price + distributor margin.

Usage:
    python manage.py reconcile_stale_prices
    python manage.py reconcile_stale_prices --hours-threshold 48 --dry-run
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Recalculate and clear stale distributor variant prices'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours-threshold', type=int, default=24,
            help='Only reconcile prices stale for more than this many hours (default: 24)',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be changed without saving',
        )

    def handle(self, *args, **options):
        threshold_hours = options['hours_threshold']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(hours=threshold_hours)

        try:
            from distributors.models import DistributorVariantPrice
        except ImportError:
            self.stderr.write('DistributorVariantPrice model not found.')
            return

        stale_qs = DistributorVariantPrice.objects.filter(
            price_stale=True,
            stale_marked_at__lt=cutoff,
        ).select_related('variant__product__supplier', 'distributor')

        count = stale_qs.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS(
                f'No prices stale for more than {threshold_hours}h. Nothing to do.'
            ))
            return

        self.stdout.write(f'Found {count} stale prices older than {threshold_hours}h.')

        updated = 0
        skipped = 0

        for dvp in stale_qs:
            try:
                # Recalculate price: variant base price + distributor margin
                variant = dvp.variant
                base = variant.selling_price  # supplier price

                # Apply distributor's default margin if available
                margin_pct = getattr(dvp.distributor, 'default_margin_percent', None)
                if margin_pct is not None and margin_pct > 0:
                    from decimal import Decimal
                    new_price = base * (1 + Decimal(str(margin_pct)) / 100)
                else:
                    # No margin configured — keep base price, just clear the stale flag
                    new_price = dvp.selling_price

                if dry_run:
                    self.stdout.write(
                        f'  [DRY] {dvp.distributor.company_name} / {variant} : '
                        f'{dvp.selling_price} → {new_price:.2f}'
                    )
                else:
                    dvp.selling_price = round(new_price, 2)
                    dvp.price_stale = False
                    dvp.stale_marked_at = None
                    dvp.save(update_fields=['selling_price', 'price_stale', 'stale_marked_at'])
                    logger.info(
                        'Reconciled price: distributor=%s variant=%s old=%s new=%s',
                        dvp.distributor_id, dvp.variant_id, dvp.selling_price, new_price,
                    )

                updated += 1
            except Exception as exc:
                logger.warning('Failed to reconcile dvp pk=%s: %s', dvp.pk, exc)
                skipped += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f'Dry run complete. Would update {updated} prices, skip {skipped}.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Reconciled {updated} stale prices. Skipped {skipped} due to errors.'
            ))

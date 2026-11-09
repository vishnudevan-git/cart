"""
Data-integrity audit — finds silent bad states that no error or 500 reveals.

These are exactly the kind of problems that slip past the URL smoke test
(pages still return 200) but are wrong in the data. Run it any time:

    venv\\Scripts\\python.exe manage.py audit_integrity

It is READ-ONLY — it reports, it never changes data. Exit code is non-zero if
any issue is found, so it can gate CI or a pre-deploy check.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Scan the database for data-integrity problems (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=10,
            help='Max example rows to print per check (default 10).')

    def handle(self, *args, **opts):
        limit = opts['limit']
        issues = []   # (severity, title, count, examples)

        def check(severity, title, queryset_or_list, fmt):
            try:
                items = list(queryset_or_list)
            except Exception as exc:
                issues.append(('ERROR', f'{title} (check failed: {exc})', 0, []))
                return
            if items:
                examples = [fmt(x) for x in items[:limit]]
                issues.append((severity, title, len(items), examples))

        self.stdout.write('=' * 70)
        self.stdout.write('DATA INTEGRITY AUDIT')
        self.stdout.write('=' * 70)

        # ---- imports kept local so the command loads even if an app changes ----
        from accounts.models import User
        from orders.models import Order
        from products.models import Product, ProductVariant
        from distributors.models import Distributor, DistributorVariantPrice
        from stockist.models import stockist_for, get_stockist_price

        # 1. Customers who ordered from a shop but have no shop link.
        check(
            'WARN',
            'Customers with orders but no linked shop (user.distributor is null)',
            User.objects.filter(role='customer', is_deleted=False, distributor__isnull=True,
                                orders__isnull=False).distinct(),
            lambda u: f'{u.username} ({u.orders.count()} orders)',
        )

        # 2. Orders with no shop (distributor) attached.
        check(
            'ERROR',
            'Orders with no Marketplace (distributor) attached',
            Order.objects.filter(distributor__isnull=True),
            lambda o: f'order #{o.id} ({o.get_status_display()})',
        )

        # 3. Soft-deleted users missing audit metadata.
        check(
            'WARN',
            'Soft-deleted users missing deleted_at/deleted_by',
            User.all_objects.filter(is_deleted=True).filter(
                models_q_blank_deleted()),
            lambda u: u.username,
        )

        # 4. Soft-deleted products missing deleted_at.
        check(
            'WARN',
            'Soft-deleted products missing deleted_at',
            Product.all_objects.filter(is_deleted=True, deleted_at__isnull=True),
            lambda p: f'{p.name} (#{p.id})',
        )

        # 5. Approved products that are still not released (charges unconfirmed)
        #    and not direct-supply — they can never be listed/sold.
        check(
            'INFO',
            'Approved products not released by stockist (charges_confirmed=False, not direct)',
            Product.objects.filter(status='approved', is_deleted=False,
                                   charges_confirmed=False, direct_supply=False),
            lambda p: f'{p.name} (#{p.id})',
        )

        # 6. Shop prices BELOW the current cost floor (shop would sell at a loss).
        below = []
        active_dvps = DistributorVariantPrice.objects.filter(
            is_active=True, distributor__status='active'
        ).select_related('variant__product', 'distributor')
        for dvp in active_dvps:
            product = dvp.variant.product
            if product.is_deleted or product.owner_enterprise_id:
                continue  # own-products have no supply chain floor
            stk = stockist_for(dvp.distributor)
            if not stk:
                continue
            try:
                floor, _ = get_stockist_price(product, dvp.variant, stk, enterprise=dvp.distributor)
            except Exception:
                continue
            if dvp.selling_price < floor:
                below.append((dvp, floor))
        check(
            'ERROR',
            'Shop selling price BELOW cost floor (selling at a loss)',
            below,
            lambda row: (f'{row[0].distributor.company_name}: {row[0].variant.product.name} '
                         f'{row[0].variant.label} sells Rs.{row[0].selling_price} < floor Rs.{row[1]}'),
        )

        # 7. Order timeline coverage: every status must be handled by the
        #    customer timeline template (guards the admin_picked-style bug).
        import re
        from pathlib import Path
        from django.conf import settings
        tmpl = Path(settings.BASE_DIR) / 'templates' / 'orders' / 'order_detail.html'
        unknown = []
        if tmpl.exists():
            body = tmpl.read_text(encoding='utf-8')
            referenced = set(re.findall(r"order\.status == '([a-z_]+)'", body))
            valid = {s[0] for s in Order.STATUS_CHOICES}
            # statuses referenced in template that are NOT real statuses = typos
            unknown = sorted(referenced - valid)
        check(
            'ERROR',
            'Order timeline references status values that do not exist (template typo)',
            unknown,
            lambda s: f"template uses '{s}' which is not in Order.STATUS_CHOICES",
        )

        # 8. Non-COD customer payments (should be none now — COD-only policy).
        from payments.models import Payment
        check(
            'INFO',
            'Customer payments that are not COD (legacy non-COD orders)',
            Payment.objects.exclude(payment_method='cod'),
            lambda p: f'order #{p.order_id}: {p.payment_method} ({p.status})',
        )

        # 9. Delivered orders whose COD payment is still pending (money not closed out).
        check(
            'WARN',
            'Delivered orders with COD payment still pending (not collected)',
            Payment.objects.filter(payment_method='cod', status='pending',
                                   order__status='delivered'),
            lambda p: f'order #{p.order_id} delivered but payment pending Rs.{p.amount}',
        )

        # ── SUPPLY-CHAIN integrity (pricing chain health) ──────────────────────
        from distributors.wholesaler_models import Wholesaler

        # 10. Active shops whose parent wholesale Distributor is SUSPENDED.
        #     The wholesale margin is silently dropped from their cost floor.
        check(
            'WARN',
            'Active shops linked to a SUSPENDED wholesale Distributor (margin silently lost)',
            Distributor.objects.filter(status='active', wholesaler__status='suspended')
                       .select_related('wholesaler'),
            lambda d: f'{d.company_name} -> {d.wholesaler.company_name} (suspended)',
        )

        # 11. Active wholesale Distributors with NO stockist assigned.
        #     Their shops have no proper stockist on the chain (falls back to the
        #     first active stockist, which may be wrong).
        check(
            'WARN',
            'Active wholesale Distributors with no stockist assigned',
            Wholesaler.objects.filter(status='active', stockist__isnull=True),
            lambda w: f'{w.company_name} ({w.enterprises.count()} shop(s)) has no stockist',
        )

        # 12. Active wholesale Distributors charging a ZERO margin (likely
        #     misconfigured — they do no processing, margin is their only earning).
        check(
            'INFO',
            'Active wholesale Distributors with margin_value = 0 (possibly unconfigured)',
            Wholesaler.objects.filter(status='active', margin_value=0),
            lambda w: f'{w.company_name} charges 0 margin',
        )

        # 13. Shops listed/pricing products with NO stockist resolvable at all.
        from stockist.models import stockist_for
        no_stockist_shops = [
            d for d in Distributor.objects.filter(status='active')
            if DistributorVariantPrice.objects.filter(distributor=d, is_active=True).exists()
            and stockist_for(d) is None
        ]
        check(
            'ERROR',
            'Active shops selling shared-catalogue products with NO stockist on their chain',
            no_stockist_shops,
            lambda d: f'{d.company_name} prices products but stockist_for() is None',
        )

        # 14. Active shop variant prices marked STALE (supplier changed price,
        #     shop has not re-priced). Customers may be on an outdated price.
        check(
            'INFO',
            'Active shop prices flagged stale (supplier price changed, not re-priced)',
            DistributorVariantPrice.objects.filter(
                is_active=True, price_stale=True, distributor__status='active'
            ).select_related('distributor', 'variant__product'),
            lambda p: (f'{p.distributor.company_name}: {p.variant.product.name} '
                       f'{p.variant.label} (stale)'),
        )

        # ---- report ----
        self.stdout.write('')
        if not issues:
            self.stdout.write(self.style.SUCCESS('✓ No integrity issues found. Data is clean.'))
            return

        order = {'ERROR': 0, 'WARN': 1, 'INFO': 2}
        issues.sort(key=lambda x: order.get(x[0], 9))
        n_error = 0
        for sev, title, count, examples in issues:
            style = {'ERROR': self.style.ERROR, 'WARN': self.style.WARNING,
                     'INFO': self.style.NOTICE}.get(sev, self.style.NOTICE)
            if sev == 'ERROR':
                n_error += 1
            self.stdout.write(style(f'[{sev}] {title} — {count}'))
            for ex in examples:
                self.stdout.write(f'        - {ex}')
            if count > len(examples):
                self.stdout.write(f'        ... and {count - len(examples)} more')

        self.stdout.write('')
        self.stdout.write('=' * 70)
        self.stdout.write(f'Summary: {len(issues)} check(s) flagged, {n_error} error-level.')
        if n_error:
            # non-zero exit for error-level problems
            raise SystemExit(1)


def models_q_blank_deleted():
    """Q: soft-deleted users with no deleted_at OR no deleted_by."""
    from django.db.models import Q
    return Q(deleted_at__isnull=True) | Q(deleted_by='')

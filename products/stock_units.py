"""Unit-aware downstream stock totals for the supplier stock page.

Downstream quantities (StockistInventory, WholesalerStock, DistributorStock) are
counted in packs of one variant. Adding packs of different sizes gives a number
that means nothing, such as 2 packs of 500 g plus 1 pack of 1 kg shown as "3".
Multiplying each count by its pack size gives a total in base units (grams or
millilitres) that can be added across sizes.
"""
from __future__ import annotations


def _base_totals_by_product(model, product_ids):
    """Sum quantity x unit_grams per product, plus the ids of products holding
    stock in a pack with no size (unit_grams == 0), which cannot be converted."""
    from django.db.models import ExpressionWrapper, F, IntegerField, Sum

    rows = (model.objects
            .filter(variant__product_id__in=product_ids)
            .values('variant__product_id')
            .annotate(total=Sum(ExpressionWrapper(
                F('quantity') * F('variant__unit_grams'),
                output_field=IntegerField()))))
    totals = {r['variant__product_id']: (r['total'] or 0) for r in rows}
    unsized = set(
        model.objects
        .filter(variant__product_id__in=product_ids, variant__unit_grams=0, quantity__gt=0)
        .values_list('variant__product_id', flat=True)
    )
    return totals, unsized


def attach_downstream_base_totals(products):
    """Set ``ds_<tier>_base`` on each product, in grams or ml.

    A value is None when the product has stock in a pack with no size, because
    that stock cannot be converted to base units.
    """
    from stockist.models import StockistInventory
    from distributors.wholesaler_models import WholesalerStock
    from distributors.models import DistributorStock

    product_ids = [p.pk for p in products]
    tiers = {
        'stockist': StockistInventory,
        'distributor': WholesalerStock,
        'shop': DistributorStock,
    }
    per_tier = {}
    for tier, model in tiers.items():
        if product_ids:
            per_tier[tier] = _base_totals_by_product(model, product_ids)
        else:
            per_tier[tier] = ({}, set())

    for p in products:
        values = {}
        for tier in tiers:
            totals, unsized = per_tier[tier]
            values[tier] = None if p.pk in unsized else totals.get(p.pk, 0)
        for tier, value in values.items():
            setattr(p, f'ds_{tier}_base', value)
        known = [v for v in values.values() if v is not None]
        p.ds_total_base = sum(known) if len(known) == len(values) else None
    return products


def format_base_units(amount, unit):
    """Human label for a base-unit amount: 2000 g -> '2 kg', 750 ml -> '750 ml'."""
    if unit == 'ml':
        return f'{amount / 1000:g} L' if amount >= 1000 else f'{amount} ml'
    return f'{amount / 1000:g} kg' if amount >= 1000 else f'{amount} g'

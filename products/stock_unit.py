"""Stock unit shown in filters: grams products with packs of 50 g and up are kilogram stock.

The stored unit (Product.master_unit) and the stored amount (master_stock_grams)
do not change. This rule only decides which filter group a product belongs to:

  * master_unit 'g' and every active pack is 50 g or more  -> 'kg'
  * master_unit 'g' and any active pack is under 50 g        -> 'g'
  * master_unit 'g' with no active packs                     -> 'g'
  * 'ml' and 'qty' are unchanged.

Keep this rule identical in effective_stock_unit() (Python) and
effective_unit_q() (database filter).
"""
from django.db.models import Min, Q

KG_FROM_GRAMS = 50


def effective_stock_unit(master_unit, pack_grams):
    """Filter group for a product. pack_grams = unit_grams of its active packs."""
    if master_unit != 'g':
        return master_unit
    small = [g for g in pack_grams if g and g > 0]
    if small and min(small) >= KG_FROM_GRAMS:
        return 'kg'
    return 'g'


def annotate_min_active_pack(queryset):
    """Adds min_active_pack: smallest active pack size in base units (NULL if none)."""
    return queryset.annotate(
        min_active_pack=Min(
            'variants__unit_grams',
            filter=Q(variants__is_active=True, variants__unit_grams__gt=0),
        )
    )


def effective_unit_q(unit):
    """Q matching products in a filter group. Needs annotate_min_active_pack first."""
    if unit == 'kg':
        return Q(master_unit='kg') | (
            Q(master_unit='g') & Q(min_active_pack__gte=KG_FROM_GRAMS))
    if unit == 'g':
        return Q(master_unit='g') & (
            Q(min_active_pack__lt=KG_FROM_GRAMS) | Q(min_active_pack__isnull=True))
    return Q(master_unit=unit)

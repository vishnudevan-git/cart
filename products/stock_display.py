"""Shared product-list annotation for the stock/products display.

Both the supplier "My Products" page and the admin "Stock" (read-only) page
render the same product-grouped table (templates/partials/product_stock_table.html).
That template expects each product to carry a set of computed attributes
(display_variants, detail_variants, total_stock_grams, stock_breakdown, etc.).

This helper builds those attributes once so both views stay in sync. Extracted
from suppliers.views.product_list so the two pages share one implementation.
"""
from __future__ import annotations


def annotate_products_for_stock(products):
    """Attach display/stock attributes to each product in `products`.

    Returns a list of the same product objects, annotated in place.
    """
    annotated = []
    for p in products:
        # Deduplicated variant pills (keep first seen per label).
        seen_labels = {}
        for v in p.variants.filter(is_active=True).order_by('unit_grams', 'selling_price'):
            if v.label not in seen_labels:
                seen_labels[v.label] = v
        p.display_variants = list(seen_labels.values())

        active_vts = list(p.variant_types.filter(is_active=True).order_by('position', 'id'))
        vt_pools = [vt.master_stock_grams or 0 for vt in active_vts]
        total_pool = sum(vt_pools)
        p.total_stock_grams = total_pool if total_pool > 0 else (p.master_stock_grams or 0)
        p.pool_variant_count = len([x for x in vt_pools if x > 0])
        p.pool_breakdown = [
            {'name': (vt.name or '(default)'), 'grams': (vt.master_stock_grams or 0)}
            for vt in active_vts if (vt.master_stock_grams or 0) > 0
        ] if total_pool > 0 and p.pool_variant_count > 1 else []

        p.variant_count = len(active_vts)

        is_pool = p.total_stock_grams and len(p.pool_breakdown) >= 0 and (
            sum(vt.master_stock_grams or 0 for vt in active_vts) > 0)
        detail_variants = []
        for vt in active_vts:
            packs = [{
                'label': pk.label,
                'price': pk.selling_price,
                'stock': pk.stock,
            } for pk in vt.packs.filter(is_active=True).order_by('unit_grams')]
            pool_g = vt.master_stock_grams or 0
            detail_variants.append({
                'name': vt.name or '(default)',
                'pool_grams': pool_g if is_pool else 0,
                'packs': packs,
            })
        p.detail_variants = detail_variants
        p.detail_is_pool = bool(is_pool)

        has_multi_variants = len(active_vts) > 1
        breakdown = []
        total_packs = 0
        if not p.total_stock_grams:
            active_packs = (p.variants.filter(is_active=True)
                            .select_related('variant_type')
                            .order_by('variant_type__position', 'unit_grams'))
            for v in active_packs:
                vt_name = (v.variant_type.name if v.variant_type else '') or ''
                name = (f'{vt_name} {v.label}'.strip()) if (has_multi_variants and vt_name) else v.label
                breakdown.append({'name': name, 'stock': v.stock})
                total_packs += v.stock
        p.stock_breakdown = breakdown
        p.stock_total_packs = total_packs

        annotated.append(p)
    return annotated


def attach_downstream_stock(products):
    """Attach per-product downstream stock totals to each product.

    Downstream tiers (below the supplier), keyed by the product's variants:
      • Stockist     -> stockist.StockistInventory.quantity
      • Distributor  -> distributors.WholesalerStock.quantity   (UI "Distributor")
      • Shop         -> distributors.DistributorStock.quantity  (UI "Marketplace")

    Sets on each product:
      p.ds_stockist, p.ds_distributor, p.ds_shop, p.ds_total

    Uses one aggregated query per tier (grouped by product), so it stays fast
    regardless of row count. See kb/08-supplier-suspension.md for why this
    buffer matters during suspension.
    """
    from django.db.models import Sum
    from stockist.models import StockistInventory
    from distributors.wholesaler_models import WholesalerStock
    from distributors.models import DistributorStock

    product_ids = [p.pk for p in products]
    if not product_ids:
        return products

    def _by_product(model):
        rows = (model.objects
                .filter(variant__product_id__in=product_ids)
                .values('variant__product_id')
                .annotate(total=Sum('quantity')))
        return {r['variant__product_id']: (r['total'] or 0) for r in rows}

    stockist_map = _by_product(StockistInventory)
    distributor_map = _by_product(WholesalerStock)
    shop_map = _by_product(DistributorStock)

    for p in products:
        p.ds_stockist = stockist_map.get(p.pk, 0)
        p.ds_distributor = distributor_map.get(p.pk, 0)
        p.ds_shop = shop_map.get(p.pk, 0)
        p.ds_total = p.ds_stockist + p.ds_distributor + p.ds_shop
    return products


def attach_downstream_detail(products):
    """Attach a per-variant / per-pack downstream breakdown to each product, so
    the details panel can show exactly which variant & pack holds stock at each
    tier — plus the shop's loose (bulk) pool for loose products.

    Also calls attach_downstream_stock so the tier totals are available.

    Sets on each product:
      p.ds_rows  -> list of {variant_label, variant_name, stockist, distributor, shop}
                    one row per ProductVariant (pack) that holds stock anywhere
      p.ds_shop_loose_grams -> shop loose pool in base units (0 if none)
      p.ds_shop_loose_unit  -> base unit ('g'/'ml'/'qty') for the loose pool
      (plus ds_stockist / ds_distributor / ds_shop / ds_total from attach_downstream_stock)
    """
    from stockist.models import StockistInventory
    from distributors.wholesaler_models import WholesalerStock
    from distributors.models import DistributorStock, DistributorLooseStock

    attach_downstream_stock(products)

    product_ids = [p.pk for p in products]
    if not product_ids:
        return products

    def _by_variant(model):
        """variant_id -> summed quantity across all holders of that tier."""
        from django.db.models import Sum
        rows = (model.objects
                .filter(variant__product_id__in=product_ids)
                .values('variant_id', 'variant__product_id')
                .annotate(total=Sum('quantity')))
        out = {}
        for r in rows:
            out[r['variant_id']] = r['total'] or 0
        return out

    st_v = _by_variant(StockistInventory)
    di_v = _by_variant(WholesalerStock)
    sh_v = _by_variant(DistributorStock)

    # Shop loose pools (per product, grams).
    from django.db.models import Sum as _Sum
    loose_rows = (DistributorLooseStock.objects
                  .filter(product_id__in=product_ids)
                  .values('product_id')
                  .annotate(g=_Sum('quantity_grams')))
    loose_map = {r['product_id']: (r['g'] or 0) for r in loose_rows}

    for p in products:
        rows = []
        for v in p.variants.all():
            s = st_v.get(v.pk, 0)
            d = di_v.get(v.pk, 0)
            sh = sh_v.get(v.pk, 0)
            if s or d or sh:
                vt_name = ''
                try:
                    vt_name = (v.variant_type.name if v.variant_type else '') or ''
                except Exception:
                    vt_name = ''
                rows.append({
                    'variant_label': v.label,
                    'variant_name': vt_name,
                    'stockist': s,
                    'distributor': d,
                    'shop': sh,
                })
        p.ds_rows = rows
        p.ds_shop_loose_grams = loose_map.get(p.pk, 0)
        p.ds_shop_loose_unit = getattr(p, 'master_unit', 'g')
    return products

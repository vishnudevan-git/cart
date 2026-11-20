"""Bundle rules across the supply chain (kb/10-bundle-supply-chain.md).

Chain, top to bottom:

    Supplier -> Stockist -> Distributor (code: wholesaler) -> Marketplace (code: distributor) -> Customer

Rules implemented here:

* A bundle is only a count. Stock is one number of units; a bundle of 10 is
  10 units taken from that number. Nothing here packs or unpacks anything.
* Each selling tier has a rule per product: ``singles``, ``bundle_only`` or
  ``forward`` (follow the tier above). Resolution: product row, supplier
  row, global row, default ``singles``.
* A buyer must order whole multiples of the bundle size when the tier that
  actually sells to it is bundle-only.
* A Distributor with ``no_buffer_stock`` is skipped: its shops are served by
  the stockist, so the stockist's rule applies to them.
* The supplier is the top. Buying from the supplier keeps the existing rule
  ``ProductVariant.supplier_order_multiple``.
* Customers always buy singles. Nothing here applies to customer checkout.

Adding a tier later: give it a ``TIER_CODES`` entry and an ``_UPSTREAM``
function returning the entity it buys from. Rules, routing and the settings
page then work for it unchanged.
"""
from products.models import ProductBundle, TierBundleRule

SUPPLIER = None          # sentinel: "the seller is the supplier"


def _stockist_model():
    from stockist.models import Stockist
    return Stockist


def _wholesaler_model():
    from distributors.wholesaler_models import Wholesaler
    return Wholesaler


def _marketplace_model():
    from distributors.models import Distributor
    return Distributor


def tier_code(entity):
    """Chain code for an entity, e.g. 'stockist' or 'wholesaler'."""
    if entity is None:
        return 'supplier'
    if isinstance(entity, _stockist_model()):
        return 'stockist'
    if isinstance(entity, _wholesaler_model()):
        return 'wholesaler'
    if isinstance(entity, _marketplace_model()):
        return 'marketplace'
    raise TypeError(f'Not a chain tier: {entity!r}')


def _active(entity):
    return entity is not None and getattr(entity, 'status', 'active') == 'active'


def upstream_of(entity):
    """The entity this buyer orders from, before skipping no-buffer tiers.

    Returns SUPPLIER (None) when the next tier up is the supplier.
    """
    code = tier_code(entity)
    if code == 'marketplace':
        wholesaler = getattr(entity, 'wholesaler', None)
        if wholesaler is not None:
            return wholesaler
        from stockist.models import stockist_for
        return stockist_for(entity)
    if code == 'wholesaler':
        stockist = entity.stockist
        if _active(stockist):
            return stockist
        from stockist.models import Stockist
        return Stockist.objects.filter(status='active').first()
    return SUPPLIER


def keeps_buffer(entity):
    """False when the tier passes orders straight through to its upstream."""
    if tier_code(entity) == 'wholesaler':
        return not entity.no_buffer_stock
    return True


def effective_seller(buyer):
    """The tier that actually fills this buyer's order (no-buffer tiers skipped)."""
    seller = upstream_of(buyer)
    while seller is not SUPPLIER and not keeps_buffer(seller):
        seller = upstream_of(seller)
    return seller


def routes_via_wholesaler(marketplace, product=None):
    """True when a shop's restock waits at its Distributor (buffer kept)."""
    if product is not None and product.direct_supply:
        return False
    wholesaler = getattr(marketplace, 'wholesaler', None)
    return wholesaler is not None and keeps_buffer(wholesaler)


def rule_mode(seller, product):
    """Resolved mode for one seller and product: product > supplier > global > singles."""
    rows = {
        row.scope: row.mode
        for row in TierBundleRule.objects.filter(
            tier=tier_code(seller), owner_id=seller.pk,
        ).filter(
            scope__in=('global', 'supplier', 'product'),
        ).filter(
            _scope_filter(product)
        )
    }
    for scope in ('product', 'supplier', 'global'):
        if scope in rows:
            return rows[scope]
    return 'singles'


def _scope_filter(product):
    from django.db.models import Q
    return (
        Q(scope='global')
        | Q(scope='supplier', supplier_id=product.supplier_id)
        | Q(scope='product', product_id=product.pk)
    )


def seller_is_bundle_only(seller, variant):
    """Whether this seller sells the variant downstream in whole bundles only."""
    seen = set()
    while seller is not SUPPLIER:
        key = (tier_code(seller), seller.pk)
        if key in seen:              # defensive: a misassigned chain must not loop
            return False
        seen.add(key)
        mode = rule_mode(seller, variant.product)
        if mode == 'bundle_only':
            return True
        if mode == 'singles':
            return False
        seller = upstream_of(seller)  # forward
    return bool(variant.only_bundle)


def bundle_size(variant):
    """Units in one bundle of this pack (1 when no active bundle is defined)."""
    size = ProductBundle.objects.filter(
        variant=variant, is_active=True,
    ).order_by('pack_quantity').values_list('pack_quantity', flat=True).first()
    return max(1, int(size or 1))


def required_multiple(buyer, variant):
    """Order quantity step for this buyer and pack (1 = any count)."""
    if variant.product.direct_supply:
        return max(1, variant.supplier_order_multiple)
    seller = effective_seller(buyer)
    if seller is SUPPLIER:
        return max(1, variant.supplier_order_multiple)
    return bundle_size(variant) if seller_is_bundle_only(seller, variant) else 1


def round_up(quantity, multiple):
    multiple = max(1, int(multiple or 1))
    quantity = max(0, int(quantity))
    return ((quantity + multiple - 1) // multiple) * multiple


# ── Settings page helpers (shared by stockist and distributor views) ────────

def settings_context(seller):
    from products.models import Product
    from suppliers.models import Supplier
    rules = TierBundleRule.objects.filter(tier=tier_code(seller), owner_id=seller.pk)
    global_row = rules.filter(scope='global').first()
    return {
        'bundle_global_mode': global_row.mode if global_row else 'singles',
        'bundle_supplier_rules': rules.filter(scope='supplier').select_related('supplier'),
        'bundle_product_rules': rules.filter(scope='product').select_related('product'),
        'bundle_mode_choices': TierBundleRule.MODE_CHOICES,
        'bundle_suppliers': Supplier.objects.exclude(status='suspended').order_by('company_name'),
        'bundle_products': Product.objects.filter(
            status='approved', is_deleted=False, direct_supply=False,
        ).order_by('name'),
    }


def handle_settings_post(request, seller):
    """Apply one settings form post. Returns a user-facing message."""
    from products.models import Product
    from suppliers.models import Supplier
    action = request.POST.get('bundle_action', '')
    mode = request.POST.get('mode', '')
    valid_modes = {m for m, _ in TierBundleRule.MODE_CHOICES}
    base = {'tier': tier_code(seller), 'owner_id': seller.pk}

    if action in ('set_global', 'set_supplier', 'set_product') and mode not in valid_modes:
        raise ValueError('Choose a valid bundle mode.')
    if action == 'set_global':
        TierBundleRule.objects.update_or_create(scope='global', **base, defaults={'mode': mode})
        return 'Global bundle setting saved.'
    if action == 'set_supplier':
        supplier = Supplier.objects.filter(pk=request.POST.get('supplier_id')).first()
        if supplier is None:
            raise ValueError('Choose a supplier.')
        TierBundleRule.objects.update_or_create(
            scope='supplier', supplier=supplier, **base, defaults={'mode': mode})
        return f'Bundle setting saved for {supplier.company_name}.'
    if action == 'set_product':
        product = Product.objects.filter(
            pk=request.POST.get('product_id'), is_deleted=False,
        ).first()
        if product is None:
            raise ValueError('Choose a product.')
        TierBundleRule.objects.update_or_create(
            scope='product', product=product, **base, defaults={'mode': mode})
        return f'Bundle setting saved for {product.name}.'
    if action == 'remove':
        deleted, _ = TierBundleRule.objects.filter(
            pk=request.POST.get('rule_id'), **base,
        ).exclude(scope='global').delete()
        if not deleted:
            raise ValueError('Setting not found.')
        return 'Override removed. The broader setting applies again.'
    raise ValueError('Unknown action.')

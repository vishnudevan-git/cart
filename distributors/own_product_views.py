"""
Marketplace (shop) OWN PRODUCTS — a feature of the Marketplace login, not a role.

Single-role policy: a shop account is only a Marketplace. It never becomes a
supplier. But a shop often has its own goods (home-made, locally sourced, its
own brand) and must be able to sell them in its own store.

Those products are PRIVATE to the one shop that owns them:
  * ``Product.owner_enterprise`` points at that shop;
  * ``Product.objects`` (the default manager) filters them out, so suppliers,
    the stockist, other Marketplaces, the shared catalogue and admin approval queues
    can never see or source them;
  * only the owning shop's catalogue and its own customers' storefront read
    them, via ``Product.all_objects``.

A shop that wants to supply the shared catalogue must open a SEPARATE supplier
account with its own login.
"""
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from django.http import JsonResponse

from products.models import (
    Product, ProductVariant, Category, ProductTemplate, ProductTemplateVariant,
)
from suppliers.models import Supplier

from .models import DistributorProduct, DistributorVariantPrice
from .views import distributor_required


def _house_supplier(dist):
    """The shop's in-house catalogue holder.

    ``Product.supplier`` is required by the schema, so each Marketplace gets one
    hidden house record to hang its own products on. It is flagged
    ``is_house_account`` and excluded from every supplier-facing surface — it is
    a data holder, never a login role and never a shared-catalogue supplier.
    """
    supplier, created = Supplier.all_objects.get_or_create(
        user=dist.user,
        defaults={
            'company_name': dist.company_name or dist.user.username,
            'category': 'other',
            'status': 'approved',
            'is_house_account': True,
        },
    )
    if not created and not supplier.is_house_account:
        supplier.is_house_account = True
        supplier.save(update_fields=['is_house_account'])
    return supplier


def _own_products(dist):
    return Product.all_objects.owned_by(dist).filter(is_deleted=False)


def _decimal(raw, default='0'):
    try:
        return Decimal(str(raw).strip() or default)
    except (InvalidOperation, AttributeError):
        return Decimal(default)


def _int(raw, default=0):
    try:
        return int(str(raw).strip() or default)
    except (TypeError, ValueError):
        return default


@distributor_required
def own_product_list(request):
    dist = request.user.distributor_profile
    products = _own_products(dist).select_related('category').prefetch_related('variants')
    listed_ids = set(
        DistributorProduct.objects.filter(
            distributor=dist, is_listed=True, product__in=products
        ).values_list('product_id', flat=True)
    )
    for p in products:
        p.is_live = p.pk in listed_ids
    return render(request, 'distributors/own_products.html', {
        'dist': dist,
        'products': products,
        'active_nav': 'own_products',
    })


@distributor_required
def own_product_form(request, pk=None):
    dist = request.user.distributor_profile
    product = get_object_or_404(_own_products(dist), pk=pk) if pk else None

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Product name is required.')
            return redirect(request.path)

        category_id = request.POST.get('category') or None
        description = (request.POST.get('description') or '').strip()

        labels = request.POST.getlist('variant_label')
        prices = request.POST.getlist('variant_price')
        mrps = request.POST.getlist('variant_mrp')
        stocks = request.POST.getlist('variant_stock')
        grams = request.POST.getlist('variant_grams')
        variant_ids = request.POST.getlist('variant_id')

        rows = []
        for i, label in enumerate(labels):
            label = (label or '').strip()
            price = _decimal(prices[i] if i < len(prices) else 0)
            if not label or price <= 0:
                continue
            rows.append({
                'pk': (variant_ids[i] if i < len(variant_ids) else '') or None,
                'label': label,
                'selling_price': price,
                'mrp': _decimal(mrps[i] if i < len(mrps) else 0) or None,
                'stock': _int(stocks[i] if i < len(stocks) else 0),
                'unit_grams': _int(grams[i] if i < len(grams) else 0),
            })

        if not rows:
            messages.error(request, 'Add at least one pack with a name and a price above ₹0.')
            return redirect(request.path)

        with transaction.atomic():
            if product is None:
                product = Product(supplier=_house_supplier(dist), owner_enterprise=dist)
            product.name = name
            product.description = description
            product.category_id = category_id
            # Shop-owned: no admin approval and no stockist processing stands
            # between this product and the shop's own customers.
            product.status = 'approved'
            product.charges_confirmed = True
            if request.FILES.get('image'):
                product.image = request.FILES['image']
            product.save()

            kept = []
            for row in rows:
                variant = None
                if row['pk']:
                    variant = ProductVariant.objects.filter(pk=row['pk'], product=product).first()
                if variant is None:
                    variant = ProductVariant(product=product)
                variant.label = row['label']
                variant.selling_price = row['selling_price']
                variant.mrp = row['mrp'] if row['mrp'] and row['mrp'] > 0 else None
                variant.stock = row['stock']
                variant.unit_grams = row['unit_grams']
                variant.is_active = True
                variant.save()
                kept.append(variant.pk)

            # Packs the shop removed from the form are deactivated, never
            # deleted (no hard deletes anywhere in this project).
            ProductVariant.objects.filter(product=product).exclude(pk__in=kept).update(is_active=False)

            _publish_to_own_store(dist, product)
            # Grow the shared template library from what this shop just entered,
            # so other shops can reuse it next time.
            _contribute_template(product)

        messages.success(request, f'"{product.name}" saved and live in your store.')
        return redirect('distributors:own_product_list')

    variants = list(product.variants.filter(is_active=True)) if product else []
    return render(request, 'distributors/own_product_form.html', {
        'dist': dist,
        'product': product,
        'variants': variants,
        'categories': Category.objects.order_by('name'),
        'active_nav': 'own_products',
    })


def _publish_to_own_store(dist, product):
    """List the product in the owning shop's store at the shop's own price.

    There is no supply chain behind an own product — no supplier cost, no
    stockist charges, no wholesale margin — so the price the shop typed IS the
    customer price.
    """
    DistributorProduct.objects.update_or_create(
        distributor=dist, product=product,
        defaults={'is_listed': True, 'margin_percent': 0, 'margin_is_manual': True},
    )
    for variant in product.variants.filter(is_active=True):
        DistributorVariantPrice.objects.update_or_create(
            distributor=dist, variant=variant,
            defaults={
                'base_price': variant.selling_price,
                'selling_price': variant.selling_price,
                'is_active': True,
                'price_stale': False,
            },
        )
    DistributorVariantPrice.objects.filter(
        distributor=dist, variant__product=product, variant__is_active=False
    ).update(is_active=False)


@distributor_required
@require_POST
def own_product_toggle(request, pk):
    """Show/hide an own product in the shop's store (never deletes it)."""
    dist = request.user.distributor_profile
    product = get_object_or_404(_own_products(dist), pk=pk)
    dp, _ = DistributorProduct.objects.get_or_create(distributor=dist, product=product)
    dp.is_listed = not dp.is_listed
    dp.save(update_fields=['is_listed'])
    messages.success(
        request,
        f'"{product.name}" is now {"visible in" if dp.is_listed else "hidden from"} your store.'
    )
    return redirect('distributors:own_product_list')


@distributor_required
@require_POST
def own_product_delete(request, pk):
    """Soft delete — the record is retained, only hidden (no hard deletes)."""
    from django.utils import timezone

    dist = request.user.distributor_profile
    product = get_object_or_404(_own_products(dist), pk=pk)
    product.is_deleted = True
    product.deleted_at = timezone.now()
    product.save(update_fields=['is_deleted', 'deleted_at'])
    DistributorProduct.objects.filter(distributor=dist, product=product).update(is_listed=False)
    DistributorVariantPrice.objects.filter(
        distributor=dist, variant__product=product
    ).update(is_active=False)
    messages.success(request, f'"{product.name}" removed from your store.')
    return redirect('distributors:own_product_list')


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT TEMPLATE LIBRARY — reuse + contribution (Marketplace own-products)
# ─────────────────────────────────────────────────────────────────────────────
def _contribute_template(product):
    """Upsert a reusable template from a shop's own product (deduped by name).

    Prices are stored only as *suggested* defaults — each shop sets its own
    price on reuse, so the contributing shop's price never binds anyone else.
    Never raises: a template hiccup must not break saving the product.
    """
    try:
        name = (product.name or '').strip()
        if not name:
            return
        tmpl, _created = ProductTemplate.objects.get_or_create(
            brand='', name=name,
            defaults={
                'category': product.category,
                'description': product.description or '',
                'source': 'shop',
            },
        )
        # Copy an image into the library only if the template has none yet.
        if product.image and not tmpl.image:
            try:
                tmpl.image = product.image
                tmpl.save(update_fields=['image'])
            except Exception:
                pass
        # Merge packs by label (keep a representative suggested price).
        existing = {v.label.lower(): v for v in tmpl.variants.all()}
        for v in product.variants.filter(is_active=True):
            key = v.label.lower()
            if key in existing:
                continue
            ProductTemplateVariant.objects.create(
                template=tmpl, label=v.label, unit_grams=v.unit_grams or 0,
                suggested_price=v.selling_price or 0, suggested_mrp=v.mrp,
            )
    except Exception:
        pass


@distributor_required
def template_search(request):
    """Type-to-search JSON for the own-product template picker.

    Returns matching templates (name/brand) with their default packs so the
    form can pre-fill everything in one click.
    """
    q = (request.GET.get('q') or '').strip()
    results = []
    if len(q) >= 2:
        from django.db.models import Q as _Q
        qs = (ProductTemplate.objects.filter(is_active=True)
              .filter(_Q(name__icontains=q) | _Q(brand__icontains=q))
              .prefetch_related('variants', 'category')[:12])
        for t in qs:
            results.append({
                'id': t.id,
                'name': t.name,
                'brand': t.brand,
                'display': t.display_name,
                'category_id': t.category_id,
                'category_name': t.category.name if t.category else '',
                'description': t.description,
                'image': t.image.url if t.image else '',
                'use_count': t.use_count,
                'packs': [
                    {'label': v.label, 'unit_grams': v.unit_grams,
                     'suggested_price': str(v.suggested_price or ''),
                     'suggested_mrp': str(v.suggested_mrp or '')}
                    for v in t.variants.all()
                ],
            })
    return JsonResponse({'results': results})


@distributor_required
def template_library(request):
    """Browse the whole library and bulk-import several templates at once —
    ideal for a brand-new shop stocking up its catalogue quickly."""
    dist = request.user.distributor_profile

    if request.method == 'POST':
        ids = request.POST.getlist('template_ids')
        if not ids:
            messages.warning(request, 'Select at least one product to import.')
            return redirect('distributors:template_library')
        created = 0
        skipped = 0
        existing_names = {
            (n or '').strip().lower()
            for n in _own_products(dist).values_list('name', flat=True)
        }
        templates = (ProductTemplate.objects.filter(id__in=ids, is_active=True)
                     .prefetch_related('variants'))
        with transaction.atomic():
            house = _house_supplier(dist)
            for t in templates:
                if t.name.strip().lower() in existing_names:
                    skipped += 1
                    continue
                product = Product(
                    supplier=house, owner_enterprise=dist,
                    name=t.name, description=t.description or '',
                    category=t.category, status='approved', charges_confirmed=True,
                )
                if t.image:
                    product.image = t.image  # shares the stored file path; copy-on-edit
                product.save()
                packs = list(t.variants.all())
                if not packs:
                    # No packs on the template — give one sensible default row.
                    ProductVariant.objects.create(
                        product=product, label='1 unit', unit_grams=0,
                        selling_price=0.01, stock=0, is_active=True)
                for v in packs:
                    ProductVariant.objects.create(
                        product=product, label=v.label, unit_grams=v.unit_grams or 0,
                        selling_price=v.suggested_price or Decimal('0.01'),
                        mrp=v.suggested_mrp, stock=0, is_active=True)
                _publish_to_own_store(dist, product)
                ProductTemplate.objects.filter(pk=t.pk).update(use_count=t.use_count + 1)
                created += 1
        msg = f'{created} product(s) imported into your store.'
        if skipped:
            msg += f' {skipped} already existed and were skipped.'
        if created:
            msg += ' Set your prices and stock on each, then they are live.'
        messages.success(request, msg)
        return redirect('distributors:own_product_list')

    q = (request.GET.get('q') or '').strip()
    templates = ProductTemplate.objects.filter(is_active=True).prefetch_related('variants', 'category')
    if q:
        from django.db.models import Q as _Q
        templates = templates.filter(_Q(name__icontains=q) | _Q(brand__icontains=q))
    templates = templates[:200]
    return render(request, 'distributors/template_library.html', {
        'dist': dist,
        'templates': templates,
        'q': q,
        'active_nav': 'own_products',
    })

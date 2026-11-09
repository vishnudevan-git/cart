from django.db import models
from django.db.models import Q
from django.core.exceptions import ValidationError
from suppliers.models import Supplier


class Category(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    # 4-char code used in SKU generation e.g. NUTS, DRYF, ORGA, SPIC, OTHE
    code = models.CharField(max_length=4, unique=True, blank=True,
                            help_text='4-char code for SKU (e.g. NUTS, DRYF, ORGA, SPIC). '
                                      'Generated automatically from the name.')
    image = models.ImageField(upload_to='categories/', blank=True)

    class Meta:
        verbose_name_plural = 'Categories'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.code:
            from lakrish_mart.codegen import unique_code, initials_code
            label = self.name or self.slug
            self.code = unique_code(
                label, 4,
                lambda c: Category.objects.filter(code=c).exclude(pk=self.pk).exists(),
                alternatives=[initials_code(label, 4)],
            )
        super().save(*args, **kwargs)


class ProductQuerySet(models.QuerySet):
    def visible_to_enterprise(self, enterprise):
        """Shared-catalogue products PLUS this one shop's own products.

        Naming: the shop tier is called "Marketplace" in the UI; the common
        product pool is the "shared catalogue".
        """
        if enterprise is None:
            return self.filter(owner_enterprise__isnull=True)
        return self.filter(Q(owner_enterprise__isnull=True) | Q(owner_enterprise=enterprise))

    def owned_by(self, enterprise):
        return self.filter(owner_enterprise=enterprise)


class SharedProductManager(models.Manager.from_queryset(ProductQuerySet)):
    """Default manager — the SHARED supply chain (shared catalogue) only.

    A Marketplace's (shop's) own products are private to that one shop, so
    they are invisible here by design: suppliers, the stockist, other
    Marketplaces, the storefront and admin approval queues all read through
    ``Product.objects`` and therefore can never see or source them. Use
    ``Product.all_objects`` only where a shop is managing its own catalogue
    or its customers are shopping it.
    """

    def get_queryset(self):
        return super().get_queryset().filter(owner_enterprise__isnull=True)


class AllProductManager(models.Manager.from_queryset(ProductQuerySet)):
    """Every product, including shop-owned (Marketplace) ones."""


class ProductVariantQuerySet(models.QuerySet):
    def shared(self):
        """Shared-catalogue variants only — never a shop's private own-products.

        ``ProductVariant.objects`` is deliberately NOT filtered by default the
        way ``Product.objects`` is: a shop's own storefront reads its packs
        through ``product.variants``, and a default filter would empty that out
        for the very shop that owns them.

        So every query that starts from ProductVariant on an upstream surface —
        stockist inventory, admin stock/reports/low-stock — must say ``.shared()``
        explicitly. The regression tests in distributors/tests.py assert each of
        those surfaces stays clean.
        """
        return self.filter(product__owner_enterprise__isnull=True)


class Product(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name='products')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='products/', blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    review_count = models.PositiveIntegerField(default=0)
    # SKU: {SUPPLIER(4)}-{CATEGORY(4)}-{SEQUENCE(3)} e.g. NUTR-NUTS-013.
    # Issued once when the product is first saved and not changed by normal edits.
    # (Old-format SKUs were converted with `manage.py reissue_skus`.)
    sku = models.CharField(max_length=20, unique=True, blank=True, db_index=True)
    # Soft delete — supplier deletes hide from supplier/distributor but stay in admin
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Master stock in base unit (grams/ml). All variants auto-calculate from this.
    master_stock_grams = models.PositiveIntegerField(
        verbose_name='Total Stock (base units)',
        default=0,
        help_text='Enter total stock in base units. e.g. 100kg = 100000, 500ml = 500. Leave 0 to manage each variant manually.'
    )
    # Master price per base unit (per gram/ml/unit)
    master_price_per_unit = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        verbose_name='Price per base unit (₹)',
        help_text='Price per gram/ml/unit. e.g. if 1kg costs ₹200, enter 0.2 (₹ per gram)'
    )
    UNIT_CHOICES = (
        ('g', 'Grams (g)'),
        ('ml', 'Millilitres (ml)'),
        ('qty', 'Quantity (pieces)'),
    )
    master_unit = models.CharField(
        max_length=5, choices=UNIT_CHOICES, default='g',
        verbose_name='Stock unit'
    )
    PRICING_MODE_CHOICES = (
        ('auto', 'Auto'),
        ('manual', 'Manual'),
    )
    pricing_mode = models.CharField(
        max_length=6, choices=PRICING_MODE_CHOICES, default='auto',
        verbose_name='Pricing mode'
    )
    show_mrp = models.BooleanField(
        default=False,
        verbose_name='Show MRP',
        help_text='If enabled, MRP strikethrough is shown to customers and distributors cannot exceed MRP'
    )
    # For goods with NO printed MRP sticker: the supplier sets a *suggested*
    # selling price so the shop knows what to charge and keeps it low. Unlike
    # MRP it is guidance, not a hard cap — the shop can price above it (with a
    # gentle warning), it is never enforced or shown to customers as an MRP.
    show_suggested_price = models.BooleanField(
        default=False,
        verbose_name='Show suggested price',
        help_text='Use when the product has no printed MRP. A suggested selling '
                  'price guides the shop to keep prices low (guidance, not a cap).'
    )
    allow_loose_distribution = models.BooleanField(
        default=False,
        verbose_name='Allow Loose Distribution',
        help_text='If enabled, distributors can buy and sell this product in loose/bulk (by weight) without fixed packing'
    )
    allow_bundle = models.BooleanField(
        default=False,
        verbose_name='Allow Bundle',
        help_text='If enabled, each pack size can define a bundle (box/bag of N packs) that marketplace & distributor buyers can order. Hidden from customers.'
    )
    # ── Supplier shipment routing ──────────────────────────────────────────
    # Which level the supplier hands this product to. Normally the stockist
    # (full chain), but a bulky item (e.g. a washing machine) can skip tiers
    # and go straight to the distributor, shop, or even the customer — to
    # avoid repeated load/unload. Any level BEFORE the target is skipped: it
    # must not see the order and must not be notified. The supplier proposes
    # this; ADMIN can override it and sees it at product-approval time.
    DELIVERY_TARGET_CHOICES = (
        ('stockist', 'Stockist (normal chain)'),
        ('distributor', 'Distributor (skip stockist)'),
        ('shop', 'Shop / Marketplace (skip stockist + distributor)'),
        ('customer', 'Customer (deliver direct)'),
    )
    delivery_target = models.CharField(
        max_length=12, choices=DELIVERY_TARGET_CHOICES, default='stockist',
        verbose_name='Supplier ships to',
        help_text='Which level the supplier hands this product to. Levels before '
                  'the target are skipped — they neither see nor are told about the order.'
    )
    # How the handover happens at that target level:
    #  collect — the target party comes and collects after the supplier confirms
    #  send    — the supplier dispatches it and records transhipment details
    SHIPMENT_MODE_CHOICES = (
        ('collect', 'Collected — target party picks up after I confirm'),
        ('send', 'Sent — I dispatch and record transhipment details'),
    )
    shipment_mode = models.CharField(
        max_length=8, choices=SHIPMENT_MODE_CHOICES, default='collect',
        verbose_name='Shipment mode',
        help_text='collect = the next level picks it up after you confirm; '
                  'send = you dispatch it and enter transhipment details.'
    )
    charges_confirmed = models.BooleanField(
        default=False,
        verbose_name='Stockist Charges Confirmed',
        help_text='Set by stockist after adding handling charges. Product becomes visible to distributors only after this.'
    )
    self_distribute_only = models.BooleanField(
        default=False,
        verbose_name='Sell only in my own store',
        help_text='Legacy flag from the old multi-role design. Superseded by '
                  'owner_enterprise — kept so historic rows keep their meaning.'
    )
    # Stage 7: perishables like eggs go straight from the supplier to the
    # marketplace (shop). No stockist processing and no wholesale Distributor
    # margin — neither earns a commission on these. Priced at supplier price,
    # visible to shops without stockist charges, restocked by the supplier.
    direct_supply = models.BooleanField(
        default=False,
        verbose_name='Direct supply (supplier → marketplace)',
        help_text='Delivered by the supplier straight to shops. Skips the '
                  'stockist and the wholesale Distributor — no commissions.'
    )
    # Marketplace (shop) own-product. When set, this product belongs solely to
    # that ONE shop: it shows only in that shop's own store and is invisible to
    # every other Marketplace, the shared catalogue, admin approval and the
    # stockist. A shop that wants to supply the shared catalogue must open a
    # separate supplier account (single-role policy).
    owner_enterprise = models.ForeignKey(
        'distributors.Distributor', on_delete=models.CASCADE,
        null=True, blank=True, related_name='own_products',
        verbose_name='Owned by Marketplace',
        help_text='Set when a shop lists its own product. Private to that shop.'
    )

    objects = SharedProductManager()
    all_objects = AllProductManager()

    class Meta:
        # FK traversal (order item -> product, cart item -> variant -> product)
        # must always resolve, even for shop-private products.
        base_manager_name = 'all_objects'
        indexes = [
            # Measured at 40k products (see PROJECT_NOTES "Stage 5"):
            # the shared-catalogue filter every marketplace page runs went
            # 9.3ms -> 0.7ms, and a shop's own-product list 1.2ms -> 0.05ms.
            models.Index(fields=['status', 'is_deleted', 'owner_enterprise'],
                         name='product_shared_lookup_idx'),
            models.Index(fields=['owner_enterprise', 'is_deleted'],
                         name='product_owner_idx'),
        ]

    @property
    def is_enterprise_owned(self):
        return self.owner_enterprise_id is not None

    def __str__(self):
        return f'[{self.sku}] {self.name}' if self.sku else self.name

    SKU_SEQ_DIGITS = 3      # 001-999 per supplier + category; grows to 4 digits only past 999

    def save(self, *args, **kwargs):
        if self.sku:
            return super().save(*args, **kwargs)
        # New product: issue the next SKU. If another save grabs the same number
        # first, the unique constraint rejects ours and we simply take the next one.
        from django.db import IntegrityError, transaction
        for _attempt in range(10):
            self.sku = self._generate_sku()
            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                taken = Product.all_objects.filter(sku=self.sku).exists()
                self.sku = ''
                if not taken:
                    raise            # a different integrity problem, not a SKU clash
        raise RuntimeError('Could not allocate a unique SKU after several attempts.')

    def _generate_sku(self):
        """Format: {SUPPLIER(4)}-{CATEGORY(4)}-{SEQUENCE(3)}, e.g. NUTR-NUTS-013.

        The sequence is at least 3 digits and simply grows (1000, 1001 ...) if a
        supplier ever has more than 999 products in one category.

        The supplier code is unique per supplier, so the sequence counts that
        supplier's products in that category (no jumps caused by other suppliers).
        all_objects, not objects: shop-private products still occupy SKUs, so
        sequencing must count them too. A clash between two simultaneous saves
        is handled by the retry in save().
        """
        sup_code = (self.supplier.code or 'XXXX').upper()
        cat_code = ((self.category.code if self.category else '') or 'XXXX').upper()[:4].ljust(4, 'X')
        prefix = f'{sup_code}-{cat_code}-'

        used = [
            int(s[len(prefix):])
            for s in Product.all_objects.filter(sku__startswith=prefix).values_list('sku', flat=True)
            if s[len(prefix):].isdigit()
        ]
        return f'{prefix}{(max(used) + 1 if used else 1):0{self.SKU_SEQ_DIGITS}d}'

    def soft_delete(self):
        from django.utils import timezone
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.status = 'rejected'   # hide from storefront
        self.save()

    def restore(self):
        self.is_deleted = False
        self.deleted_at = None
        self.status = 'approved'
        self.save()

    def default_variant(self):
        return self.variants.filter(is_active=True).order_by('selling_price').first()

    def is_in_stock(self):
        return self.variants.filter(is_active=True, stock__gt=0).exists()

    def sync_variant_stocks(self):
        """Recalculate stock and price for all variants that have unit_grams set."""
        if self.master_stock_grams == 0:
            return
        for v in self.variants.filter(is_active=True, unit_grams__gt=0):
            v.stock = self.master_stock_grams // v.unit_grams
            # Auto-calculate price if master price is set
            if self.master_price_per_unit:
                from decimal import Decimal
                v.selling_price = round(Decimal(v.unit_grams) * self.master_price_per_unit, 2)
                # Only set MRP = selling_price if MRP is not already set higher
                # (supplier may have set a custom MRP via show_mrp)
                if not v.mrp or v.mrp <= v.selling_price:
                    v.mrp = v.selling_price
            ProductVariant.objects.filter(pk=v.pk).update(
                stock=v.stock,
                selling_price=v.selling_price,
                mrp=v.mrp,
            )


class ProductVariantType(models.Model):
    """A product variant — a higher layer than pack size.

    Example: "Premium" vs "Regular" basmati rice. Each variant owns its own
    pack sizes (ProductVariant rows), each with its own price, stock, loose
    pool and bundle counts. Toggles like loose/bundle stay on the Product
    (shared); only the per-pack numbers differ per variant.

    A product with only ONE variant leaves its name blank — the storefront then
    shows no variant picker, so single-variant products look unchanged.
    """
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='variant_types'
    )
    name = models.CharField(
        max_length=60, blank=True,
        help_text='Variant name shown to buyers, e.g. "Premium". Leave blank '
                  'for a single-variant product (no picker is shown).'
    )
    # Per-variant loose pool, mirroring Product.master_stock_grams which stays
    # as the fallback for single-variant products.
    master_stock_grams = models.PositiveIntegerField(default=0)
    position = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product', 'position', 'id']
        indexes = [
            models.Index(fields=['product', 'is_active'],
                         name='varianttype_product_active_idx'),
        ]

    def __str__(self):
        return f'{self.product.name} — {self.name or "(default)"}'

    def sync_pack_stocks(self):
        """Recompute each active pack's stock from THIS variant's loose pool.

        For a loose product, a variant holds one shared pool (master_stock_grams)
        and each pack's count is pool // unit_grams. e.g. 5000g pool → 5×1kg,
        10×500g, 20×250g. Packs with no unit size are left untouched.
        """
        if not self.master_stock_grams:
            return
        for pk in self.packs.filter(is_active=True, unit_grams__gt=0):
            new_stock = self.master_stock_grams // pk.unit_grams
            if pk.stock != new_stock:
                ProductVariant.objects.filter(pk=pk.pk).update(stock=new_stock)


class ProductVariant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='variants')
    # The variant this pack belongs to. Nullable + SET_NULL so the migration can
    # backfill safely and existing product.variants queries keep working.
    variant_type = models.ForeignKey(
        'ProductVariantType', on_delete=models.CASCADE,
        related_name='packs', null=True, blank=True
    )
    label = models.CharField(max_length=50)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Suggested retail price shown to the shop when the pack has no MRP sticker
    # (Product.show_suggested_price). Guidance only — never a hard cap.
    suggested_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    unit_grams = models.PositiveIntegerField(
        verbose_name='Unit size (g/ml)',
        default=0,
        help_text='Size of this variant in base units. e.g. 1kg=1000, 500g=500, 250g=250. Set 0 for manual stock.'
    )

    objects = models.Manager.from_queryset(ProductVariantQuerySet)()

    class Meta:
        ordering = ['selling_price']
        indexes = [
            # Storefront and catalogue pages always ask for one product's live
            # packs. Measured at 40k variants: 16.4ms -> 3.9ms on the join.
            models.Index(fields=['product', 'is_active'],
                         name='variant_product_active_idx'),
        ]

    def __str__(self):
        return f'{self.product.name} — {self.label}'

    def discount_percent(self):
        if self.mrp and self.mrp > self.selling_price:
            return int(((self.mrp - self.selling_price) / self.mrp) * 100)
        return 0

    def is_in_stock(self):
        return self.stock > 0

    def clean(self):
        super().clean()
        if self.selling_price is None or self.selling_price <= 0:
            raise ValidationError({'selling_price': 'Selling price must be greater than 0.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        is_new = self.pk is None
        price_changed = False
        if not is_new:
            try:
                old = ProductVariant.objects.get(pk=self.pk)
                price_changed = (old.selling_price != self.selling_price or old.mrp != self.mrp)
            except ProductVariant.DoesNotExist:
                pass
        super().save(*args, **kwargs)
        if is_new:
            VariantPriceHistory.objects.create(
                variant=self, mrp=self.mrp, selling_price=self.selling_price,
                changed_by='supplier', note='Initial price set',
            )
        elif price_changed:
            VariantPriceHistory.objects.create(
                variant=self, mrp=self.mrp, selling_price=self.selling_price,
                changed_by='supplier', note='Price updated by supplier',
            )
            # Mark all distributor prices for this variant as stale using service layer
            try:
                from distributors.services import mark_distributor_prices_stale
                mark_distributor_prices_stale(self.pk, self.selling_price)
            except Exception:
                # Fallback to direct update if service not available (during initial setup)
                try:
                    from distributors.models import DistributorVariantPrice
                    DistributorVariantPrice.objects.filter(variant=self).update(
                        price_stale=True,
                        new_supplier_price=self.selling_price,
                    )
                except Exception:
                    pass


class VariantPriceHistory(models.Model):
    CHANGED_BY_CHOICES = (
        ('supplier', 'Supplier'),
        ('admin', 'Admin'),
        ('system', 'System'),
    )
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE, related_name='price_history')
    mrp = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    changed_by = models.CharField(max_length=10, choices=CHANGED_BY_CHOICES, default='supplier')
    note = models.CharField(max_length=200, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-changed_at']

    def __str__(self):
        return f'{self.variant} @ Rs.{self.selling_price} on {self.changed_at:%d %b %Y}'

    def discount_percent(self):
        if self.mrp and self.mrp > self.selling_price:
            return int(((self.mrp - self.selling_price) / self.mrp) * 100)
        return 0


class ProductBundle(models.Model):
    """A wholesale ordering option: a box/bag of N units of one pack variant.

    Example: a carton of 12 × 1L oil packets, or a 25kg rice bag made of
    25 × 1kg packs. Bundles are orderable ONLY by marketplace (shop) and
    distributor buyers — never shown to end customers.

    A bundle does NOT hold its own stock or price. It is purely a convenience
    way to order N of the underlying pack at once:
      • price  = pack selling_price × pack_quantity   (computed)
      • stock  = how many whole bundles the pack/pool stock can make (computed)
    Ordering one bundle draws down the normal pack/pool stock.
    """
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='bundles'
    )
    # The pack this bundle is made of (e.g. the 1L oil variant). Drives both
    # the computed price and the computed availability.
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, related_name='bundles'
    )
    name = models.CharField(
        max_length=80,
        help_text='Shown to wholesale buyers, e.g. "Box of 12" or "25kg Bag".'
    )
    pack_quantity = models.PositiveIntegerField(
        default=1,
        help_text='How many units of the pack are inside this bundle (e.g. 12).'
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product', 'pack_quantity']
        indexes = [
            models.Index(fields=['product', 'is_active'],
                         name='bundle_product_active_idx'),
        ]

    def __str__(self):
        return f'{self.product.name} — {self.name} ({self.pack_quantity}× {self.variant.label})'

    @property
    def price(self):
        """Computed wholesale price = pack price × packs inside."""
        return round(self.variant.selling_price * self.pack_quantity, 2)

    @property
    def per_unit_price(self):
        """Effective price of one pack inside the bundle (= the pack price)."""
        return self.variant.selling_price

    def available_bundles(self):
        """How many whole bundles the underlying pack stock can make."""
        if not self.pack_quantity:
            return 0
        return self.variant.stock // self.pack_quantity

    def is_in_stock(self):
        return self.is_active and self.available_bundles() > 0


class Wishlist(models.Model):
    user = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='wishlist'
    )
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='wishlisted_by')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')

    def __str__(self):
        return f'{self.user.username} → {self.product.name}'


class ProductReview(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='reviews'
    )
    rating = models.PositiveSmallIntegerField(default=5)  # 1-5
    comment = models.TextField(blank=True)
    is_verified_purchase = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('product', 'user')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.username} rated {self.product.name} {self.rating}★'

    def save(self, *args, **kwargs):
        # Auto-detect if this is a verified purchase when creating the review
        if self.pk is None and not self.is_verified_purchase:
            from orders.models import OrderItem
            has_purchased = OrderItem.objects.filter(
                order__customer=self.user,
                order__status='delivered', # Only count if actually delivered
                product=self.product
            ).exists()
            self.is_verified_purchase = has_purchased
            
        super().save(*args, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# PRODUCT TEMPLATE LIBRARY
# A shared, reusable catalogue of common products (soap, shampoo, rice, dal,
# eggs, water, biscuits, ...). Marketplace (shop) owners search this library
# when adding their OWN products so they don't re-type the same item every time.
#
# Reuse = INDEPENDENT COPY: picking a template pre-fills the own-product form;
# the shop edits price/stock and saves a product PRIVATE to that shop
# (owner_enterprise set). Templates are never linked to the resulting product,
# so one shop editing its copy never affects another shop — this is what makes
# the library safe in a multi-marketplace setup.
#
# The library grows two ways: admin-curated master entries, AND automatically
# from shop own-products (upserted on save, deduped by brand+name).
# ─────────────────────────────────────────────────────────────────────────────
class ProductTemplate(models.Model):
    SOURCE_CHOICES = (
        ('admin', 'Admin curated'),
        ('shop', 'Contributed by a shop'),
        ('supplier', 'From supplier catalogue'),
    )
    name = models.CharField(max_length=200, db_index=True)
    brand = models.CharField(max_length=120, blank=True, db_index=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='templates')
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='templates/', blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='shop')
    # Popularity: how many times a shop has reused this template. Used to rank
    # the most-common products to the top of the picker.
    use_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-use_count', 'name']
        indexes = [
            models.Index(fields=['is_active', 'name'], name='template_active_name_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['brand', 'name'], name='uniq_template_brand_name'),
        ]

    def __str__(self):
        return f'{self.brand} {self.name}'.strip() if self.brand else self.name

    @property
    def display_name(self):
        return f'{self.brand} — {self.name}' if self.brand else self.name


class ProductTemplateVariant(models.Model):
    """A default pack/size on a template (e.g. 500 g, 1 kg). Prices here are
    only SUGGESTED defaults; the shop sets its own price when it reuses."""
    template = models.ForeignKey(
        ProductTemplate, on_delete=models.CASCADE, related_name='variants')
    label = models.CharField(max_length=50)
    unit_grams = models.PositiveIntegerField(default=0)
    suggested_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    suggested_mrp = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ['suggested_price', 'label']

    def __str__(self):
        return f'{self.template} — {self.label}'

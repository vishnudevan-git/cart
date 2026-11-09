from django.db import models
from django.conf import settings


class MarketplaceSupplierManager(models.Manager):
    """Real suppliers only — never an Marketplace's in-house catalogue holder."""

    def get_queryset(self):
        return super().get_queryset().filter(is_house_account=False)


class Supplier(models.Model):
    STATUS_CHOICES = (
        ('approved', 'Approved'),
        ('suspended', 'Suspended'),
    )
    CATEGORY_CHOICES = (
        ('organic', 'Organic Products'),
        ('dry_fruits', 'Dry Fruits'),
        ('nuts', 'Nuts'),
        ('spices', 'Spices'),
        ('other', 'Other'),
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='supplier_profile'
    )
    company_name = models.CharField(max_length=200)
    # 4-char supplier code used in SKU (e.g. NUTR for NutriHub Traders). Unique
    # per supplier and generated from the company name when left blank.
    code = models.CharField(max_length=6, unique=True, blank=True,
                            help_text='Unique supplier code used in SKUs (e.g. NUTR). '
                                      'Generated automatically from the company name.')
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='approved')
    address = models.TextField(blank=True, help_text='Pickup address for stockist')
    # In-house catalogue holder for ONE Marketplace (shop) that lists its own
    # products in its own store. It is NOT a marketplace supplier and NOT a
    # login role — the owning account stays a single-role Marketplace. House
    # accounts are excluded from every supplier-facing list, the marketplace,
    # admin approval and stockist procurement.
    is_house_account = models.BooleanField(default=False)

    # ── Global shipment handover ───────────────────────────────────────────
    # One supplier-wide switch for how goods are handed over. When
    # `use_global_shipment_mode` is on, this GLOBAL value WINS over every
    # product's own `shipment_mode` — the supplier doesn't have to set it per
    # product. `collect` = the target party picks up; `send` = supplier
    # self-dispatches (the RECEIVER bears the pickup/freight cost and adds it
    # in their own login later — no automatic price change here).
    SHIPMENT_MODE_CHOICES = (
        ('collect', 'Allow collect — target party picks up'),
        ('send', 'Self-dispatch — I send it (receiver bears pickup cost)'),
    )
    use_global_shipment_mode = models.BooleanField(
        default=True,
        verbose_name='Use one global shipment mode',
        help_text='When on, the global mode below applies to ALL products and '
                  'overrides each product\u2019s own setting.'
    )
    default_shipment_mode = models.CharField(
        max_length=8, choices=SHIPMENT_MODE_CHOICES, default='collect',
        verbose_name='Global shipment mode'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def effective_shipment_mode(self, product=None):
        """The handover mode that actually applies.

        Global wins when `use_global_shipment_mode` is on; otherwise fall back
        to the product's own `shipment_mode` (or 'collect').
        """
        if self.use_global_shipment_mode:
            return self.default_shipment_mode
        if product is not None:
            return getattr(product, 'shipment_mode', 'collect') or 'collect'
        return 'collect'

    # Default manager = real marketplace suppliers only. An Marketplace's
    # in-house catalogue holder is invisible here, so it never shows up in
    # supplier lists, procurement, reports or admin approval. Use all_objects
    # when you specifically need house accounts.
    objects = MarketplaceSupplierManager()
    all_objects = models.Manager()

    class Meta:
        # FK / OneToOne traversal (product -> supplier, user -> supplier_profile)
        # must resolve for house accounts too.
        base_manager_name = 'all_objects'

    def __str__(self):
        return self.company_name

    def save(self, *args, **kwargs):
        if not self.code:
            from lakrish_mart.codegen import unique_code
            # all_objects: house accounts hold codes too, so they must count.
            self.code = unique_code(
                self.company_name, 4,
                lambda c: Supplier.all_objects.filter(code=c).exclude(pk=self.pk).exists(),
            )
        super().save(*args, **kwargs)

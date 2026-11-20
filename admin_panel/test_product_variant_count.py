"""Admin product list: the Variants column counts only live variants and packs."""
import re

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from products.models import ProductVariant, ProductVariantType


class ProductVariantCountTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.product = self.world.shared_product
        self.client_admin = login_as(self.client, self.world.admin)
        # Shared product already has one unnamed live pack (500 g).
        self.live_type = ProductVariantType.objects.create(product=self.product, name='W450')
        self.dead_type = ProductVariantType.objects.create(
            product=self.product, name='W180', is_active=True)
        for label, grams, active, vt in [
            ('100g', 100, True, self.live_type), ('250g', 250, True, self.live_type),
            ('100g', 100, False, self.dead_type), ('250g', 250, False, self.dead_type),
        ]:
            ProductVariant.objects.create(
                product=self.product, variant_type=vt, label=label, unit_grams=grams,
                selling_price=50, is_active=active, stock=10)

    def _row_text(self):
        page = self.client_admin.get(reverse('admin_panel:product_list')).content.decode()
        cell = re.search(r'<td data-col="variants"[^>]*>\s*(.*?)\s*</td>', page, re.S)
        self.assertIsNotNone(cell)
        return re.sub(r'\s+', ' ', cell.group(1)).strip()

    def test_counts_live_variants_and_live_packs_only(self):
        # Live packs: W450's 2 + the unnamed default 500 g pack = 3.
        # Live variants: W450 only. W180 has no live pack, so it is not counted.
        self.assertEqual(self._row_text(), '1 variant · 3 packs')

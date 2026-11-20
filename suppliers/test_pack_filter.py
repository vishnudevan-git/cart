"""Supplier product list: Stock unit and Pack size filters, with the 50 g rule.

Grams products whose active packs are all 50 g or more are kilogram stock.
A grams product with any pack under 50 g stays in grams.
"""
from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from products.models import Product, ProductVariant


class PackSizeFilterTests(TestCase):
    def setUp(self):
        self.world = build_world()
        self.supplier = self.world.supplier
        # Grams, packs 500 g + 1 kg -> kilogram stock.
        self.big = self.world.shared_product
        ProductVariant.objects.create(
            product=self.big, label='1kg', unit_grams=1000, selling_price=100, stock=5,
        )
        # Grams, one 250 g pack -> kilogram stock.
        self.small = self._product('Small Only', 'g', [('250g', 250)])
        # Grams, a 10 g pack and a 100 g pack -> grams stock (a pack is under 50 g).
        self.tiny = self._product('Tiny Pack', 'g', [('10g', 10), ('100g', 100)])
        # Millilitres, 1 L pack -> not affected by the 50 g rule.
        self.litre = self._product('Oil Litre', 'ml', [('1 L', 1000)])
        self.client_s = login_as(self.client, self.world.supplier_user)
        self.url = reverse('suppliers:product_list')

    def _product(self, name, unit, packs):
        p = Product.objects.create(
            supplier=self.supplier, category=self.world.category, name=name,
            status='approved', charges_confirmed=True, master_unit=unit,
        )
        for label, grams in packs:
            ProductVariant.objects.create(
                product=p, label=label, unit_grams=grams, selling_price=50, stock=5,
            )
        return p

    def _names(self, **params):
        page = self.client_s.get(self.url, params)
        self.assertEqual(page.status_code, 200)
        return sorted(p.name for p in page.context['products']), page

    def test_pack_choices_follow_the_effective_unit(self):
        _, page = self._names()
        self.assertEqual(dict(page.context['pack_choices']), {
            'kg:250': '250g', 'kg:500': '500 g', 'kg:1000': '1kg',
            'g:10': '10g', 'g:100': '100g', 'ml:1000': '1 L',
        })

    def test_stock_unit_kg_offers_only_kg_packs(self):
        _, page = self._names(unit='kg')
        self.assertEqual(sorted(dict(page.context['pack_choices'])), ['kg:1000', 'kg:250', 'kg:500'])

    def test_stock_unit_g_offers_only_grams_packs(self):
        _, page = self._names(unit='g')
        self.assertEqual(sorted(dict(page.context['pack_choices'])), ['g:10', 'g:100'])

    def test_fifty_grams_and_up_lists_under_kilograms(self):
        names, _ = self._names(unit='kg')
        self.assertEqual(names, sorted([self.big.name, self.small.name]))

    def test_product_with_a_small_pack_lists_under_grams(self):
        names, _ = self._names(unit='g')
        self.assertEqual(names, [self.tiny.name])

    def test_pack_filter_matches_its_own_unit(self):
        self.assertEqual(self._names(pack='kg:1000')[0], [self.big.name])
        self.assertEqual(self._names(pack='g:10')[0], [self.tiny.name])
        self.assertEqual(self._names(pack='ml:1000')[0], [self.litre.name])

    def test_1000_ml_pack_is_not_matched_by_1_kg(self):
        self.assertNotIn(self.litre.name, self._names(pack='kg:1000')[0])

    def test_litres_with_a_grams_pack_gives_nothing(self):
        self.assertEqual(self._names(unit='l', pack='kg:1000')[0], [])

    def test_inactive_pack_does_not_match(self):
        ProductVariant.objects.filter(product=self.small).update(is_active=False)
        self.assertEqual(self._names(pack='kg:250')[0], [])

    def test_stock_unit_label_is_shown(self):
        _, page = self._names()
        self.assertContains(page, 'Stock unit: All')
        self.assertContains(page, 'Pack size: All')
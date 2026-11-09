"""End-to-end price-flow regression — the rupee value must change correctly at
EVERY supply stage: Supplier → Stockist charges → wholesale Distributor margin →
Marketplace selling price (with MRP cap + cost floor). Plus direct-supply, which
must skip the stockist AND the Distributor (zero commission for both).

Exercises the REAL get_stockist_price() and the REAL save_product_listing view.
Run with -v2 to see the stage-by-stage rupee trace printed.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.factories import build_world, login_as
from products.models import Product, ProductVariant
from distributors.models import DistributorVariantPrice
from stockist.models import (StockistStage, ProcessingCharge, get_stockist_price,
                             stockist_for)


class PriceFlowTrace(TestCase):
    def test_full_chain(self):
        w = build_world()
        sup, st, wh, shop = w.supplier, w.stockist, w.wholesaler, w.shop_a

        # A 1kg pack, supplier price ₹200, MRP ₹260.
        p = Product.objects.create(
            name='Trace Rice', supplier=sup, category=w.category, status='approved',
            master_unit='g', master_stock_grams=100000, master_price_per_unit=Decimal('0.2'),
            pricing_mode='manual', charges_confirmed=True, show_mrp=True,
        )
        v = ProductVariant.objects.create(
            product=p, label='1kg', selling_price=Decimal('200'), mrp=Decimal('260'),
            stock=100, unit_grams=1000,
        )

        print('\n================ PRODUCT PRICE FLOW (1kg pack) ================')
        # ── STAGE 1: Supplier ──
        print(f'STAGE 1  Supplier price ............. ₹{v.selling_price}')
        self.assertEqual(v.selling_price, Decimal('200'))

        # ── STAGE 2: Stockist processing charges (fixed ₹15 + 10% of supplier) ──
        s1 = StockistStage.objects.create(stockist=st, name='Packing', order=1)
        ProcessingCharge.objects.create(stockist=st, scope='global', stage_obj=s1,
                                        charge_type='fixed', charge_value=Decimal('15'))
        s2 = StockistStage.objects.create(stockist=st, name='Handling', order=2)
        ProcessingCharge.objects.create(stockist=st, scope='global', stage_obj=s2,
                                        charge_type='percent', charge_value=Decimal('10'))

        stockist_price, bd = get_stockist_price(p, v, st)
        detail = ', '.join('{}:+₹{}'.format(b['stage'], b['amount']) for b in bd)
        print(f'STAGE 2  + Stockist charges ......... ₹{stockist_price}   ({detail})')
        # 200 + 15 (fixed) + 20 (10% of 200) = 235
        self.assertEqual(stockist_price, Decimal('235'))

        # ── STAGE 3: Wholesale Distributor margin (₹8/kg by weight) ──
        wh.margin_mode = 'per_weight'
        wh.margin_value = Decimal('8')
        wh.save(update_fields=['margin_mode', 'margin_value'])
        base, bd2 = get_stockist_price(p, v, st, enterprise=shop)
        print(f'STAGE 3  + Distributor margin ....... ₹{base}   '
              f'(marketplace TRUE COST / floor)')
        self.assertEqual(base, Decimal('243'))          # 235 + 8×1kg

        # ── STAGE 4/5: Marketplace sets customer price (real view) ──
        client = login_as(self.client, w.shop_a_user)
        url = reverse('distributors:save_product_listing', args=[p.pk])
        # The shop's pricing pages are gated until it has priced its catalogue
        # once (the "Set Global Pricing first" lock). Seed one existing price so
        # this shop counts as already-configured and the real save runs.
        DistributorVariantPrice.objects.create(
            distributor=shop, variant=w.shared_variant, base_price=Decimal('1'),
            selling_price=Decimal('1'), is_active=True,
        )

        def set_price(raw):
            client.post(url, {'is_listed': '1', 'margin_percent': '',
                              f'selling_price_{v.pk}': str(raw)})
            return DistributorVariantPrice.objects.get(distributor=shop, variant=v)

        normal = set_price('250')
        print(f'STAGE 4  Marketplace sells at ....... ₹{normal.selling_price}   '
              f'(margin over cost = ₹{normal.selling_price - normal.base_price})')
        self.assertEqual(normal.selling_price, Decimal('250'))
        self.assertEqual(normal.base_price, Decimal('243'))
        self.assertEqual(normal.distributor_margin(), Decimal('7'))

        # MRP cap: try ₹300 (> MRP 260) → clamped to 260
        capped = set_price('300')
        print(f'         MRP cap: tried ₹300 ....... ₹{capped.selling_price}  (capped at MRP ₹260)')
        self.assertEqual(capped.selling_price, Decimal('260'))

        # Floor: try ₹100 (< cost 243) → clamped up to 243 (never below cost)
        floored = set_price('100')
        print(f'         Floor:  tried ₹100 ....... ₹{floored.selling_price}  (raised to cost ₹243)')
        self.assertEqual(floored.selling_price, Decimal('243'))

        print('============== customer pays STAGE-4 price ==============\n')

    def test_direct_supply_zero_commission(self):
        w = build_world()
        sup, st, wh, shop = w.supplier, w.stockist, w.wholesaler, w.shop_a
        p = Product.objects.create(
            name='Trace Eggs', supplier=sup, category=w.category, status='approved',
            master_unit='qty', master_stock_grams=1000, master_price_per_unit=Decimal('5'),
            pricing_mode='manual', direct_supply=True,
        )
        v = ProductVariant.objects.create(product=p, label='6pc', selling_price=Decimal('90'),
                                          stock=100, unit_grams=0)
        # Even WITH charges + margin configured, direct supply skips both.
        s1 = StockistStage.objects.create(stockist=st, name='Packing', order=1)
        ProcessingCharge.objects.create(stockist=st, scope='global', stage_obj=s1,
                                        charge_type='fixed', charge_value=Decimal('15'))
        wh.margin_mode = 'per_quantity'; wh.margin_value = Decimal('10')
        wh.save(update_fields=['margin_mode', 'margin_value'])

        cost, bd = get_stockist_price(p, v, st, enterprise=shop)
        print('\n=========== DIRECT SUPPLY (eggs) — zero commission ===========')
        print(f'Supplier ₹{v.selling_price} → marketplace cost ₹{cost}  '
              f'(charges applied: {len(bd)}) — stockist & distributor earn ₹0')
        print('==============================================================\n')
        self.assertEqual(cost, Decimal('90'))    # unchanged — no charge, no margin
        self.assertEqual(bd, [])

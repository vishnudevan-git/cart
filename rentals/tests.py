"""
Stage 8 — Rent: the invariants that must never rot.

* The admin master switch gates EVERYTHING — off means no rent page works.
* The wallet is real money (cash-loaded), separate from reward points; rent is
  paid from it and can't be spent into the negative.
* A unit is never double-booked; owner block-days and full days are refused.
* The renter — never the shop — pays for damage or loss.
* Reject / cancel-before-start refunds the wallet in full.
* Zero commission: a customer-owner receives the full rent into their wallet.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.factories import build_world, login_as
from admin_panel.models import SiteSettings
from .models import Wallet, RentalItem, RentalBlock, RentalBooking
from . import services


def enable_rentals(on=True):
    site = SiteSettings.get()
    site.rentals_enabled = on
    site.save(update_fields=['rentals_enabled'])


class RentTestCase(TestCase):
    def setUp(self):
        self.world = build_world()
        self.shop = self.world.shop_a
        # customer_a belongs to shop_a (from build_world)
        self.cust = self.world.customer_a
        enable_rentals(True)

    def make_item(self, owner_kind='shop', owner=None, rent='100', cost='5000',
                  units=1, status='approved'):
        return RentalItem.objects.create(
            distributor=self.shop, owner=owner or self.shop.user, owner_kind=owner_kind,
            name='Cleaning Machine', category='cleaning',
            rent_per_day=Decimal(rent), item_cost=Decimal(cost), units=units, status=status,
        )


class MasterSwitchTests(RentTestCase):
    def test_off_blocks_new_rental_pages(self):
        enable_rentals(False)
        client = login_as(self.client, self.cust)
        # NEW-rental surfaces are blocked when the switch is off.
        for name in ('rentals:customer_list', 'rentals:customer_publish'):
            resp = client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, f'{name} should redirect when rentals are off')
        shop_client = login_as(self.client_class(), self.shop.user)
        self.assertEqual(shop_client.get(reverse('rentals:shop_items')).status_code, 302)

    def test_off_still_allows_closing_out_bookings(self):
        """Turning off must NOT strand in-flight bookings — the booking pages
        stay reachable and an open booking can still be cancelled/refunded."""
        item = self.make_item(rent='100')
        self.start = timezone.localdate() + timedelta(days=1)
        services.credit_wallet(self.cust, self.shop, Decimal('500'), 'load')
        booking = services.create_booking(item, self.cust, 'customer', self.start, 2)
        enable_rentals(False)

        client = login_as(self.client, self.cust)
        self.assertEqual(client.get(reverse('rentals:customer_bookings')).status_code, 200,
                         'my-rentals must be reachable when rentals are off')
        client.post(reverse('rentals:customer_bookings'),
                    {'booking_id': booking.pk, 'action': 'cancel'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('500'),
                         'wallet must be refunded even with the switch off')

    def test_on_opens_the_pages(self):
        client = login_as(self.client, self.cust)
        self.assertEqual(client.get(reverse('rentals:customer_list')).status_code, 200)

    def test_admin_toggle_flips_the_switch(self):
        enable_rentals(False)
        admin = login_as(self.client, self.world.admin)
        admin.post(reverse('rentals:admin_rentals'), {'rentals_enabled': '1'})
        self.assertTrue(SiteSettings.get().rentals_enabled)
        admin.post(reverse('rentals:admin_rentals'), {'rentals_enabled': '0'})
        self.assertFalse(SiteSettings.get().rentals_enabled)


class WalletTests(RentTestCase):
    def test_load_then_balance(self):
        services.credit_wallet(self.cust, self.shop, Decimal('500'), 'load')
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('500'))

    def test_cannot_overspend(self):
        services.credit_wallet(self.cust, self.shop, Decimal('100'), 'load')
        with self.assertRaises(ValueError):
            services.debit_wallet(self.cust, self.shop, Decimal('150'), 'rent_pay')
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('100'))

    def test_wallet_is_separate_from_reward_points(self):
        """Loading a wallet must not touch the reward-points balance."""
        from distributors.reward_models import RewardPoints
        services.credit_wallet(self.cust, self.shop, Decimal('500'), 'load')
        self.assertFalse(
            RewardPoints.objects.filter(customer=self.cust, distributor=self.shop).exists(),
            'wallet load leaked into reward points'
        )


class BookingTests(RentTestCase):
    def setUp(self):
        super().setUp()
        self.item = self.make_item(rent='100', units=1)
        self.start = timezone.localdate() + timedelta(days=1)
        services.credit_wallet(self.cust, self.shop, Decimal('1000'), 'load')

    def test_booking_debits_the_wallet(self):
        b = services.create_booking(self.item, self.cust, 'customer', self.start, 3)
        self.assertEqual(b.amount, Decimal('300'))
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('700'))
        self.assertTrue(b.paid_from_wallet)

    def test_insufficient_wallet_blocks_and_rolls_back(self):
        poor = self.world.customer_b            # no wallet loaded, but belongs to shop_b
        # move poor into this shop for the test
        poor.distributor = self.shop; poor.save(update_fields=['distributor'])
        with self.assertRaises(ValueError):
            services.create_booking(self.item, poor, 'customer', self.start, 3)
        # the booking row must not survive the rollback
        self.assertFalse(RentalBooking.objects.filter(renter=poor).exists())

    def test_no_double_booking_beyond_units(self):
        services.create_booking(self.item, self.cust, 'customer', self.start, 2)
        services.credit_wallet(self.world.customer_b, self.shop, Decimal('500'), 'load')
        with self.assertRaises(ValueError):
            services.create_booking(self.item, self.world.customer_b, 'customer', self.start, 1)

    def test_two_units_allow_two_bookings(self):
        item2 = self.make_item(units=2)
        services.create_booking(item2, self.cust, 'customer', self.start, 1)
        services.credit_wallet(self.world.customer_b, self.shop, Decimal('500'), 'load')
        b = services.create_booking(item2, self.world.customer_b, 'customer', self.start, 1)
        self.assertEqual(b.status, 'requested')

    def test_blocked_day_refuses_booking(self):
        RentalBlock.objects.create(item=self.item, day=self.start)
        with self.assertRaises(ValueError):
            services.create_booking(self.item, self.cust, 'customer', self.start, 1)

    def test_reject_refunds_wallet(self):
        b = services.create_booking(self.item, self.cust, 'customer', self.start, 3)
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('700'))
        services.refund_booking(b)
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('1000'))


class CompletionMoneyTests(RentTestCase):
    """New flow: money transfers to the owner when the period is over (no
    handover/return steps). Unconfirmed requests past their start auto-refund."""

    def setUp(self):
        super().setUp()
        self.start = timezone.localdate() + timedelta(days=1)

    def test_customer_owner_earns_full_rent_no_commission(self):
        owner = self.world.customer_b
        owner.distributor = self.shop; owner.save(update_fields=['distributor'])
        item = self.make_item(owner_kind='customer', owner=owner, rent='100')
        services.credit_wallet(self.cust, self.shop, Decimal('1000'), 'load')
        b = services.create_booking(item, self.cust, 'customer', self.start, 2)

        services.settle_completion(b)

        b.refresh_from_db()
        self.assertEqual(b.status, 'completed')
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('800'))
        self.assertEqual(services.wallet_balance(owner, self.shop), Decimal('200'))

    def test_settle_due_pays_owner_after_period_ends(self):
        owner = self.world.customer_b
        owner.distributor = self.shop; owner.save(update_fields=['distributor'])
        item = self.make_item(owner_kind='customer', owner=owner, rent='100')
        services.credit_wallet(self.cust, self.shop, Decimal('1000'), 'load')
        past = timezone.localdate() - timedelta(days=5)
        b = services.create_booking(item, self.cust, 'customer', past, 2)   # ended days ago
        b.status = 'confirmed'; b.save(update_fields=['status'])

        done, expired = services.settle_due(RentalBooking.objects.filter(pk=b.pk))

        b.refresh_from_db()
        self.assertEqual((done, b.status), (1, 'completed'))
        self.assertEqual(services.wallet_balance(owner, self.shop), Decimal('200'))

    def test_settle_due_refunds_unconfirmed_past_start(self):
        item = self.make_item(rent='100')
        services.credit_wallet(self.cust, self.shop, Decimal('500'), 'load')
        past = timezone.localdate() - timedelta(days=2)
        b = services.create_booking(item, self.cust, 'customer', past, 1)   # never confirmed
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('400'))

        done, expired = services.settle_due(RentalBooking.objects.filter(pk=b.pk))

        b.refresh_from_db()
        self.assertEqual((expired, b.status), (1, 'cancelled'))
        self.assertEqual(services.wallet_balance(self.cust, self.shop), Decimal('500'))


class IsolationTests(RentTestCase):
    def test_customer_sees_only_their_own_shops_items(self):
        mine = self.make_item()
        other_shop = self.world.shop_b
        RentalItem.objects.create(
            distributor=other_shop, owner=other_shop.user, owner_kind='shop',
            name='Other Drill', rent_per_day=Decimal('50'), item_cost=Decimal('1000'), status='approved',
        )
        client = login_as(self.client, self.cust)
        body = client.get(reverse('rentals:customer_list')).content.decode()
        self.assertIn('Cleaning Machine', body)
        self.assertNotIn('Other Drill', body)

    def test_customer_publish_needs_approval(self):
        client = login_as(self.client, self.cust)
        client.post(reverse('rentals:customer_publish'), {
            'name': 'My Ladder', 'category': 'tool',
            'rent_per_day': '30', 'item_cost': '800', 'units': '1',
        })
        item = RentalItem.objects.get(name='My Ladder')
        self.assertEqual(item.status, 'pending')
        self.assertEqual(item.owner_kind, 'customer')
        # not visible to other customers until approved
        self.assertFalse(item.is_live)


class DistributorTests(RentTestCase):
    """The wholesale Distributor rents from its shops (offline) and publishes."""

    def setUp(self):
        super().setUp()
        # shop_a is supplied by the world's wholesaler (from build_world setUp
        # in stockist tests; here we wire it explicitly).
        self.shop.wholesaler = self.world.wholesaler
        self.shop.save(update_fields=['wholesaler'])
        self.wuser = self.world.wholesaler_user
        self.start = timezone.localdate() + timedelta(days=1)

    def test_distributor_rents_offline_no_wallet_move(self):
        item = self.make_item(rent='300')
        client = login_as(self.client, self.wuser)
        client.post(reverse('rentals:dist_item', args=[item.pk]),
                    {'start_day': self.start.isoformat(), 'days': '2'})
        b = RentalBooking.objects.get(item=item, renter=self.wuser)
        self.assertEqual(b.renter_kind, 'distributor')
        self.assertFalse(b.paid_from_wallet)
        self.assertEqual(b.amount, Decimal('600'))
        # a Distributor has no wallet — none was created
        self.assertFalse(Wallet.objects.filter(customer=self.wuser).exists())

    def test_distributor_publishes_into_shop_pending(self):
        client = login_as(self.client, self.wuser)
        client.post(reverse('rentals:dist_publish'), {
            'shop_id': self.shop.pk, 'name': 'Concrete Mixer', 'category': 'machine',
            'rent_per_day': '400', 'item_cost': '25000', 'units': '1',
        })
        item = RentalItem.objects.get(name='Concrete Mixer')
        self.assertEqual(item.owner_kind, 'distributor')
        self.assertEqual(item.status, 'pending')
        self.assertEqual(item.distributor, self.shop)

    def test_business_renter_offline_never_credits_owner_wallet(self):
        """A customer-owned item rented by the Distributor (offline) must NOT
        credit the owner's wallet — no in-system money moved."""
        owner = self.world.customer_b
        owner.distributor = self.shop; owner.save(update_fields=['distributor'])
        item = self.make_item(owner_kind='customer', owner=owner, rent='100')
        b = services.create_booking(item, self.wuser, 'distributor', self.start, 2)
        services.settle_completion(b)
        self.assertEqual(services.wallet_balance(owner, self.shop), Decimal('0'))

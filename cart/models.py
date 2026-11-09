from django.db import models
from django.conf import settings
from decimal import Decimal, ROUND_HALF_UP
from products.models import ProductVariant


def _round_rupee(value):
    return Decimal(str(value or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


class Cart(models.Model):
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart_items'
    )
    variant = models.ForeignKey(ProductVariant, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ('customer', 'variant')

    def _current_stockist_floor(self, distributor=None):
        from stockist.models import get_stockist_price, stockist_for

        # Stage 7: the stockist is resolved through the shop's Distributor chain.
        active_stockist = stockist_for(distributor)
        # `enterprise=distributor` adds the wholesale Distributor's margin to the
        # floor, so a customer is never charged below the Marketplace's true cost.
        if not active_stockist:
            cost, _ = get_stockist_price(self.variant.product, self.variant, None, enterprise=distributor)
            return cost

        stockist_price, _ = get_stockist_price(
            self.variant.product, self.variant, active_stockist, enterprise=distributor
        )
        return stockist_price

    def subtotal(self, distributor=None):
        """Use distributor price if available, else base variant price."""
        floor_price = self._current_stockist_floor(distributor)
        if distributor:
            dvp = self.variant.dist_prices.filter(distributor=distributor, is_active=True).first()
            if dvp:
                unit = _round_rupee(max(dvp.selling_price, floor_price))
                return unit * self.quantity
        unit = _round_rupee(max(self.variant.selling_price, floor_price))
        return unit * self.quantity

    def unit_price(self, distributor=None):
        floor_price = self._current_stockist_floor(distributor)
        if distributor:
            dvp = self.variant.dist_prices.filter(distributor=distributor, is_active=True).first()
            if dvp:
                return _round_rupee(max(dvp.selling_price, floor_price))
        return _round_rupee(max(self.variant.selling_price, floor_price))

    @property
    def product(self):
        return self.variant.product

from django.db import models
from django.conf import settings


class RewardConfig(models.Model):
    """Distributor enables/disables reward points. 1 point = ₹1."""
    distributor = models.OneToOneField(
        'distributors.Distributor', on_delete=models.CASCADE, related_name='reward_config'
    )
    is_enabled = models.BooleanField(default=False)
    points_earn_percent = models.PositiveIntegerField(
        default=5,
        help_text='% of distributor margin automatically given as points on delivery'
    )
    min_points_to_redeem = models.PositiveIntegerField(default=10,
        help_text='Minimum points before customer can redeem')
    max_redeem_percent = models.PositiveIntegerField(default=50,
        help_text='Max % of order value payable with points')
    welcome_bonus_enabled = models.BooleanField(default=False,
        help_text='Auto-give points to new customers on registration')
    welcome_bonus_points = models.PositiveIntegerField(default=0,
        help_text='Points given to new customers on registration')

    def __str__(self):
        return f'{self.distributor.company_name} — Rewards {"ON" if self.is_enabled else "OFF"}'


class RewardPoints(models.Model):
    """Points balance per customer per distributor. 1 point = ₹1."""
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reward_points'
    )
    distributor = models.ForeignKey(
        'distributors.Distributor', on_delete=models.CASCADE, related_name='customer_rewards'
    )
    balance = models.IntegerField(default=0)
    total_earned = models.IntegerField(default=0)
    total_redeemed = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('customer', 'distributor')

    def __str__(self):
        return f'{self.customer.username} @ {self.distributor.company_name} — ₹{self.balance}'


class RewardTransaction(models.Model):
    """Ledger of all point transactions."""
    TYPE_CHOICES = (
        ('allocated', 'Allocated by Distributor'),
        ('redeemed', 'Redeemed by Customer'),
        ('adjusted', 'Manual Adjustment'),
    )
    reward = models.ForeignKey(RewardPoints, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    points = models.IntegerField()  # positive = given, negative = redeemed
    order = models.ForeignKey(
        'orders.Order', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reward_transactions'
    )
    note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.reward.customer.username} {self.transaction_type} ₹{self.points}'

"""Reward points helpers. 1 point = ₹1."""
from .reward_models import RewardConfig, RewardPoints, RewardTransaction


def get_balance(customer, distributor):
    obj, _ = RewardPoints.objects.get_or_create(
        customer=customer, distributor=distributor,
        defaults={'balance': 0, 'total_earned': 0, 'total_redeemed': 0}
    )
    return obj


def allocate_points(distributor, customer, points, order=None, note=''):
    """Distributor gives points to customer."""
    if points <= 0:
        return
    balance = get_balance(customer, distributor)
    balance.balance += points
    balance.total_earned += points
    balance.save()
    RewardTransaction.objects.create(
        reward=balance, transaction_type='allocated',
        points=points, order=order,
        note=note or 'Allocated by distributor'
    )


def earn_points(order):
    """Auto-called on delivery. Points = earn_percent % of distributor margin."""
    try:
        dist = order.distributor
        config = dist.reward_config
        if not config.is_enabled or config.points_earn_percent <= 0:
            return 0
    except (AttributeError, RewardConfig.DoesNotExist):
        return 0

    balance = get_balance(order.customer, dist)
    already = RewardTransaction.objects.filter(
        reward=balance, order=order, transaction_type='allocated'
    ).exists()
    if already:
        return 0

    from decimal import Decimal
    margin = sum(item.distributor_margin() for item in order.items.all())
    points = int(Decimal(str(margin)) * config.points_earn_percent / 100)
    if points <= 0:
        return 0

    allocate_points(dist, order.customer, points, order=order,
                    note=f'Auto-earned on delivery of order #{order.pk}')
    return points


def redeem_points(customer, distributor, points, order=None):
    """Customer redeems points at checkout. Returns ₹ discount."""
    if points <= 0:
        return 0
    try:
        config = distributor.reward_config
        if not config.is_enabled:
            return 0
    except RewardConfig.DoesNotExist:
        return 0

    balance = get_balance(customer, distributor)
    points = min(points, balance.balance)
    if points <= 0:
        return 0

    balance.balance -= points
    balance.total_redeemed += points
    balance.save()
    RewardTransaction.objects.create(
        reward=balance, transaction_type='redeemed',
        points=-points, order=order,
        note=f'Redeemed ₹{points} at checkout'
    )
    return points


def get_redeemable_info(customer, distributor, order_amount):
    """Return redemption info for checkout page."""
    try:
        config = distributor.reward_config
        if not config.is_enabled:
            return None
    except RewardConfig.DoesNotExist:
        return None

    balance = get_balance(customer, distributor)
    if balance.balance < config.min_points_to_redeem:
        return {
            'balance': balance.balance,
            'min_to_redeem': config.min_points_to_redeem,
            'can_redeem': False,
        }

    from decimal import Decimal
    max_by_percent = int(Decimal(str(order_amount)) * config.max_redeem_percent / 100)
    max_points = min(balance.balance, max_by_percent)

    return {
        'balance': balance.balance,
        'min_to_redeem': config.min_points_to_redeem,
        'can_redeem': True,
        'max_points': max_points,
        'rate_num': 1,
    }


def give_welcome_bonus(customer, distributor):
    """Give welcome bonus points to a new customer on registration."""
    try:
        config = distributor.reward_config
        if not config.is_enabled or not config.welcome_bonus_enabled or config.welcome_bonus_points <= 0:
            return 0
    except Exception:
        return 0
    allocate_points(distributor, customer, config.welcome_bonus_points,
                    note=f'Welcome bonus on registration')
    return config.welcome_bonus_points


def reverse_order_rewards(order):
    """Undo reward effects when an order is cancelled after redemption or allocation."""
    dist = getattr(order, 'distributor', None)
    if not dist:
        return

    balance = get_balance(order.customer, dist)
    if balance.transactions.filter(order=order, transaction_type='adjusted').exists():
        return

    allocated = sum(
        tx.points for tx in balance.transactions.filter(order=order, transaction_type='allocated')
    )
    redeemed = sum(
        -tx.points for tx in balance.transactions.filter(order=order, transaction_type='redeemed')
    )

    if allocated > 0:
        balance.balance = max(0, balance.balance - allocated)
        balance.total_earned = max(0, balance.total_earned - allocated)
        RewardTransaction.objects.create(
            reward=balance,
            transaction_type='adjusted',
            points=-allocated,
            order=order,
            note=f'Reversal of earned points for cancelled order #{order.pk}',
        )

    if redeemed > 0:
        balance.balance += redeemed
        balance.total_redeemed = max(0, balance.total_redeemed - redeemed)
        RewardTransaction.objects.create(
            reward=balance,
            transaction_type='adjusted',
            points=redeemed,
            order=order,
            note=f'Restored redeemed points for cancelled order #{order.pk}',
        )

    if allocated > 0 or redeemed > 0:
        balance.save()

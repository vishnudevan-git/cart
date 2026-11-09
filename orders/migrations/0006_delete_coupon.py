from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_orderitem_fulfillment_status_order_partial'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Coupon',
        ),
    ]

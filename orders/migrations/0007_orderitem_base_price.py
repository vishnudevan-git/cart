# Generated manually to add base_price field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_delete_coupon'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='base_price',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]

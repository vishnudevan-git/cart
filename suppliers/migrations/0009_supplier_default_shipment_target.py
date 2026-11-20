from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('suppliers', '0008_alter_supplier_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='supplier',
            name='default_shipment_target',
            field=models.CharField(
                choices=[
                    ('stockist', 'Stockist (normal chain)'),
                    ('distributor', 'Distributor (skip stockist)'),
                    ('shop', 'Shop / Marketplace (skip stockist + distributor)'),
                    ('customer', 'Customer (deliver direct)'),
                ],
                default='stockist',
                max_length=12,
                verbose_name='Global ship-to target',
            ),
        ),
    ]

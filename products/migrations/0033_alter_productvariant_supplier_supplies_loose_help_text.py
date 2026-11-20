from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0032_productvariant_supplier_order_multiple_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='productvariant',
            name='supplier_supplies_loose',
            field=models.BooleanField(
                default=False,
                help_text=(
                    'When product loose mode is off, this pack is supplied only in its configured '
                    'bundle quantity. Product loose mode replaces this per-pack setting.'
                ),
            ),
        ),
    ]

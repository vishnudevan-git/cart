"""Category codes become 3 characters (was 2): DF -> DRY, NU -> NUT, OR -> ORG ...

The column is widened, then every category gets a unique 3-character code built
from its name. Only FUTURE SKUs use the new codes; SKUs already issued keep the
old code inside them.
"""
from django.db import migrations, models


def backfill_codes(apps, schema_editor):
    from lakrish_mart.codegen import unique_code, initials_code
    Category = apps.get_model('products', 'Category')
    taken = {c.code for c in Category.objects.all() if len(c.code) == 3}
    for c in Category.objects.order_by('pk'):
        if len(c.code) == 3:
            continue
        label = c.name or c.slug
        code = unique_code(label, 3, lambda x: x in taken, alternatives=[initials_code(label, 3)])
        taken.add(code)
        Category.objects.filter(pk=c.pk).update(code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0023_product_delivery_target_product_shipment_mode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='code',
            field=models.CharField(blank=True, help_text='3-char code for SKU (e.g. NUT, DRY, ORG, SPI). Generated automatically from the name.', max_length=3, unique=True),
        ),
        migrations.RunPython(backfill_codes, migrations.RunPython.noop),
    ]

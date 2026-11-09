"""Category codes become 4 characters (was 3): NUT -> NUTS, DRY -> DRYF, ORG -> ORGA ...

The column is widened, then every category whose code is not yet 4 characters
gets a unique 4-character code built from its name. Only FUTURE SKUs use the
new codes; SKUs already issued are not touched.
"""
from django.db import migrations, models


def backfill_codes(apps, schema_editor):
    from lakrish_mart.codegen import unique_code, initials_code
    Category = apps.get_model('products', 'Category')
    taken = {c.code for c in Category.objects.all() if len(c.code) == 4}
    for c in Category.objects.order_by('pk'):
        if len(c.code) == 4:
            continue
        label = c.name or c.slug
        code = unique_code(label, 4, lambda x: x in taken, alternatives=[initials_code(label, 4)])
        taken.add(code)
        Category.objects.filter(pk=c.pk).update(code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0024_alter_category_code'),
    ]

    operations = [
        migrations.AlterField(
            model_name='category',
            name='code',
            field=models.CharField(blank=True, help_text='4-char code for SKU (e.g. NUTS, DRYF, ORGA, SPIC). Generated automatically from the name.', max_length=4, unique=True),
        ),
        migrations.RunPython(backfill_codes, migrations.RunPython.noop),
    ]

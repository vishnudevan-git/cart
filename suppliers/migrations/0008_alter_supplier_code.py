"""Supplier codes become 4 characters and unique.

1. widen the column (still not unique, so existing duplicates like 'NT' are fine),
2. give every supplier a unique 4-character code built from its company name,
3. only then add the unique constraint.

Only FUTURE SKUs use the new codes; SKUs already issued are not touched.
"""
from django.db import migrations, models


def backfill_codes(apps, schema_editor):
    from lakrish_mart.codegen import unique_code
    Supplier = apps.get_model('suppliers', 'Supplier')
    keep = set()
    todo = []
    for s in Supplier.objects.order_by('pk'):
        if s.code and len(s.code) == 4 and s.code not in keep:
            keep.add(s.code)          # already in the new shape and unique: leave it
        else:
            todo.append(s)
    taken = set(keep)
    for s in todo:
        code = unique_code(s.company_name, 4, lambda c: c in taken)
        taken.add(code)
        Supplier.objects.filter(pk=s.pk).update(code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('suppliers', '0007_supplier_default_shipment_mode_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='supplier',
            name='code',
            field=models.CharField(blank=True, max_length=6),
        ),
        migrations.RunPython(backfill_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='supplier',
            name='code',
            field=models.CharField(blank=True, help_text='Unique supplier code used in SKUs (e.g. NUTR). Generated automatically from the company name.', max_length=6, unique=True),
        ),
    ]

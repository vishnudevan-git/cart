from django.db import migrations, models


def copy_bundle_only_flags(apps, schema_editor):
    ProductVariant = apps.get_model('products', 'ProductVariant')
    ProductBundle = apps.get_model('products', 'ProductBundle')
    for variant in ProductVariant.objects.filter(
        supplier_supplies_loose=True,
        product__allow_loose_distribution=False,
        product__allow_bundle=True,
    ).iterator():
        if ProductBundle.objects.filter(
            variant_id=variant.pk,
            is_active=True,
            pack_quantity__gt=1,
        ).exists():
            ProductVariant.objects.filter(pk=variant.pk).update(only_bundle=True)


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0033_alter_productvariant_supplier_supplies_loose_help_text'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariant',
            name='only_bundle',
            field=models.BooleanField(
                default=False,
                help_text='When enabled, downstream wholesale orders for this pack must use the configured bundle quantity.',
            ),
        ),
        migrations.RunPython(copy_bundle_only_flags, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='productvariant',
            name='supplier_order_multiple',
        ),
        migrations.RemoveField(
            model_name='productvariant',
            name='supplier_supplies_loose',
        ),
    ]

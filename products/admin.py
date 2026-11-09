from django.contrib import admin
from .models import (
    Product, Category, ProductVariant,
    ProductTemplate, ProductTemplateVariant,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'supplier', 'category', 'status']
    list_filter = ['status', 'category']
    inlines = [ProductVariantInline]


class ProductTemplateVariantInline(admin.TabularInline):
    model = ProductTemplateVariant
    extra = 1


@admin.register(ProductTemplate)
class ProductTemplateAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'category', 'source', 'use_count', 'is_active']
    list_filter = ['source', 'is_active', 'category']
    search_fields = ['name', 'brand']
    inlines = [ProductTemplateVariantInline]

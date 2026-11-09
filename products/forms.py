from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from .models import Product, ProductVariant


def _bootstrap(form):
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'form-control')


class ProductForm(forms.ModelForm):
    # Human-friendly stock entry
    pricing_mode_field = forms.CharField(widget=forms.HiddenInput(), required=False, initial='auto')
    stock_amount = forms.DecimalField(
        label='Total Stock', min_value=0, required=False,
        widget=forms.NumberInput(attrs={'placeholder': 'e.g. 100', 'step': '0.001'}),
        help_text='How much stock do you have in total?'
    )
    stock_unit = forms.ChoiceField(
        label='Stock Unit',
        choices=[
            ('kg', 'Kilograms (kg)'),
            ('g', 'Grams (g)'),
            ('litre', 'Litres (L)'),
            ('ml', 'Millilitres (ml)'),
            ('qty', 'Pieces / Quantity'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    price_per_kg = forms.DecimalField(
        label='Price per kg / litre / 100 pieces (₹)',
        min_value=0, required=False,
        widget=forms.NumberInput(attrs={'placeholder': 'e.g. 200', 'step': '0.01'}),
        help_text='Enter your selling price per kg, per litre, or per 100 pieces.'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _bootstrap(self)
        # Pre-fill human fields from stored values
        if self.instance and self.instance.pk:
            ms = self.instance.master_stock_grams  # stored in base units (g / ml / qty)
            mu = self.instance.master_unit          # 'g', 'ml', or 'qty'
            mp = self.instance.master_price_per_unit

            # Use self.initial dict — this is what Django's BoundField.value() reads for unbound fields
            # Always pre-fill even when value is 0 (show "0", not blank placeholder)
            if mu == 'g':
                self.initial['stock_amount'] = round(ms / 1000, 3) if ms else 0
                self.initial['stock_unit'] = 'kg'
            elif mu == 'ml':
                self.initial['stock_amount'] = round(ms / 1000, 3) if ms else 0
                self.initial['stock_unit'] = 'litre'
            else:  # qty
                self.initial['stock_amount'] = ms if ms is not None else 0
                self.initial['stock_unit'] = 'qty'

            # Always pre-fill price — mp can be 0, so check for None explicitly
            if mp is not None:
                if mu == 'g':
                    self.initial['price_per_kg'] = round(float(mp) * 1000, 2)
                elif mu == 'ml':
                    self.initial['price_per_kg'] = round(float(mp) * 1000, 2)
                else:
                    self.initial['price_per_kg'] = round(float(mp) * 100, 2)

    class Meta:
        model = Product
        fields = ['name', 'category', 'description', 'image']
        widgets = {'description': forms.Textarea(attrs={'rows': 3})}

    def save(self, commit=True):
        product = super().save(commit=False)
        from decimal import Decimal
        amount = self.cleaned_data.get('stock_amount') or 0
        unit = self.cleaned_data.get('stock_unit', 'kg')
        price_per_big = self.cleaned_data.get('price_per_kg') or 0

        # Convert to base units (grams/ml/qty)
        if unit == 'kg':
            product.master_stock_grams = int(float(amount) * 1000)
            product.master_unit = 'g'
            product.master_price_per_unit = Decimal(str(price_per_big)) / 1000
        elif unit == 'g':
            product.master_stock_grams = int(float(amount))
            product.master_unit = 'g'
            product.master_price_per_unit = Decimal(str(price_per_big)) / 1000
        elif unit == 'litre':
            product.master_stock_grams = int(float(amount) * 1000)
            product.master_unit = 'ml'
            product.master_price_per_unit = Decimal(str(price_per_big)) / 1000
        elif unit == 'ml':
            product.master_stock_grams = int(float(amount))
            product.master_unit = 'ml'
            product.master_price_per_unit = Decimal(str(price_per_big)) / 1000
        else:  # qty
            product.master_stock_grams = int(float(amount))
            product.master_unit = 'qty'
            product.master_price_per_unit = Decimal(str(price_per_big)) / 100

        # Save pricing mode from hidden field
        mode = self.cleaned_data.get('pricing_mode_field', 'auto')
        product.pricing_mode = mode if mode in ('auto', 'manual') else 'auto'

        if commit:
            product.save()
        return product


class ProductVariantForm(forms.ModelForm):
    def __init__(self, *args, enforce_suggested=True, **kwargs):
        # enforce_suggested: apply the "suggested price >= supplier price" rule.
        # The variant formset turns it off when the product is not using a
        # suggested price at all (then the value is ignored anyway).
        self.enforce_suggested = enforce_suggested
        super().__init__(*args, **kwargs)
        _bootstrap(self)
        self.fields['is_active'].widget.attrs['class'] = 'form-check-input'

    class Meta:
        model = ProductVariant
        fields = ['label', 'selling_price', 'mrp', 'suggested_price', 'stock', 'unit_grams', 'is_active']
        widgets = {
            'label': forms.TextInput(attrs={'readonly': 'readonly', 'class': 'form-control bg-light'}),
            'selling_price': forms.NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control bg-light'}),
            'mrp': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'suggested_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'stock': forms.NumberInput(attrs={'readonly': 'readonly', 'class': 'form-control bg-light'}),
            'unit_grams': forms.HiddenInput(),
        }

    def clean(self):
        cleaned = super().clean()
        price = cleaned.get('selling_price')
        suggested = cleaned.get('suggested_price')
        # The suggested retail price guides the shop, so it can never sit
        # below what the supplier charges for that pack.
        if (self.enforce_suggested and price is not None
                and suggested is not None and suggested < price):
            self.add_error(
                'suggested_price',
                f'Suggested price (\u20b9{suggested}) cannot be less than the supplier price (\u20b9{price}).'
            )
        return cleaned


class BaseVariantFormSet(BaseInlineFormSet):
    """Enforce the suggested-price rule only when the product actually uses a
    suggested price (its flag is on). MRP and suggested price may now both be
    enabled together — the rule applies whenever a suggested price is shown."""

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        data = self.data if self.is_bound else {}
        kwargs['enforce_suggested'] = data.get('show_suggested_field') == 'true'
        return kwargs


# Inline formset: up to 10 variants per product, at least 1 required
VariantFormSet = inlineformset_factory(
    Product, ProductVariant,
    form=ProductVariantForm,
    formset=BaseVariantFormSet,
    extra=1,
    min_num=1,
    validate_min=True,
    can_delete=True,
)


class ProductReviewForm(forms.ModelForm):
    class Meta:
        from .models import ProductReview
        model = ProductReview
        fields = ['rating', 'comment']
        widgets = {
            'rating': forms.NumberInput(attrs={'min': 1, 'max': 5, 'class': 'form-control'}),
            'comment': forms.Textarea(attrs={'rows': 3, 'class': 'form-control', 'placeholder': 'Write your review here...'}),
        }


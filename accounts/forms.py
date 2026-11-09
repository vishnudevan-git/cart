from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.core.validators import RegexValidator
from .models import User


def _bootstrap(form):
    for field in form.fields.values():
        widget = field.widget
        if not isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
            widget.attrs.setdefault('class', 'form-control')


class CustomerRegisterForm(UserCreationForm):
    email = forms.EmailField(required=True)
    country_code = forms.ChoiceField(
        choices=[
            ('+91', '🇮🇳 IN +91'),
        ],
        initial='+91',
        label='Country Code',
        widget=forms.Select(attrs={
            'id': 'id_country_code',
            'class': 'form-select border-0 rounded-0 bg-light fw-semibold',
            'style': 'width:120px;flex-shrink:0;font-size:.9rem',
        })
    )
    phone = forms.CharField(max_length=15, required=False)
    # Pincode is NOT collected at signup — it is captured later in the delivery
    # address area (accounts/addresses + checkout). Keeps registration short.
    invite_code = forms.CharField(
        max_length=20, required=False,
        label='Distributor Code or Mobile No. (optional)',
        help_text='Enter the code or mobile number of your distributor to shop from their store.',
        validators=[RegexValidator(r'^[a-zA-Z0-9+\- ]{0,20}$', 'Invalid invite code format.')],
    )
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'country_code', 'phone', 'password1', 'password2', 'invite_code']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = 'Mobile number or Email'
        self.fields['username'].help_text = 'Enter your 10-digit mobile number or email address.'
        self.fields['username'].widget.attrs['placeholder'] = 'e.g. 9876543210 or you@example.com'
        _bootstrap(self)

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        from accounts.utils import is_valid_username
        if not is_valid_username(username):
            raise forms.ValidationError(
                'Enter a valid 10-digit mobile number (or 91/+91 prefix) or email address.'
            )
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("An account with this email/phone already exists. Please login instead.")
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_phone(self):
        phone = self.cleaned_data.get('phone', '').strip()
        if phone:
            from accounts.utils import normalize_phone
            import re
            phone = normalize_phone(phone)
            if not re.match(r'^[0-9]{10}$', phone):
                raise forms.ValidationError('Phone must be exactly 10 digits.')
            if User.objects.filter(phone=phone).exists():
                raise forms.ValidationError("An account with this phone number already exists.")
        return phone

    def clean_invite_code(self):
        code = self.cleaned_data.get('invite_code', '').strip()
        if code:
            from distributors.models import Distributor
            from django.db.models import Q
            exists = Distributor.objects.filter(
                Q(invite_code__iexact=code) | 
                Q(phone=code) | 
                Q(user__phone=code) | 
                Q(user__username=code),
                status='active'
            ).exists()
            if not exists:
                raise forms.ValidationError('No active distributor found with this code or mobile number.')
        return code

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = 'customer'
        user.email = self.cleaned_data['email']
        user.phone = self.cleaned_data.get('phone', '')
        # Pincode is collected later in the delivery address area, not at signup.
        # Auto-sync: if username is a phone number, ensure phone field is set (always 10 digits)
        username = self.cleaned_data.get('username', '')
        from accounts.utils import is_valid_phone, normalize_phone
        if is_valid_phone(username) and not user.phone:
            user.phone = normalize_phone(username)
        # Auto-sync: if username is an email, ensure email field matches
        if '@' in username and not user.email:
            user.email = username

        # Handle distributor code (creates customer role)
        if self.cleaned_data.get('invite_code', '').strip():
            invite_code = self.cleaned_data.get('invite_code', '').strip()
            from distributors.models import Distributor
            from django.db.models import Q
            distributor = Distributor.objects.filter(
                Q(invite_code__iexact=invite_code) | 
                Q(phone=invite_code) | 
                Q(user__phone=invite_code) | 
                Q(user__username=invite_code),
                status='active'
            ).first()
            if distributor:
                user.distributor = distributor

        if commit:
            user.save()
        return user

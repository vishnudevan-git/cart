from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
import os
from pathlib import Path
from .forms import CustomerRegisterForm
from .models import User, PasswordResetRequest

# Naming map: role 'distributor' = UI "Marketplace" (shop);
#              role 'wholesaler'  = UI "Distributor" (wholesale tier).
ROLE_LABELS = {
    'admin': ('Admin Panel', 'bi-speedometer2', 'danger'),
    'supplier': ('Supplier Portal', 'bi-shop', 'warning'),
    'stockist': ('Stockist Portal', 'bi-boxes', 'primary'),
    'wholesaler': ('Distributor Portal', 'bi-truck', 'info'),
    'distributor': ('Marketplace Portal', 'bi-shop-window', 'info'),
    'customer': ('Shop', 'bi-bag', 'success'),
}


def customer_only(view):
    """Single-role policy: personal shopping surfaces (profile, delivery
    addresses, rewards) belong to CUSTOMER accounts only.

    Admin/Supplier/Stockist/Distributor/Marketplace logins are business
    consoles — they are not shoppers and must not see "My Address" & co.
    """
    from functools import wraps

    @wraps(view)
    def _wrapped(request, *args, **kwargs):
        if request.user.primary_role() != 'customer':
            messages.info(request, 'That page is only for customer accounts.')
            return _redirect_by_active_role(request.user.primary_role())
        return view(request, *args, **kwargs)
    return _wrapped


def _create_password_reset_request(user):
    """Create one pending reset request per user, reused across all entry points."""
    if not user:
        return None
    pending = PasswordResetRequest.objects.filter(user=user, status='pending').first()
    if pending:
        return pending
    return PasswordResetRequest.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=24),
    )


def _demo_login_enabled():
    if bool(getattr(settings, 'DEBUG', False)):
        return True

    if bool(getattr(settings, 'ALLOW_DEMO_LOGIN', False)):
        return True

    raw = os.environ.get('ALLOW_DEMO_LOGIN', '')
    if str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}:
        return True

    # Fallback: read .env directly so dev toggles work even if server process
    # loaded settings before the env change.
    try:
        env_path = Path(settings.BASE_DIR) / '.env'
        if env_path.exists():
            for line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                if key.strip() == 'ALLOW_DEMO_LOGIN':
                    return val.strip().lower() in {'1', 'true', 'yes', 'on'}
    except Exception:
        pass

    return False


def _get_active_role(request, user):
    session_role = request.session.get('active_role')
    allowed = user.get_roles_list()
    if session_role and session_role in allowed:
        return session_role
    return user.primary_role()


def _redirect_by_active_role(role):
    if role == 'admin':
        return redirect('admin_panel:dashboard')
    elif role == 'supplier':
        return redirect('suppliers:dashboard')
    elif role == 'stockist':
        return redirect('stockist:dashboard')
    elif role == 'distributor':
        return redirect('distributors:dashboard')
    elif role == 'wholesaler':
        return redirect('wholesaler:dashboard')
    else:
        return redirect('products:home')


def _merge_and_redirect(request, user):
    active_role = _get_active_role(request, user)
    if active_role == 'customer':
        from cart.session_cart import merge_guest_cart_to_user
        merge_guest_cart_to_user(request, user)
    return _redirect_by_active_role(active_role)


def login_view(request):
    if request.user.is_authenticated:
        return _merge_and_redirect(request, request.user)
    
    # Handle "register" confirmation from prompt
    if request.GET.get('action') == 'register':
        identifier = request.session.get('new_identifier', '')
        if identifier:
            request.session['register_identifier'] = identifier
            request.session.pop('new_identifier', None)
            return redirect('accounts:register')
        return redirect('accounts:login')
    
    show_register_prompt = False
    prompt_identifier = ''
    
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        
        from accounts.utils import is_valid_login_identifier
        if not identifier or not is_valid_login_identifier(identifier):
            messages.error(request, 'Please enter a valid 10-digit mobile number or email ID.')
            return redirect('accounts:login')
        
        # Check if user exists by username, email, or phone
        user_exists = User.objects.filter(username__iexact=identifier).exists() or \
                      User.objects.filter(email__iexact=identifier).exists() or \
                      User.objects.filter(phone=identifier).exists()
                      
        if user_exists:
            request.session['login_identifier'] = identifier
            return redirect('accounts:login_password')
        else:
            request.session['new_identifier'] = identifier
            show_register_prompt = True
            prompt_identifier = identifier
            
    return render(request, 'accounts/login.html', {
        'show_register_prompt': show_register_prompt,
        'prompt_identifier': prompt_identifier,
    })


def _users_with_role(role):
    from suppliers.suspension import is_user_suspended_partner

    users = User.objects.filter(is_active=True, is_deleted=False).order_by('id')
    if role:
        users = [u for u in users if role in u.get_roles_list()]
    else:
        users = list(users)
    # Rule 1 (kb/08): a suspended partner (supplier / stockist / distributor /
    # marketplace) must not appear in the test-login list and must not be able
    # to log in. Drop them here so they never show.
    users = [u for u in users if not is_user_suspended_partner(u)]
    return users


def _serialize_user(user):
    data = {
        'id': user.id,
        'username': user.username,
        'first_name': user.first_name or '',
        'last_name': user.last_name or '',
        'phone': user.phone or '',
        'company_name': '',
    }
    # Include company name for business roles so the login page shows
    # the shop/supplier/stockist/wholesaler name instead of a raw first_name.
    try:
        role = user.primary_role()
        if role == 'distributor':
            data['company_name'] = user.distributor_profile.company_name
        elif role == 'supplier':
            data['company_name'] = user.supplier_profile.company_name
        elif role == 'stockist':
            data['company_name'] = user.stockist_profile.company_name
        elif role == 'wholesaler':
            data['company_name'] = user.wholesaler_profile.company_name
    except Exception:
        pass
    return data


def demo_login(request):
    """One-click demo login for runbook links like /accounts/demo-login/?role=supplier."""
    if not _demo_login_enabled():
        messages.error(request, 'Demo login is only available in development mode.')
        return redirect('accounts:login')

    role = (request.GET.get('role') or 'customer').strip().lower()
    next_url = request.GET.get('next', '').strip()

    users = _users_with_role(role)
    if not users:
        messages.error(request, f'No active users found for role: {role}.')
        return redirect('accounts:quick_test_login')

    user = users[0]
    login(request, user, backend='accounts.backends.CaseInsensitiveBackend')
    request.session['active_role'] = role

    if next_url:
        return redirect(next_url)
    return _redirect_by_active_role(role)


def quick_test_login(request):
    """Developer utility: select role/user and login without password (DEBUG only)."""
    if not _demo_login_enabled():
        messages.error(request, 'Quick test login is only available in development mode.')
        return redirect('accounts:login')

    if request.method == 'POST':
        role = (request.POST.get('role') or 'customer').strip().lower()
        username = (request.POST.get('username') or '').strip()

        if not username:
            messages.error(request, 'Please select or enter a username.')
            return redirect('accounts:quick_test_login')

        user = User.objects.filter(username__iexact=username, is_active=True, is_deleted=False).first()
        if not user:
            messages.error(request, f'User "{username}" not found.')
            return redirect('accounts:quick_test_login')

        if role not in user.get_roles_list():
            messages.error(request, f'User "{username}" does not have role "{role}".')
            return redirect('accounts:quick_test_login')

        # Rule 1 (kb/08): a suspended partner cannot log in — even via the dev
        # test-login, which bypasses the auth backend.
        from suppliers.suspension import is_user_suspended_partner
        if is_user_suspended_partner(user):
            messages.error(request, 'This account is suspended. Contact the admin.')
            return redirect('accounts:quick_test_login')

        login(request, user, backend='accounts.backends.CaseInsensitiveBackend')
        request.session['active_role'] = role
        return _redirect_by_active_role(role)

    from distributors.models import Distributor

    distributors_data = []
    distributors = Distributor.objects.select_related('user').filter(status='active').order_by('company_name')
    for dist in distributors:
        customers = User.objects.filter(
            distributor=dist,
            is_active=True,
            is_deleted=False,
        ).order_by('id')
        distributors_data.append({
            'distributor_id': dist.id,
            'distributor_name': dist.company_name,
            'customers': [_serialize_user(c) for c in customers],
        })

    roles = ['customer', 'supplier', 'distributor', 'wholesaler', 'stockist', 'admin']
    users_by_role = {role: [_serialize_user(u) for u in _users_with_role(role)] for role in roles}

    return render(request, 'accounts/quick_test_login.html', {
        'distributors_data': distributors_data,
        'users_by_role': users_by_role,
    })


def login_password(request):
    if request.user.is_authenticated:
        return _merge_and_redirect(request, request.user)
    
    identifier = request.session.get('login_identifier')
    if not identifier:
        return redirect('accounts:login')
        
    if request.method == 'POST':
        password = request.POST.get('password', '').strip()
        user = authenticate(request, username=identifier, password=password)
        if user:
            login(request, user)
            request.session.pop('login_identifier', None)
            # Single-role policy: every account has exactly one role, so there
            # is never a role to pick — go straight to that role's landing page.
            request.session['active_role'] = user.primary_role()
            # Activity log
            try:
                from lakrish_mart.activity_logger import act as _act
                _act.auth('login_success', username=user.get_username(),
                          role=user.primary_role(),
                          ip=request.META.get('REMOTE_ADDR', '?'))
            except Exception:
                pass
            return _merge_and_redirect(request, user)
        messages.error(request, 'Invalid password.')
        # Activity log failed login
        try:
            from lakrish_mart.activity_logger import act as _act
            _act.auth('login_failed', username=identifier,
                      ip=request.META.get('REMOTE_ADDR', '?'))
        except Exception:
            pass
        
    return render(request, 'accounts/login_password.html', {'identifier': identifier})


def register_prompt(request):
    if request.user.is_authenticated:
        return _merge_and_redirect(request, request.user)
        
    identifier = request.session.get('register_identifier')
    if not identifier:
        return redirect('accounts:login')
        
    if request.method == 'POST':
        return redirect('accounts:register')
        
    return render(request, 'accounts/register_prompt.html', {'identifier': identifier})


@login_required
def pick_role(request):
    """Single-role policy: there is nothing to pick.

    Kept as a compatibility shim so any old link/bookmark simply lands the user
    on their own role's home page instead of 404-ing.
    """
    request.session['active_role'] = request.user.primary_role()
    return _merge_and_redirect(request, request.user)


def logout_view(request):
    logout(request)
    request.session.pop('active_role', None)
    return redirect('accounts:login')


def forgot_password(request):
    """User submits mobile or email — creates a reset request for admin/distributor."""
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip()
        user = None
        if identifier:
            try:
                user = User.objects.get(phone=identifier)
            except User.DoesNotExist:
                pass
            if user is None:
                try:
                    user = User.objects.get(email__iexact=identifier)
                except (User.DoesNotExist, User.MultipleObjectsReturned):
                    user = User.objects.filter(email__iexact=identifier).first()

        if user:
            req = _create_password_reset_request(user)
            if req and not req.requested_ip:
                req.requested_ip = request.META.get('REMOTE_ADDR') or None
                req.save(update_fields=['requested_ip'])
        # Always show same message — don't reveal if account exists
        messages.success(
            request,
            'Your request has been submitted. Your admin or distributor will reset your password shortly.'
        )
        return redirect('accounts:login')
    return render(request, 'accounts/forgot_password.html')


@login_required
def change_password(request):
    """Centralized password change page for all authenticated roles."""
    if request.method == 'POST':
        current_password = request.POST.get('current_password', '').strip()
        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if not request.user.check_password(current_password):
            messages.error(request, 'Current password is incorrect.')
            return redirect('accounts:change_password')

        if len(new_password) < 6:
            messages.error(request, 'New password must be at least 6 characters.')
            return redirect('accounts:change_password')

        if new_password != confirm_password:
            messages.error(request, 'New password and confirm password do not match.')
            return redirect('accounts:change_password')

        if new_password == current_password:
            messages.error(request, 'New password must be different from current password.')
            return redirect('accounts:change_password')

        request.user.set_password(new_password)
        request.user.save(update_fields=['password'])
        update_session_auth_hash(request, request.user)
        messages.success(request, 'Password changed successfully.')
        return redirect('accounts:change_password')

    return render(request, 'accounts/change_password.html')


@login_required
@require_POST
def request_password_reset(request):
    """Centralized reset request endpoint for all authenticated roles."""
    _create_password_reset_request(request.user)
    messages.success(request, 'Password reset request submitted. Admin/distributor will resolve it shortly.')
    return redirect('accounts:change_password')


def register_customer(request):
    invite_code = request.GET.get('ref') or request.session.get('distributor_invite')
    initial_data = {'invite_code': invite_code}
    
    identifier = request.session.get('register_identifier')
    if identifier:
        initial_data['username'] = identifier
        if '@' in identifier:
            initial_data['email'] = identifier
        else:
            initial_data['phone'] = identifier

    form = CustomerRegisterForm(request.POST or None, initial=initial_data)

    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        code = form.cleaned_data.get('invite_code', '').strip()
        if code:
            from distributors.models import Distributor
            from django.db.models import Q
            try:
                # Allow finding distributor by invite_code, their registered phone, or user account phone/username
                dist = Distributor.objects.filter(
                    Q(invite_code__iexact=code) | 
                    Q(phone=code) | 
                    Q(user__phone=code) | 
                    Q(user__username=code),
                    status='active'
                ).first()
                if dist:
                    user.distributor = dist
                    request.session['distributor_id'] = dist.pk
            except Exception:
                pass
        elif request.session.get('distributor_id'):
            from distributors.models import Distributor
            try:
                user.distributor = Distributor.objects.get(pk=request.session['distributor_id'])
            except Distributor.DoesNotExist:
                pass
        user.roles = 'customer'
        user.save()
        login(request, user, backend='accounts.backends.CaseInsensitiveBackend')
        request.session['active_role'] = 'customer'
        request.session.pop('register_identifier', None)
        # Give welcome bonus if distributor has it enabled
        if user.distributor:
            try:
                from distributors.rewards import give_welcome_bonus
                give_welcome_bonus(user, user.distributor)
            except Exception:
                pass
        messages.success(request, 'Welcome to LaKrish Mart!')
        # Activity log: new customer registered
        try:
            from lakrish_mart.activity_logger import act as _act
            _act.user('registered', user=user,
                      detail=f'shop={user.distributor.company_name if user.distributor else "unlinked"}')
        except Exception:
            pass
        return _merge_and_redirect(request, user)

    # Diagnostic: when a registration POST is rejected, record WHICH fields
    # failed and why, so the request log explains the repeated re-renders.
    if request.method == 'POST':
        import logging as _logging
        _reg_log = _logging.getLogger('requests')
        try:
            errs = {f: [e for e in errlist] for f, errlist in form.errors.items()}
            _reg_log.info(f'    ↳ REGISTER FAILED | errors={errs}')
        except Exception:
            pass

    return render(request, 'accounts/register_customer.html', {'form': form})


@login_required
@customer_only
def profile(request):
    if request.method == 'POST':
        user = request.user
        user.first_name = request.POST.get('first_name', '').strip()
        user.last_name = request.POST.get('last_name', '').strip()
        user.email = request.POST.get('email', '').strip()
        user.phone = request.POST.get('phone', '').strip()
        user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('accounts:profile')
    return render(request, 'accounts/profile.html')


def redirect_by_role(user):
    if user.has_role('admin') or user.is_superuser:
        return redirect('admin_panel:dashboard')
    elif user.has_role('supplier'):
        return redirect('suppliers:dashboard')
    elif user.has_role('distributor'):
        return redirect('distributors:dashboard')
    else:
        return redirect('products:home')


@login_required
def save_preferences(request):
    """Save language and dark mode preference to user profile."""
    if request.method == 'POST':
        lang = request.POST.get('language', '').strip()
        dark = request.POST.get('dark_mode') == '1'
        user = request.user
        if lang:
            user.preferred_language = lang
            from django.utils.translation import activate
            activate(lang)
            request.session['_language'] = lang
        user.dark_mode = dark
        user.save(update_fields=['preferred_language', 'dark_mode'])
    return redirect(request.POST.get('next', '/'))


@login_required
def toggle_dark_mode(request):
    """Quick toggle dark mode via GET."""
    user = request.user
    user.dark_mode = not user.dark_mode
    user.save(update_fields=['dark_mode'])
    return redirect(request.META.get('HTTP_REFERER', '/'))


@login_required
@customer_only
def manage_addresses(request):
    from django.utils.http import url_has_allowed_host_and_scheme
    from .models import CustomerAddress

    def _safe_next_url(raw_next):
        target = (raw_next or '').strip()
        if target and url_has_allowed_host_and_scheme(target, {request.get_host()}):
            return target
        return ''

    addresses = CustomerAddress.objects.filter(user=request.user)
    next_url = _safe_next_url(request.GET.get('next') if request.method == 'GET' else request.POST.get('next'))
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        phone = request.POST.get('phone', '').strip()
        door_number = request.POST.get('door_number', '').strip()
        street_details = request.POST.get('street_details', '').strip()
        locality = request.POST.get('locality', '').strip()
        city = request.POST.get('city', '').strip()
        pincode = request.POST.get('pincode', '').strip()

        if not all([name, phone, door_number, street_details, locality, city, pincode]):
            messages.error(request, "Name, phone, door number, street, locality, city, and pincode are required.")
            return render(request, 'accounts/addresses.html', {'addresses': addresses, 'next_url': next_url})

        CustomerAddress.objects.create(
            user=request.user,
            label=request.POST.get('label', 'Home'),
            name=name,
            phone=phone,
            door_number=door_number,
            street_details=street_details,
            locality=locality,
            post_office=request.POST.get('post_office', '').strip(),
            taluk=request.POST.get('taluk', '').strip(),
            city=city,
            district=request.POST.get('district', '').strip(),
            state=request.POST.get('state', '').strip(),
            pincode=pincode,
            google_map_link=request.POST.get('google_map_link', '').strip(),
            is_default=True if not addresses.exists() else False
        )
        messages.success(request, "Address added successfully.")
        if next_url:
            return redirect(next_url)
        return redirect('accounts:addresses')
    return render(request, 'accounts/addresses.html', {'addresses': addresses, 'next_url': next_url})


@login_required
@customer_only
@require_POST
def delete_address(request, pk):
    from .models import CustomerAddress
    from django.utils.http import url_has_allowed_host_and_scheme

    next_url = (request.POST.get('next') or '').strip()
    get_object_or_404(CustomerAddress, pk=pk, user=request.user).delete()
    messages.success(request, "Address deleted.")
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        return redirect(next_url)
    return redirect('accounts:addresses')



def pincode_lookup(request, pincode):
    """Server-side proxy for India Post pincode API to avoid CORS/network issues."""
    import urllib.request as _ur
    import urllib.error as _ue
    import ssl as _ssl
    import json as _json
    import re as _re
    if not _re.match(r'^\d{6}$', pincode):
        return JsonResponse({'error': 'Invalid pincode'}, status=400)
    try:
        url = f'https://api.postalpincode.in/pincode/{pincode}'
        req = _ur.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        # Try verified SSL first; fall back to unverified if the CA chain is
        # incomplete on this host (common with the India Post API on Windows).
        ctx = _ssl.create_default_context()
        try:
            with _ur.urlopen(req, timeout=6, context=ctx) as resp:
                data = _json.loads(resp.read().decode())
        except (_ue.URLError, OSError):
            # SSL verification failed — retry without cert check for this known endpoint
            ctx = _ssl._create_unverified_context()
            with _ur.urlopen(req, timeout=6, context=ctx) as resp:
                data = _json.loads(resp.read().decode())
        return JsonResponse(data, safe=False)
    except Exception:
        return JsonResponse([{'Status': 'Error', 'PostOffice': None}], safe=False)


@login_required
@customer_only
def my_rewards(request):
    """Customer views their reward points balance and history."""
    from distributors.reward_models import RewardPoints, RewardTransaction
    distributor = getattr(request.user, 'distributor', None)
    reward_data = None
    transactions = []
    if distributor:
        try:
            rp = RewardPoints.objects.get(customer=request.user, distributor=distributor)
            transactions = rp.transactions.order_by('-created_at')[:20]
            reward_data = rp
        except RewardPoints.DoesNotExist:
            pass
    return render(request, 'accounts/my_rewards.html', {
        'reward_data': reward_data,
        'transactions': transactions,
        'distributor': distributor,
    })

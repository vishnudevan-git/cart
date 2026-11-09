from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.db.models import Q, Count
from django.utils import timezone
from decimal import Decimal

from products.models import Product, ProductVariant, Category
from accounts.models import User
from orders.models import Order, OrderItem
from distributors.models import Distributor
from suppliers.models import Supplier

from .models import (
    ProductTransaction, RoleTransaction, OrderTransaction, 
    OrderItemTransaction, VariantTransaction
)


# ============================================================================
# PRODUCT LIFECYCLE MANAGEMENT
# ============================================================================

def log_product_transaction(product, event, initiated_by, reason='', old_value=None, new_value=None):
    """Helper to log product transactions"""
    ProductTransaction.objects.create(
        product=product,
        event=event,
        initiated_by=initiated_by,
        reason=reason,
        old_value=old_value or {},
        new_value=new_value or {}
    )


@login_required
def product_lifecycle_dashboard(request):
    """Dashboard for product lifecycle management"""
    if not request.user.has_role('admin'):
        messages.error(request, 'Access denied')
        return redirect('products:home')
    
    products = Product.objects.all().select_related('supplier', 'category')
    transactions = ProductTransaction.objects.select_related('product', 'initiated_by')[:50]
    
    context = {
        'products': products,
        'transactions': transactions,
        'status_counts': {
            'pending': products.filter(status='pending').count(),
            'approved': products.filter(status='approved').count(),
            'rejected': products.filter(status='rejected').count(),
        }
    }
    return render(request, 'transactions/product_dashboard.html', context)


@login_required
@require_http_methods(["POST"])
def activate_product(request, product_id):
    """Activate a product"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        product = get_object_or_404(Product, pk=product_id)
        
        old_status = product.status
        if product.status == 'pending':
            product.status = 'approved'
            product.save()
            
            log_product_transaction(
                product=product,
                event='activated',
                initiated_by=request.user,
                reason=request.POST.get('reason', ''),
                old_value={'status': old_status},
                new_value={'status': 'approved'}
            )
            
            messages.success(request, f'Product "{product.name}" activated')
        else:
            messages.warning(request, 'Product is not in pending state')
        
        return redirect('transactions:product_detail', product_id=product.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:product_dashboard')


@login_required
@require_http_methods(["POST"])
def deactivate_product(request, product_id):
    """Deactivate a product"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        product = get_object_or_404(Product, pk=product_id)
        
        old_status = product.status
        product.status = 'rejected'
        product.save()
        
        log_product_transaction(
            product=product,
            event='deactivated',
            initiated_by=request.user,
            reason=request.POST.get('reason', ''),
            old_value={'status': old_status},
            new_value={'status': 'rejected'}
        )
        
        messages.success(request, f'Product "{product.name}" deactivated')
        return redirect('transactions:product_detail', product_id=product.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:product_dashboard')


@login_required
@require_http_methods(["POST"])
def delete_product(request, product_id):
    """Soft delete a product"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        product = get_object_or_404(Product, pk=product_id)
        
        old_status = product.status
        product.soft_delete()
        log_product_transaction(
            product=product,
            event='deleted',
            initiated_by=request.user,
            reason=request.POST.get('reason', ''),
            old_value={'is_deleted': False, 'status': old_status},
            new_value={'is_deleted': True, 'status': 'rejected', 'deleted_at': str(product.deleted_at)}
        )
        
        messages.success(request, f'Product "{product.name}" deleted')
        return redirect('transactions:product_dashboard')
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:product_dashboard')


@login_required
@require_http_methods(["POST"])
def restore_product(request, product_id):
    """Restore a deleted product"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        product = get_object_or_404(Product, pk=product_id)
        
        product.restore()
        
        log_product_transaction(
            product=product,
            event='restored',
            initiated_by=request.user,
            reason=request.POST.get('reason', ''),
            old_value={'is_deleted': True},
            new_value={'is_deleted': False, 'status': product.status}
        )
        
        messages.success(request, f'Product "{product.name}" restored')
        return redirect('transactions:product_detail', product_id=product.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:product_dashboard')


@login_required
def product_detail(request, product_id):
    """View product details and transaction history"""
    if not request.user.has_role('admin'):
        messages.error(request, 'Access denied')
        return redirect('products:home')
    
    product = get_object_or_404(Product, pk=product_id)
    transactions = ProductTransaction.objects.filter(product=product)
    variants = product.variants.all()
    
    context = {
        'product': product,
        'transactions': transactions,
        'variants': variants,
    }
    return render(request, 'transactions/product_detail.html', context)


# ============================================================================
# ROLE LIFECYCLE MANAGEMENT
# ============================================================================

def log_role_transaction(user, event, initiated_by, role_affected='', old_value='', new_value='', reason=''):
    """Helper to log role transactions"""
    RoleTransaction.objects.create(
        user=user,
        event=event,
        role_affected=role_affected,
        initiated_by=initiated_by,
        old_value=old_value,
        new_value=new_value,
        reason=reason
    )


@login_required
def role_management_dashboard(request):
    """Dashboard for user role management"""
    if not request.user.has_role('admin'):
        messages.error(request, 'Access denied')
        return redirect('products:home')
    
    users = User.objects.all().prefetch_related('role_transactions')
    transactions = RoleTransaction.objects.select_related('user', 'initiated_by')[:50]
    
    role_stats = {
        'admin': User.objects.filter(roles__contains='admin').count(),
        'supplier': User.objects.filter(roles__contains='supplier').count(),
        'distributor': User.objects.filter(roles__contains='distributor').count(),
        'stockist': User.objects.filter(roles__contains='stockist').count(),
        'customer': User.objects.filter(roles__contains='customer').count(),
    }
    
    context = {
        'users': users,
        'transactions': transactions,
        'role_stats': role_stats,
    }
    return render(request, 'transactions/role_management.html', context)


@login_required
@require_http_methods(["POST"])
def add_role(request, user_id):
    """Set the user's single role (single-role policy: this REPLACES the old one)."""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        user = get_object_or_404(User, pk=user_id)
        role = request.POST.get('role', '').strip()
        
        if role not in User.role_values():
            return JsonResponse({'error': 'Invalid role'}, status=400)
        
        old_roles = ','.join(user.get_roles_list())
        # A role change must also provision the profile that the destination
        # dashboard requires. Keep role + profile creation atomic so an account
        # can never be left as (for example) a Marketplace with no shop profile.
        with transaction.atomic():
            user.add_role(role)
            user.save()
            if role == 'supplier':
                Supplier.objects.get_or_create(
                    user=user,
                    defaults={'company_name': f'{user.username} Supplier', 'status': 'approved'},
                )
            elif role == 'distributor':
                Distributor.objects.get_or_create(
                    user=user,
                    defaults={'company_name': f'{user.username} Marketplace', 'status': 'active'},
                )
            elif role == 'wholesaler':
                from distributors.wholesaler_models import Wholesaler
                Wholesaler.objects.get_or_create(
                    user=user,
                    defaults={'company_name': f'{user.username} Distributor', 'status': 'active'},
                )
        new_roles = ','.join(user.get_roles_list())
        
        log_role_transaction(
            user=user,
            event='role_added',
            initiated_by=request.user,
            role_affected=role,
            old_value=old_roles,
            new_value=new_roles,
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'{user.username} is now a "{role}" (previous role: {old_roles or "—"})')
        return redirect('transactions:user_detail', user_id=user.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:role_management')


@login_required
@require_http_methods(["POST"])
def remove_role(request, user_id):
    """Disabled under the single-role policy.

    An account always holds exactly one role, so a role can be CHANGED (see
    ``add_role``) but never removed — removing it would leave the account with
    no role at all.
    """
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)

    messages.error(
        request,
        'Roles cannot be removed — every account must hold exactly one role. '
        'Use "Change Role" instead.'
    )
    return redirect('transactions:user_detail', user_id=user_id)


@login_required
@require_http_methods(["POST"])
def suspend_user(request, user_id):
    """Suspend a user account"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        user = get_object_or_404(User, pk=user_id)
        
        user.is_active = False
        user.save()
        
        log_role_transaction(
            user=user,
            event='suspended',
            initiated_by=request.user,
            old_value='active',
            new_value='suspended',
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'User {user.username} has been suspended')
        return redirect('transactions:user_detail', user_id=user.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:role_management')


@login_required
@require_http_methods(["POST"])
def activate_user(request, user_id):
    """Activate a suspended user"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        user = get_object_or_404(User, pk=user_id)
        
        user.is_active = True
        user.save()
        
        log_role_transaction(
            user=user,
            event='activated',
            initiated_by=request.user,
            old_value='suspended',
            new_value='active',
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'User {user.username} has been activated')
        return redirect('transactions:user_detail', user_id=user.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:role_management')


@login_required
@require_http_methods(["POST"])
def delete_user(request, user_id):
    """Mark user as deleted"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        user = get_object_or_404(User, pk=user_id)
        
        user.soft_delete(deleted_by=request.user.username)
        
        log_role_transaction(
            user=user,
            event='deleted',
            initiated_by=request.user,
            old_value='active',
            new_value='deleted',
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'User {user.username} has been deleted')
        return redirect('transactions:role_management')
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:role_management')


@login_required
def user_detail(request, user_id):
    """View user details and role transaction history"""
    if not request.user.has_role('admin'):
        messages.error(request, 'Access denied')
        return redirect('products:home')
    
    user = get_object_or_404(User, pk=user_id)
    transactions = RoleTransaction.objects.filter(user=user)
    
    context = {
        'user': user,
        'transactions': transactions,
        'roles': user.get_roles_list(),
    }
    return render(request, 'transactions/user_detail.html', context)


# ============================================================================
# ORDER LIFECYCLE MANAGEMENT
# ============================================================================

def log_order_transaction(order, event, initiated_by, old_status='', new_status='', details=None, reason=''):
    """Helper to log order transactions"""
    OrderTransaction.objects.create(
        order=order,
        event=event,
        old_status=old_status,
        new_status=new_status,
        details=details or {},
        initiated_by=initiated_by,
        reason=reason
    )


@login_required
def order_lifecycle_dashboard(request):
    """Dashboard for order lifecycle management"""
    if not request.user.has_role('admin'):
        messages.error(request, 'Access denied')
        return redirect('products:home')
    
    orders = Order.objects.all().select_related('customer', 'distributor')
    transactions = OrderTransaction.objects.select_related('order', 'initiated_by')[:50]
    
    status_counts = {
        'pending': orders.filter(status='pending').count(),
        'confirmed': orders.filter(status='confirmed').count(),
        'stockist_picked': orders.filter(status='stockist_picked').count(),
        'at_distributor': orders.filter(status='at_distributor').count(),
        'out_for_delivery': orders.filter(status='out_for_delivery').count(),
        'delivered': orders.filter(status='delivered').count(),
        'cancelled': orders.filter(status='cancelled').count(),
    }
    
    context = {
        'orders': orders,
        'transactions': transactions,
        'status_counts': status_counts,
    }
    return render(request, 'transactions/order_dashboard.html', context)


@login_required
@require_http_methods(["POST"])
def confirm_order(request, order_id):
    """Confirm a pending order"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        order = get_object_or_404(Order, pk=order_id)
        old_status = order.status
        
        order.status = 'confirmed'
        order.save()
        
        log_order_transaction(
            order=order,
            event='confirmed',
            initiated_by=request.user,
            old_status=old_status,
            new_status='confirmed',
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'Order #{order.id} confirmed')
        return redirect('transactions:order_detail', order_id=order.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:order_dashboard')


@login_required
@require_http_methods(["POST"])
def update_order_status(request, order_id):
    """Update order status"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        order = get_object_or_404(Order, pk=order_id)
        new_status = request.POST.get('status', '').strip()
        
        valid_statuses = [status[0] for status in Order._meta.get_field('status').choices]
        if new_status not in valid_statuses:
            return JsonResponse({'error': 'Invalid status'}, status=400)
        
        old_status = order.status
        order.status = new_status
        order.save()
        
        log_order_transaction(
            order=order,
            event=new_status,
            initiated_by=request.user,
            old_status=old_status,
            new_status=new_status,
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'Order #{order.id} status updated to {new_status}')
        return redirect('transactions:order_detail', order_id=order.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:order_dashboard')


@login_required
@require_http_methods(["POST"])
def cancel_order(request, order_id):
    """Cancel an order"""
    if not request.user.has_role('admin'):
        return JsonResponse({'error': 'Access denied'}, status=403)
    
    try:
        order = get_object_or_404(Order, pk=order_id)
        
        if order.status == 'delivered':
            return JsonResponse({'error': 'Cannot cancel delivered order'}, status=400)
        
        old_status = order.status
        order.status = 'cancelled'
        order.save()
        
        log_order_transaction(
            order=order,
            event='cancelled',
            initiated_by=request.user,
            old_status=old_status,
            new_status='cancelled',
            reason=request.POST.get('reason', '')
        )
        
        messages.success(request, f'Order #{order.id} cancelled')
        return redirect('transactions:order_detail', order_id=order.id)
    except Exception as e:
        messages.error(request, f'Error: {str(e)}')
        return redirect('transactions:order_dashboard')


@login_required
def order_detail(request, order_id):
    """View order details and transaction history"""
    if not request.user.has_role('admin'):
        messages.error(request, 'Access denied')
        return redirect('products:home')
    
    order = get_object_or_404(Order, pk=order_id)
    transactions = OrderTransaction.objects.filter(order=order)
    items = order.items.all()
    item_transactions = OrderItemTransaction.objects.filter(item__order=order)
    
    context = {
        'order': order,
        'transactions': transactions,
        'items': items,
        'item_transactions': item_transactions,
    }
    return render(request, 'transactions/order_detail.html', context)

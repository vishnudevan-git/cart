from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Payment, RoleSettlement, sync_order_ledger


ACTIVE_SETTLEMENT_STATUSES = ('pending', 'submitted', 'disputed')
HISTORY_SETTLEMENT_STATUSES = ('confirmed', 'cancelled')


def _payment_filter_values(request):
	return {
		'search': request.GET.get('q', '').strip(),
		'payment_status_filter': request.GET.get('payment_status', '').strip(),
		'settlement_status_filter': request.GET.get('settlement_status', '').strip(),
		'stage_filter': request.GET.get('stage', '').strip(),
	}


def _filter_customer_payments(queryset, search='', payment_status_filter=''):
	if search:
		search_filter = (
			Q(order__customer__username__icontains=search) |
			Q(order__customer__first_name__icontains=search) |
			Q(order__customer__last_name__icontains=search) |
			Q(order__distributor__company_name__icontains=search) |
			Q(transaction_reference__icontains=search) |
			Q(received_note__icontains=search)
		)
		if search.isdigit():
			search_filter |= Q(order_id=int(search))
		queryset = queryset.filter(search_filter)
	if payment_status_filter:
		queryset = queryset.filter(status=payment_status_filter)
	return queryset


def _filter_settlements(queryset, search='', settlement_status_filter='', stage_filter=''):
	if search:
		search_filter = (
			Q(distributor__company_name__icontains=search) |
			Q(stockist__company_name__icontains=search) |
			Q(supplier__company_name__icontains=search) |
			Q(order__customer__username__icontains=search) |
			Q(order__customer__first_name__icontains=search) |
			Q(order__customer__last_name__icontains=search) |
			Q(transfer_reference__icontains=search) |
			Q(transfer_note__icontains=search) |
			Q(query_note__icontains=search)
		)
		if search.isdigit():
			search_filter |= Q(order_id=int(search))
		queryset = queryset.filter(search_filter)
	if settlement_status_filter:
		queryset = queryset.filter(status=settlement_status_filter)
	if stage_filter:
		queryset = queryset.filter(stage=stage_filter)
	return queryset


def _active_role(request):
	roles = request.user.get_roles_list()
	role = request.session.get('active_role', request.user.primary_role())
	return role if role in roles else request.user.primary_role()


def _role_sidebar_template(role):
	return {
		'supplier': 'suppliers/partials/sidebar.html',
		'distributor': 'distributors/partials/sidebar.html',
		'stockist': 'stockist/partials/sidebar.html',
		'admin': 'admin_panel/partials/sidebar.html',
	}.get(role)


def _user_profiles(request, role):
	return {
		'distributor': getattr(request.user, 'distributor_profile', None) if role == 'distributor' else None,
		'stockist': getattr(request.user, 'stockist_profile', None) if role == 'stockist' else None,
		'supplier': getattr(request.user, 'supplier_profile', None) if role == 'supplier' else None,
	}


@login_required
def center(request):
	role = _active_role(request)
	profiles = _user_profiles(request, role)
	filters = _payment_filter_values(request)

	if request.method == 'POST':
		action = request.POST.get('action', '')

		if action == 'update_customer_payment' and role in ('distributor', 'admin'):
			payment = get_object_or_404(Payment.objects.select_related('order__distributor'), pk=request.POST.get('payment_id'))
			distributor = profiles['distributor']
			if role != 'admin' and payment.order.distributor_id != getattr(distributor, 'id', None):
				messages.error(request, 'You cannot update this customer payment.')
				return redirect('payments:center')

			new_status = request.POST.get('status', payment.status)
			if new_status not in dict(Payment.STATUS_CHOICES):
				new_status = payment.status
			payment.status = new_status
			payment.transaction_reference = request.POST.get('transaction_reference', '').strip()
			payment.received_note = request.POST.get('received_note', '').strip()
			payment.paid_at = timezone.now() if new_status == 'paid' else None
			payment.save(update_fields=['status', 'transaction_reference', 'received_note', 'paid_at'])
			sync_order_ledger(payment.order)
			messages.success(request, f'Customer payment for order #{payment.order_id} updated.')
			return redirect('payments:center')

		if action in ('submit_settlement', 'confirm_settlement', 'raise_query'):
			settlement = get_object_or_404(
				RoleSettlement.objects.select_related('order', 'distributor', 'stockist', 'supplier'),
				pk=request.POST.get('settlement_id')
			)

			payer_role = settlement.payer_role()
			payee_role = settlement.payee_role()

			if action == 'submit_settlement':
				if role not in (payer_role, 'admin'):
					messages.error(request, 'Only the sender can submit settlement details.')
					return redirect('payments:center')
				try:
					amount_paid = Decimal(request.POST.get('amount_paid', settlement.amount_due))
				except InvalidOperation:
					messages.error(request, 'Enter a valid settlement amount.')
					return redirect('payments:center')
				if amount_paid <= 0 or amount_paid > settlement.amount_due:
					messages.error(request, 'Settlement amount must be between 0 and the amount due.')
					return redirect('payments:center')
				settlement.amount_paid = amount_paid
				settlement.transfer_reference = request.POST.get('transfer_reference', '').strip()
				settlement.transfer_note = request.POST.get('transfer_note', '').strip()
				settlement.query_note = ''
				settlement.status = 'submitted'
				settlement.submitted_at = timezone.now()
				settlement.submitted_by = request.user
				settlement.confirmed_at = None
				settlement.confirmed_by = None
				settlement.disputed_by = None
				settlement.save()
				messages.success(request, f'Settlement for order #{settlement.order_id} submitted for confirmation.')
				return redirect('payments:center')

			if action == 'confirm_settlement':
				if role not in (payee_role, 'admin'):
					messages.error(request, 'Only the receiver can confirm this settlement.')
					return redirect('payments:center')
				settlement.status = 'confirmed'
				settlement.confirmed_at = timezone.now()
				settlement.confirmed_by = request.user
				if settlement.amount_paid <= 0:
					settlement.amount_paid = settlement.amount_due
				settlement.save(update_fields=['status', 'confirmed_at', 'confirmed_by', 'amount_paid'])
				messages.success(request, f'Settlement for order #{settlement.order_id} confirmed.')
				return redirect('payments:center')

			if role not in (payer_role, payee_role, 'admin'):
				messages.error(request, 'You cannot raise a query on this settlement.')
				return redirect('payments:center')
			settlement.status = 'disputed'
			settlement.query_note = request.POST.get('query_note', '').strip()
			settlement.disputed_by = request.user
			settlement.save(update_fields=['status', 'query_note', 'disputed_by'])
			messages.warning(request, f'Query raised for order #{settlement.order_id}.')
			return redirect('payments:center')

	context = {
		'active_role': role,
		'sidebar_template': _role_sidebar_template(role),
		'customer_payments': Payment.objects.none(),
		'incoming_settlements': RoleSettlement.objects.none(),
		'outgoing_settlements': RoleSettlement.objects.none(),
		'global_settlements': RoleSettlement.objects.none(),
		'payment_status_choices': Payment.STATUS_CHOICES,
		'settlement_status_choices': RoleSettlement.STATUS_CHOICES,
		'settlement_stage_choices': RoleSettlement.STAGE_CHOICES,
		**filters,
	}

	if role == 'distributor' and profiles['distributor']:
		dist = profiles['distributor']
		context.update({
			'customer_payments': _filter_customer_payments(
				Payment.objects.filter(order__distributor=dist).select_related('order__customer').order_by('-created_at'),
				search=filters['search'],
				payment_status_filter=filters['payment_status_filter'],
			),
			'outgoing_settlements': _filter_settlements(RoleSettlement.objects.filter(
				distributor=dist, stage='distributor_to_stockist', status__in=ACTIVE_SETTLEMENT_STATUSES
			).select_related('order__customer', 'stockist').order_by('-updated_at'),
				search=filters['search'],
				settlement_status_filter=filters['settlement_status_filter'],
				stage_filter=filters['stage_filter'],
			),
		})
	elif role == 'stockist' and profiles['stockist']:
		stockist = profiles['stockist']
		context.update({
			'incoming_settlements': _filter_settlements(RoleSettlement.objects.filter(
				stockist=stockist, stage='distributor_to_stockist', status__in=ACTIVE_SETTLEMENT_STATUSES
			).select_related('order__customer', 'distributor').order_by('-updated_at'),
				search=filters['search'],
				settlement_status_filter=filters['settlement_status_filter'],
				stage_filter=filters['stage_filter'],
			),
			'outgoing_settlements': _filter_settlements(RoleSettlement.objects.filter(
				stockist=stockist, stage='stockist_to_supplier', status__in=ACTIVE_SETTLEMENT_STATUSES
			).select_related('order', 'supplier').order_by('-updated_at'),
				search=filters['search'],
				settlement_status_filter=filters['settlement_status_filter'],
				stage_filter=filters['stage_filter'],
			),
		})
	elif role == 'supplier' and profiles['supplier']:
		supplier = profiles['supplier']
		context.update({
			'incoming_settlements': _filter_settlements(RoleSettlement.objects.filter(
				supplier=supplier, stage='stockist_to_supplier', status__in=ACTIVE_SETTLEMENT_STATUSES
			).select_related('order', 'stockist').order_by('-updated_at'),
				search=filters['search'],
				settlement_status_filter=filters['settlement_status_filter'],
				stage_filter=filters['stage_filter'],
			),
		})
	elif role == 'admin':
		customer_payments = _filter_customer_payments(
			Payment.objects.select_related('order__customer', 'order__distributor').order_by('-created_at'),
			search=filters['search'],
			payment_status_filter=filters['payment_status_filter'],
		)
		global_settlements = _filter_settlements(
			RoleSettlement.objects.select_related(
				'order', 'distributor', 'stockist', 'supplier'
			).filter(status__in=ACTIVE_SETTLEMENT_STATUSES).order_by('-updated_at'),
			search=filters['search'],
			settlement_status_filter=filters['settlement_status_filter'],
			stage_filter=filters['stage_filter'],
		)
		context.update({
			'customer_payments': customer_payments[:100],
			'global_settlements': global_settlements[:200],
		})

	settlement_scope = RoleSettlement.objects.all()
	if role == 'distributor' and profiles['distributor']:
		settlement_scope = settlement_scope.filter(
			distributor=profiles['distributor'],
			stage='distributor_to_stockist'
		)
	elif role == 'stockist' and profiles['stockist']:
		settlement_scope = settlement_scope.filter(stockist=profiles['stockist'])
	elif role == 'supplier' and profiles['supplier']:
		settlement_scope = settlement_scope.filter(
			supplier=profiles['supplier'],
			stage='stockist_to_supplier'
		)

	active_scope = settlement_scope.filter(status__in=ACTIVE_SETTLEMENT_STATUSES)
	context['settlement_summary'] = active_scope.values('status').annotate(count=Count('id')).order_by('status')
	context['pending_count'] = active_scope.count()

	# Completed settlements — the old separate "Settlement History" page,
	# merged in here (user decision 2026-07-28: one payments page, not two).
	context['completed_settlements'] = _filter_settlements(
		settlement_scope.filter(status__in=HISTORY_SETTLEMENT_STATUSES).select_related(
			'order', 'distributor', 'stockist', 'supplier'
		).order_by('-updated_at'),
		search=filters['search'],
		settlement_status_filter=filters['settlement_status_filter'],
		stage_filter=filters['stage_filter'],
	)[:100]
	return render(request, 'payments/center.html', context)

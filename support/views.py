from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from accounts.decorators import role_required
from .models import Ticket


support_required = role_required('supplier', 'distributor')
admin_required = role_required('admin', set_active_role='admin', allow_superuser=True)


# ── Supplier / Distributor views ──────────────────────────────────────────────

@support_required
def submit_ticket(request):
    """Supplier or distributor submits a support ticket."""
    user = request.user
    # Determine active role: prefer session, fall back to user.role
    active_role = request.session.get('active_role', user.role)
    if active_role not in ('supplier', 'distributor'):
        roles = user.get_roles_list()
        active_role = 'supplier' if 'supplier' in roles else 'distributor'

    if request.method == 'POST':
        ticket_type = request.POST.get('ticket_type', '').strip()
        subject = request.POST.get('subject', '').strip()
        description = request.POST.get('description', '').strip()
        image = request.FILES.get('image')

        if not ticket_type or not subject or not description:
            messages.error(request, 'Please fill in all required fields.')
        else:
            ticket = Ticket(
                submitted_by=user,
                role=active_role,
                ticket_type=ticket_type,
                subject=subject,
                description=description,
            )
            if image:
                ticket.image = image
            ticket.save()
            messages.success(request, 'Your ticket has been submitted. We will get back to you soon.')
            return redirect('support:my_tickets')

    return render(request, 'support/submit_ticket.html', {
        'ticket_type_choices': Ticket.TICKET_TYPE_CHOICES,
        'active_role': active_role,
    })


@support_required
def my_tickets(request):
    """List all tickets submitted by the current user."""
    tickets = Ticket.objects.filter(submitted_by=request.user)
    return render(request, 'support/my_tickets.html', {
        'tickets': tickets,
        'active_role': request.session.get('active_role', request.user.role),
    })


@support_required
def ticket_detail(request, pk):
    """View a single ticket and admin response."""
    ticket = get_object_or_404(Ticket, pk=pk, submitted_by=request.user)
    return render(request, 'support/ticket_detail.html', {
        'ticket': ticket,
        'active_role': request.session.get('active_role', request.user.role),
    })


# ── Admin views ───────────────────────────────────────────────────────────────

@admin_required
def admin_ticket_list(request):
    """Admin sees all tickets with optional filters."""
    tickets = Ticket.objects.select_related('submitted_by').all()

    status_filter = request.GET.get('status', '')
    type_filter = request.GET.get('ticket_type', '')
    role_filter = request.GET.get('role', '')

    if status_filter:
        tickets = tickets.filter(status=status_filter)
    if type_filter:
        tickets = tickets.filter(ticket_type=type_filter)
    if role_filter:
        tickets = tickets.filter(role=role_filter)

    return render(request, 'admin_panel/support_tickets.html', {
        'tickets': tickets,
        'status_filter': status_filter,
        'type_filter': type_filter,
        'role_filter': role_filter,
        'status_choices': Ticket.STATUS_CHOICES,
        'type_choices': Ticket.TICKET_TYPE_CHOICES,
        'role_choices': Ticket.ROLE_CHOICES,
        'pending_count': Ticket.objects.filter(status='open').count(),
    })


@admin_required
def admin_ticket_respond(request, pk):
    """Admin sets status and writes a response."""
    ticket = get_object_or_404(Ticket, pk=pk)

    if request.method == 'POST':
        ticket.status = request.POST.get('status', ticket.status)
        ticket.priority = request.POST.get('priority', ticket.priority)
        ticket.admin_response = request.POST.get('admin_response', '').strip()
        ticket.save()
        messages.success(request, f'Ticket #{ticket.pk} updated.')
        return redirect('admin_panel:support_tickets')

    return render(request, 'admin_panel/support_respond.html', {
        'ticket': ticket,
        'status_choices': Ticket.STATUS_CHOICES,
        'priority_choices': Ticket.PRIORITY_CHOICES,
    })

from django.contrib import admin
from .models import Ticket


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('id', 'subject', 'submitted_by', 'role', 'ticket_type', 'status', 'priority', 'created_at')
    list_filter = ('status', 'ticket_type', 'role', 'priority')
    search_fields = ('subject', 'submitted_by__username', 'description')
    readonly_fields = ('submitted_by', 'role', 'created_at', 'updated_at')

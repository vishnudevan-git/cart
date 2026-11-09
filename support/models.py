from django.db import models
from django.conf import settings


class Ticket(models.Model):
    TICKET_TYPE_CHOICES = (
        ('bug', 'Bug Report'),
        ('suggestion', 'Suggestion'),
        ('other', 'Other'),
    )
    STATUS_CHOICES = (
        ('open', 'Open'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('closed', 'Closed'),
    )
    PRIORITY_CHOICES = (
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
    )
    ROLE_CHOICES = (
        ('supplier', 'Supplier'),
        ('distributor', 'Distributor'),
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='support_tickets',
    )
    role = models.CharField(max_length=15, choices=ROLE_CHOICES)
    ticket_type = models.CharField(max_length=15, choices=TICKET_TYPE_CHOICES)
    subject = models.CharField(max_length=200)
    description = models.TextField()
    image = models.ImageField(upload_to='support/', blank=True, null=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='open')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    admin_response = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'#{self.pk} — {self.subject} ({self.submitted_by.username})'

    def status_badge_class(self):
        return {
            'open': 'bg-primary',
            'in_progress': 'bg-warning text-dark',
            'resolved': 'bg-success',
            'closed': 'bg-secondary',
        }.get(self.status, 'bg-secondary')

    def priority_badge_class(self):
        return {
            'low': 'bg-info text-dark',
            'medium': 'bg-warning text-dark',
            'high': 'bg-danger',
        }.get(self.priority, 'bg-secondary')

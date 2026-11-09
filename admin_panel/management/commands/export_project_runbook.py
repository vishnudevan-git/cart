from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone

from admin_panel.models import SiteSettings
from admin_panel.runbook import collect_runbook_data


class Command(BaseCommand):
    help = 'Export live runbook into project_runbook.html in repository root.'

    def handle(self, *args, **options):
        site = SiteSettings.get()
        context = collect_runbook_data()
        context.update({
            'generated_at': timezone.now(),
            'demo_mode_enabled': site.demo_mode_enabled,
            'demo_allow_quick_login': site.demo_allow_quick_login,
            'demo_block_transactions': site.demo_block_transactions,
        })

        html = render_to_string('project/runbook.html', context)
        output_path = Path(settings.BASE_DIR) / 'project_runbook.html'
        output_path.write_text(html, encoding='utf-8')

        self.stdout.write(self.style.SUCCESS(f'Runbook exported to {output_path}'))

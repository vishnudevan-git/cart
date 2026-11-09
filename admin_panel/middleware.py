from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from admin_panel.models import SiteSettings


class DemoTransactionGuardMiddleware:
    """Blocks write actions that create real transactions in demo sessions."""

    BLOCKED_PREFIXES = (
        '/orders/',
        '/payments/',
        '/transactions/',
    )

    ALLOWLIST = {
        '/orders/my-orders/',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_block(request):
            msg = 'Demo mode is active. Real transaction actions are disabled for this session.'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': msg}, status=403)
            messages.warning(request, msg)
            return redirect(request.META.get('HTTP_REFERER', '/runbook/'))
        return self.get_response(request)

    def _should_block(self, request):
        if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return False
        if not request.session.get('demo_session'):
            return False

        site = SiteSettings.get()
        if not site.demo_mode_enabled or not site.demo_block_transactions:
            return False

        path = request.path or '/'
        if path in self.ALLOWLIST:
            return False
        return path.startswith(self.BLOCKED_PREFIXES)


import logging
import time

_request_logger = logging.getLogger('requests')


class RequestLoggingMiddleware:
    """Writes one line per HTTP request to a per-run request log.

    The file is created fresh on each server start and named with the start
    timestamp (logs/requests_<stamp>.log) so no single file grows forever.

    Each line captures: timestamp, who (username + role, or 'guest'),
    method, path (with query string), response status, duration in ms, and
    client IP. Noise like static files and favicon is skipped. Sensitive form
    values are NEVER logged — only the path and status, so passwords and other
    posted secrets never land in the file.
    """

    # Prefixes we don't care about (static assets, health noise).
    SKIP_PREFIXES = ('/static/', '/media/', '/favicon.ico')

    def __init__(self, get_response):
        self.get_response = get_response
        # Write a one-time START banner at the top of each run's log file.
        try:
            from datetime import datetime
            from django.conf import settings
            stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            logfile = getattr(settings, 'REQUEST_LOG_FILE', '(unknown)')
            _request_logger.info('=' * 78)
            _request_logger.info(f'SERVER START  {stamp}   ->  {logfile}')
            _request_logger.info(
                'columns: time | who(role) | method | status | duration | ip | url')
            _request_logger.info('=' * 78)
        except Exception:
            pass

    def __call__(self, request):
        path = request.path or '/'
        if path.startswith(self.SKIP_PREFIXES):
            return self.get_response(request)

        start = time.monotonic()
        response = self.get_response(request)
        duration_ms = (time.monotonic() - start) * 1000.0

        try:
            self._log(request, response, duration_ms)
        except Exception:
            # Logging must never break a request.
            pass
        return response

    def _client_ip(self, request):
        fwd = request.META.get('HTTP_X_FORWARDED_FOR')
        if fwd:
            return fwd.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '-')

    def _who(self, request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            role = request.session.get('active_role') or getattr(user, 'role', '?')
            return f'{user.get_username()}({role})'
        return 'guest'

    def _log(self, request, response, duration_ms):
        from datetime import datetime

        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        path = request.path or '/'
        qs = request.META.get('QUERY_STRING', '')
        if qs:
            path = f'{path}?{qs}'

        line = (
            f'{ts} | {self._who(request):<22} | {request.method:<6} | '
            f'{response.status_code} | {duration_ms:6.1f}ms | '
            f'{self._client_ip(request):<15} | {path}'
        )
        _request_logger.info(line)

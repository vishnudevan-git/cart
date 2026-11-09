"""
Project-wide smoke test: walk every GET URL as every role, assert no 500s.

This is the cheapest broad safety net in the project. It does not check that a
page is *correct* — only that it renders at all for each role, which catches the
whole class of "renamed a field / removed a context variable / changed a manager
and forgot one template" breakages that no targeted test would notice.

Detail URLs are visited with real primary keys where we have them and a
placeholder otherwise, so a 404 is an expected, acceptable outcome. Only 5xx
fails the build.
"""
import logging
import re

from django.test import TestCase
from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

from accounts.factories import build_world, login_as


# Routes deliberately not crawled.
SKIP_PREFIXES = ('/django-admin',)
SKIP_SUBSTRINGS = ('logout',)          # would end the session mid-crawl

ARG_PATTERN = re.compile(r'<(?:(?P<converter>[^:>]+):)?(?P<name>[^>]+)>')


def _collect_patterns(resolver=None, prefix=''):
    """Every registered URL pattern, flattened to a path string."""
    resolver = resolver or get_resolver()
    paths = []
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            paths += _collect_patterns(pattern, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            paths.append(prefix + str(pattern.pattern))
    return paths


class AllUrlsSmokeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.world = build_world()

    def setUp(self):
        # Expected 403/404 responses log a traceback each — thousands of lines of
        # noise that would bury the one line that matters when this test fails.
        logging.getLogger('django.request').setLevel(logging.CRITICAL)
        self.addCleanup(logging.getLogger('django.request').setLevel, logging.NOTSET)

    def _sample_args(self):
        """Real primary keys so detail pages actually render their templates."""
        world = self.world
        product = world.shared_product
        return {
            'pk': product.pk, 'id': product.pk,
            'product_pk': product.pk, 'product_id': product.pk,
            'variant_pk': world.shared_variant.pk, 'variant_id': world.shared_variant.pk,
            'category_pk': world.category.pk, 'category_id': world.category.pk,
            'slug': world.category.slug,
            'user_id': world.customer_a.pk, 'customer_pk': world.customer_a.pk,
            'distributor_pk': world.shop_a.pk, 'distributor_id': world.shop_a.pk,
            'supplier_pk': world.supplier.pk, 'supplier_id': world.supplier.pk,
            'wholesaler_pk': world.wholesaler.pk,
            'stockist_pk': world.stockist.pk,
            'pincode': '600001', 'token': 'x' * 16,
        }

    def _concrete_urls(self):
        samples = self._sample_args()

        def fill(match):
            name = match.group('name')
            if name in samples:
                return str(samples[name])
            return '1' if match.group('converter') == 'int' else 'x'

        urls = set()
        for pattern in _collect_patterns():
            if pattern.startswith('^'):
                continue                        # regex route — can't synthesise
            url = '/' + ARG_PATTERN.sub(fill, pattern)
            if '<' in url or '(' in url:
                continue                        # unresolved converter
            if url.startswith(SKIP_PREFIXES) or any(s in url for s in SKIP_SUBSTRINGS):
                continue
            urls.add(url)
        return sorted(urls)

    def _users_by_role(self):
        world = self.world
        return {
            'guest': None,
            'admin': world.admin,
            'supplier': world.supplier_user,
            'stockist': world.stockist_user,
            'wholesaler': world.wholesaler_user,
            'distributor': world.shop_a_user,
            'customer': world.customer_a,
        }

    def test_no_server_errors_for_any_role(self):
        urls = self._concrete_urls()
        self.assertGreater(len(urls), 100, 'URL collection looks broken')

        failures = []
        for role, user in self._users_by_role().items():
            client = self.client_class()
            if user is not None:
                login_as(client, user)

            for url in urls:
                try:
                    response = client.get(url, follow=True)
                except Exception as exc:                    # noqa: BLE001
                    failures.append(f'{role:12} {url}  raised {type(exc).__name__}: {exc}')
                    continue
                if response.status_code >= 500:
                    failures.append(f'{role:12} {url}  -> HTTP {response.status_code}')

        self.assertEqual(
            failures, [],
            '\n'.join([f'{len(failures)} server error(s):', *failures[:40]]),
        )
